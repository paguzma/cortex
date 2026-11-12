import json
from datetime import datetime

from cortex import config, paths
from cortex.agentos import collector, pulses
from cortex.routines import report
from cortex.routines.base import Result


def _state():
    return json.loads((paths.state_dir() / "agentos" / "state.json").read_text())


def test_collector_on_an_empty_seeded_vault(ready, monkeypatch):
    monkeypatch.setattr(collector, "MCP_URL", "http://127.0.0.1:9/mcp")
    state = collector.collect()
    out = paths.state_dir() / "agentos"
    assert {p.name for p in out.glob("*.json")} == {"state.json", "notes.json", "logs.json"}
    assert _state() == json.loads(json.dumps(state))
    assert set(state) == {"generated", "instance", "status", "routines", "logs", "pending", "vault",
                          "mcp", "clients", "graph", "pulses"}
    failed = [k for k, v in state.items() if isinstance(v, dict) and v.get("ok") is False]
    assert failed == []
    g = state["graph"]
    assert g["domains"] == ["general"] and list(g["areas"]) == ["general"]
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"core", "skill", "routine", "mcp", "client"} <= kinds
    assert g["layers"]["memory"] == 0 and g["layers"]["core"] >= 4
    assert {n["id"] for n in g["nodes"] if n["layer"] == "core"} >= {"AGENTS", "SCHEMA", "INDEX", "BOOTSTRAP"}
    assert {n["id"] for n in g["nodes"] if n["kind"] == "routine"} == {"routine:consolidate", "routine:clean", "routine:snapshot"}
    assert any(n["plugin"] == "cortex" for n in g["nodes"] if n["kind"] == "skill")
    assert state["mcp"]["status"] == "down"
    assert state["status"]["core"] == {"state": "idle", "who": []}
    assert state["status"]["routines_total"] == 3 and state["status"]["routines_ok"] == 3
    assert state["status"]["vault_commit"]
    assert state["pending"]["items"] == [] and state["pending"]["domain_proposals"] == []
    assert state["vault"]["notes"] == 0 and state["vault"]["commits"] >= 1
    assert state["pulses"]["events"] == []
    notes = json.loads((out / "notes.json").read_text())
    assert "SCHEMA.md" in notes and not any(k.startswith(("_archive", "_templates", ".git")) for k in notes)
    logs = json.loads((out / "logs.json").read_text())
    assert set(logs) == {"consolidate", "clean", "snapshot"}


def test_collector_with_thirty_notes_in_three_domains(seeded_vault):
    state = collector.collect()
    g = state["graph"]
    assert g["domains"] == ["home", "lab", "work", "general"]
    notes = [n for n in g["nodes"] if n["layer"] in ("memory", "rules")]
    assert len(notes) == 30
    by_area = {}
    for n in notes:
        by_area.setdefault(n["area"], 0)
        by_area[n["area"]] += 1
    assert by_area == {"home": 10, "lab": 10, "work": 10}      # el voto de los vecinos las reparte
    assert state["vault"]["notes"] == 30
    assert state["pending"]["counts"]["vault"] == 3
    assert all(i["path"].startswith("entities/projects/") for i in state["pending"]["items"])


def test_routine_reports_feed_status_pending_and_runs(ready):
    cfg = config.load()
    report.write_report("clean", Result("fail", "it broke", "trace"), cfg)
    report.write_report("snapshot", Result("ok", "committed", ""), cfg)
    state = collector.collect()
    assert state["status"]["routines_ok"] == 2
    assert [i["kind"] for i in state["pending"]["items"]] == ["routine"]
    items = {i["name"]: i for i in state["routines"]["items"]}
    assert items["clean"]["ok"] is False and items["snapshot"]["last_status"] == "ok"
    assert items["consolidate"]["state"] == "no-credential"
    assert {r["routine"] for r in state["graph"]["runs"]} == {"clean", "snapshot"}
    # la corrida ok que acaba de terminar pulsa hacia lo que la rutina toca
    runs = [e for e in state["pulses"]["events"] if e["type"] == "run"]
    assert runs and all(e["origin"] == "routine:snapshot" for e in runs)


def test_status_says_which_routine_is_running(ready):
    import os
    from cortex.routines import runner
    runner.running_file().write_text(json.dumps({"routine": "consolidate", "pid": os.getpid(),
                                                 "since": datetime.now().astimezone().isoformat()}))
    state = collector.collect()
    assert state["status"]["core"] == {"state": "working", "who": ["consolidate"]}
    assert state["routines"]["running"] == "consolidate"


def test_clients_from_config_and_access_log(ready):
    cfg = config.load()
    cfg["clients"] = {"10.0.0.2": "laptop"}
    config.save(cfg)
    now = datetime.now().timestamp()
    lines = [{"ts": now - 5, "request": {"remote_ip": "10.0.0.2", "method": "POST", "uri": "/mcp"}, "status": 200},
             {"ts": now - 4, "request": {"remote_ip": "192.0.2.9", "method": "POST", "uri": "/mcp"}, "status": 200}]
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    collector.access_log().write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    state = collector.collect()
    items = {c["id"]: c for c in state["clients"]["items"]}
    assert items["client:10.0.0.2"]["label"] == "laptop" and items["client:10.0.0.2"]["last_seen"]
    assert items[pulses.CLIENT_UNKNOWN]["unnamed_ips"] == ["192.0.2.9"]
    ids = {n["id"] for n in state["graph"]["nodes"]}
    assert {"client:10.0.0.2", pulses.CLIENT_UNKNOWN} <= ids
    # dos POST sin lectura/escritura que los explique: dos consultas, una por cliente
    origins = sorted(e["origin"] for e in state["pulses"]["events"] if e["type"] == "query")
    assert origins == ["client:10.0.0.2", pulses.CLIENT_UNKNOWN]


def test_pulses_from_basic_memory_log(seeded_vault):
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S") + ".123"
    log = collector.bm_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"{stamp} | INFO     | basic_memory.mcp.tools.read_note:read_note:233 - Attempting to read "
                   f"note from Project: cortex identifier: entities/projects/home-hub\n"
                   f"{stamp} | INFO     | basic_memory.mcp.tools.write_note:write_note:233 - MCP tool call "
                   f"tool=write_note project=cortex directory=references, title=lab note 1, tags=None\n")
    state = collector.collect()
    events = [(e["type"], e["origin"], e["target"]) for e in state["pulses"]["events"]]
    assert ("read", "unknown", "home-hub") in events
    assert ("write", "unknown", "lab-note-1") in events


def test_mcp_status_any_http_answer_is_up():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(406)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert collector.mcp_status(f"http://127.0.0.1:{srv.server_address[1]}/mcp") == "up"
    finally:
        srv.shutdown()
    assert collector.mcp_status("http://127.0.0.1:9/mcp", timeout=1) == "down"


def test_routine_with_invalid_schedule_is_not_shown_ready(ready):
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    config.save(cfg)
    state = collector.collect()
    items = {i["name"]: i for i in state["routines"]["items"]}
    assert items["snapshot"]["state"] == "invalid-schedule"
    assert items["snapshot"]["healthy"] is False
    assert state["status"]["routines_ok"] == 2
