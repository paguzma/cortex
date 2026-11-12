import json
from datetime import datetime, timedelta, timezone

from cortex.agentos import pulses

TZ = pulses.LOCAL_TZ
SINCE = datetime(2026, 10, 5, 23, 30, 0, tzinfo=TZ)


def _epoch(h, m, s):
    return datetime(2026, 10, 5, h, m, s, tzinfo=TZ).timestamp()


# Formato real de basic-memory 0.23 (verificado en el contenedor)
BM = [
    "2026-10-05 23:29:59.000 | INFO     | basic_memory.mcp.tools.read_note:read_note:233 - Attempting to read note from Project: cortex identifier: entities/infra/server",
    "2026-10-05 23:34:59.100 | INFO     | basic_memory.mcp.tools.read_note:read_note:233 - Attempting to read note from Project: cortex identifier: entities/infra/server",
    "2026-10-05 23:35:03.199 | INFO     | basic_memory.mcp.tools.edit_note:edit_note:578 - MCP tool call tool=edit_note project=cortex identifier=entities/infra/firewall operation=insert_before_section output_format=text",
    "2026-10-05 23:35:10.000 | INFO     | basic_memory.mcp.tools.write_note:write_note:233 - MCP tool call tool=write_note project=cortex directory=episodes/2026, title=2026-10-05 New note, tags=None",
    "2026-10-05 23:35:11.000 | INFO     | basic_memory.repository.semantic_vector_sync:sync:552 - Vector batch sync complete",
]


def caddy(ts, ip, method="POST", uri="/mcp", status=200):
    return json.dumps({"level": "info", "ts": ts, "logger": "http.log.access.log0", "msg": "handled request",
                       "request": {"remote_ip": ip, "client_ip": ip, "method": method, "uri": uri,
                                   "headers": {"Authorization": ["REDACTED"]}},
                       "status": status, "size": 10})


CLIENTS = {"10.0.0.2": "laptop", "10.0.0.3": "server"}
ACCESS = [
    caddy(_epoch(23, 34, 58), "10.0.0.2"),
    caddy(_epoch(23, 35, 3), "10.0.0.3"),
    caddy(_epoch(23, 35, 40), "10.0.0.2"),
    caddy(_epoch(23, 35, 41), "10.0.0.2", method="GET"),
    caddy(_epoch(23, 29, 0), "10.0.0.9"),
    caddy(_epoch(23, 35, 20), "127.0.0.1", status=401),
]
IDS = {"server", "firewall", "2026-10-05--new-note"}
TITLES = {"2026-10-05 New note": "2026-10-05--new-note"}


def test_cortex_events_filters_window_and_type():
    ev = pulses.cortex_events(BM, SINCE)
    assert [(e["type"], e["ref"]) for e in ev] == [
        ("read", "entities/infra/server"),
        ("write", "entities/infra/firewall"),
        ("write", "2026-10-05 New note"),
    ]
    assert ev[0]["ts"].tzinfo is not None


def test_build_context_and_read_content_are_reads():
    lines = [
        "2026-10-05 23:35:20.000 | INFO     | basic_memory.mcp.tools.build_context:build_context:305 - MCP tool call tool=build_context project=cortex url=memory://probe-note depth=1 timeframe=7d output_format=json",
        "2026-10-05 23:35:21.000 | INFO     | basic_memory.mcp.tools.read_content:read_content:241 - MCP tool call tool=read_content project=None path=references/probe-note.md",
    ]
    ev = pulses.cortex_events(lines, SINCE)
    assert [(e["type"], e["ref"], e["detail"]) for e in ev] == [
        ("read", "probe-note", "build_context"), ("read", "references/probe-note.md", "read_content")]
    assert pulses.resolve_target(ev[1]["ref"], {"probe-note"}, {}) == "probe-note"


def test_parse_caddy_json_line():
    rec = pulses.parse_access(ACCESS[0])
    assert rec["ip"] == "10.0.0.2" and rec["method"] == "POST" and rec["uri"] == "/mcp" and rec["status"] == 200
    assert rec["ts"] == datetime(2026, 10, 5, 23, 34, 58, tzinfo=TZ)
    assert pulses.parse_access("not json") is None
    assert pulses.parse_access('{"msg": "no ts"}') is None


def test_parse_access_prefers_client_ip_behind_a_proxy():
    # Detrás de tailscale serve, remote_ip es el gateway de Docker; Caddy saca client_ip de X-Forwarded-For.
    line = json.dumps({"ts": _epoch(23, 34, 58), "status": 200,
                       "request": {"remote_ip": "172.18.0.1", "client_ip": "100.64.0.7",
                                   "method": "POST", "uri": "/mcp"}})
    assert pulses.parse_access(line)["ip"] == "100.64.0.7"


def test_mcp_posts_only_successful_posts_in_window_with_client():
    ps = pulses.mcp_posts(ACCESS, SINCE, CLIENTS)
    assert [p["origin"] for p in ps] == ["client:10.0.0.2", "client:10.0.0.3", "client:10.0.0.2"]


def test_unknown_ip_is_the_generic_client():
    ps = pulses.mcp_posts([caddy(_epoch(23, 36, 0), "192.0.2.7")], SINCE, CLIENTS)
    assert [p["origin"] for p in ps] == [pulses.CLIENT_UNKNOWN]


def test_mcp_posts_ignores_non_2xx_and_other_paths():
    assert pulses.mcp_posts([ACCESS[5]], SINCE, CLIENTS) == []
    assert pulses.mcp_posts([caddy(_epoch(23, 36, 0), "10.0.0.2", uri="/setup")], SINCE, CLIENTS) == []


def test_last_seen_per_ip():
    seen = pulses.last_seen(ACCESS)
    assert seen["10.0.0.2"] == datetime(2026, 10, 5, 23, 35, 41, tzinfo=TZ)
    assert "127.0.0.1" not in seen  # su única petición fue un 401


def test_last_seen_ignores_unauthenticated_requests():
    # Un 401 con el client_ip de un cliente nombrado no lo marca como visto.
    seen = pulses.last_seen([caddy(_epoch(23, 40, 0), "10.0.0.3", status=401),
                             caddy(_epoch(23, 41, 0), "10.0.0.2", method="GET", status=403)])
    assert seen == {}


def test_cross_assigns_origin_and_leftovers_are_queries():
    ev = pulses.cross(pulses.cortex_events(BM, SINCE), pulses.mcp_posts(ACCESS, SINCE, CLIENTS))
    assert [(e["type"], e["origin"]) for e in sorted(ev, key=lambda e: e["ts"])] == [
        ("read", "client:10.0.0.2"),      # 23:34:59 <-> post 23:34:58
        ("write", "client:10.0.0.3"),     # 23:35:03 <-> post 23:35:03
        ("write", "unknown"),             # 23:35:10: ningún post a ±2 s
        ("query", "client:10.0.0.2"),     # post 23:35:40 sobrante
    ]


def test_burst_of_posts_is_one_query():
    def post(sec, origin):
        return {"ts": datetime(2026, 10, 5, 23, 36, sec, tzinfo=TZ), "origin": origin}
    posts = [post(s, "client:a") for s in (0, 0, 1, 2, 4)] + [post(1, "client:b")]
    ev = sorted(pulses.cross([], posts), key=lambda e: e["origin"])
    assert [(e["type"], e["origin"], e["detail"]) for e in ev] == [
        ("query", "client:a", "query or connection (5)"),
        ("query", "client:b", "query or connection (1)"),
    ]


def test_resolve_target():
    assert pulses.resolve_target("entities/infra/server", IDS, TITLES) == "server"
    assert pulses.resolve_target("server", IDS, TITLES) == "server"
    assert pulses.resolve_target("2026-10-05 New note", IDS, TITLES) == "2026-10-05--new-note"
    assert pulses.resolve_target("2026-10-05 new NOTE", IDS, TITLES) == "2026-10-05--new-note"
    assert pulses.resolve_target("no/such/note", IDS, TITLES) == "mcp:cortex"
    assert pulses.resolve_target("cortex", IDS, TITLES) == "mcp:cortex"


def test_build_serializes_resolves_and_trims():
    ev = pulses.cross(pulses.cortex_events(BM, SINCE), pulses.mcp_posts(ACCESS, SINCE, CLIENTS))
    out = pulses.build(ev, IDS, TITLES, maximum=3)
    assert len(out) == 3
    assert out[-1]["type"] == "query"
    assert out[0]["target"] == "firewall"
    assert set(out[0]) == {"ts", "type", "origin", "target", "detail"}
    assert datetime.fromisoformat(out[0]["ts"]).tzinfo is not None


def test_read_tail_returns_whole_lines(tmp_path):
    p = tmp_path / "log"
    p.write_text("first long line\nsecond\nthird\n")
    assert pulses.read_tail(p, n_bytes=14) == ["second", "third"]
    assert pulses.read_tail(tmp_path / "missing") == []
