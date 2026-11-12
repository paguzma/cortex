import http.client
import json
import os
import threading
from datetime import datetime

import pytest

from cortex.agentos import collector, server


@pytest.fixture
def running(ready, monkeypatch):
    monkeypatch.setattr(collector, "MCP_URL", "http://127.0.0.1:9/mcp")
    srv = server.make_server(0)
    srv.live.collect_once()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.live.stop.set()
    srv.shutdown()
    srv.server_close()


def get(srv, path):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", path)
    r = conn.getresponse()
    return r, r.read()


def test_serves_the_panel_with_relative_urls(running):
    r, body = get(running, "/")
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/html")
    html = body.decode()
    assert 'src="app.js' in html and 'src="/' not in html
    r, body = get(running, "/app.js")
    assert r.status == 200 and b"data/state.json" in body and b"new EventSource('events')" in body
    assert b"'/data" not in body and b"'/events" not in body


def test_serves_state_json(running):
    r, body = get(running, "/data/state.json?t=123")
    assert r.status == 200 and r.getheader("Cache-Control") == "no-store"
    state = json.loads(body)
    assert state["graph"]["domains"] == ["general"]
    for name in ("notes.json", "logs.json"):
        assert get(running, f"/data/{name}")[0].status == 200


def test_unknown_paths_are_404(running):
    for path in ("/data/../config/config.json", "/data/other.json", "/nope", "/web/app.js"):
        assert get(running, path)[0].status == 404


def test_state_before_the_first_collection_is_404(ready):
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert get(srv, "/data/state.json")[0].status == 404
    finally:
        srv.shutdown()
        srv.server_close()


def _read_events(srv, n):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", "/events")
    r = conn.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    out, buf = [], b""
    while len(out) < n:
        buf += r.fp.readline()
        if buf.endswith(b"\n\n"):
            for line in buf.decode().splitlines():
                if line.startswith("data: "):
                    out.append(json.loads(line[6:]))
            buf = b""
    conn.close()
    return out


def test_events_stream_snapshot_and_broadcast(running):
    from cortex.routines import runner
    runner.running_file().write_text(json.dumps({"routine": "snapshot", "pid": os.getpid(), "since": "x"}))
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("ev", _read_events(running, 2)))
    t.start()
    for _ in range(100):
        if running.live.broadcaster._clients:
            break
        threading.Event().wait(0.02)
    running.live.broadcaster.emit({"ts": "2026-10-08T10:00:00+00:00", "type": "read", "origin": "unknown",
                                   "target": "SCHEMA", "detail": "read_note"})
    t.join(5)
    first, second = result["ev"]
    assert first == {"type": "routines_running", "routines": ["routine:snapshot"]}
    assert second["type"] == "read" and second["target"] == "SCHEMA"


def test_live_publish_resolves_targets(running):
    q = running.live.broadcaster.subscribe()
    running.live.publish({"ts": datetime.now().astimezone(), "type": "write", "origin": "client:unknown",
                          "ref": "references/no-such-note.md", "detail": "write_note"})
    running.live.publish({"ts": datetime.now().astimezone(), "type": "read", "origin": "unknown",
                          "ref": "SCHEMA", "detail": "read_note"})
    got = [json.loads(q.get(timeout=1)) for _ in range(2)]
    assert [e["target"] for e in got] == ["mcp:cortex", "SCHEMA"]


class TestRoutineWatcher:
    def test_first_poll_only_learns(self):
        w = server.RoutineWatcher()
        assert w.poll(None, {"clean": {"ts": "t1", "status": "ok"}}) == []

    def test_start_and_end_with_report(self):
        w = server.RoutineWatcher()
        w.poll(None, {"clean": None})
        ev = w.poll({"routine": "clean", "since": "2026-10-08T05:00:00+00:00"}, None)
        assert ev == [{"type": "routine", "routine": "routine:clean", "state": "running",
                       "ts": "2026-10-08T05:00:00+00:00"}]
        ev = w.poll(None, {"clean": {"ts": "2026-10-08T05:01:00+00:00", "status": "fail"}})
        assert [(e["routine"], e["state"]) for e in ev] == [("routine:clean", "failed")]

    def test_fast_run_seen_only_by_its_report(self):
        w = server.RoutineWatcher()
        w.poll(None, {"snapshot": {"ts": "t1", "status": "ok"}})
        ev = w.poll(None, {"snapshot": {"ts": "t2", "status": "ok"}})
        assert [(e["state"], e["ts"], e["ok"]) for e in ev] == [("finished", "t2", True)]
        assert w.poll(None, None) == []

    def test_end_without_report_is_finished(self):
        w = server.RoutineWatcher()
        w.poll({"routine": "consolidate", "since": "s"}, {"consolidate": None})
        ev = w.poll(None, None)
        assert [(e["routine"], e["state"]) for e in ev] == [("routine:consolidate", "finished")]


def test_poll_routines_emits_run_pulses(running):
    from cortex import config
    from cortex.routines import report
    from cortex.routines.base import Result
    live = running.live
    live.poll_routines()                             # aprende el estado inicial
    q = live.broadcaster.subscribe()
    report.write_report("snapshot", Result("ok", "done", ""), config.load())
    live._reports_mtime = None                       # por si el mtime del directorio no cambió
    live.poll_routines()
    got = []
    while not q.empty():
        got.append(json.loads(q.get_nowait()))
    assert got[0]["type"] == "routine" and got[0]["state"] == "finished"
    runs = [e for e in got if e["type"] == "run"]
    assert runs and {e["origin"] for e in runs} == {"routine:snapshot"}


def test_main_reads_the_port_from_env(monkeypatch):
    captured = {}

    class Fake:
        def __init__(self):
            self.live = server.Live()

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setenv("CORTEX_AGENTOS_PORT", "18999")
    monkeypatch.setattr(server, "make_server", lambda port: captured.setdefault("port", port) and Fake())
    monkeypatch.setattr(server.Live, "start", lambda self, collect=True: captured.setdefault("collect", collect))
    assert server.main() == 0
    assert captured == {"port": 18999, "collect": True}


def test_write_wakes_collector_early(monkeypatch):
    import threading
    import time
    from cortex.agentos import server
    live = server.Live()
    calls = []
    monkeypatch.setattr(live, "collect_once", lambda: calls.append(time.monotonic()))
    t = threading.Thread(target=live.collect_loop, kwargs={"every": 60, "debounce": 0.2}, daemon=True)
    t.start()
    time.sleep(0.1)
    assert len(calls) == 1
    live.wake.set()
    time.sleep(0.6)
    live.stop.set()
    live.wake.set()
    t.join(2)
    assert len(calls) == 2
