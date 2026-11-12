"""Eventos reales para los pulsos del panel.

De dos logs que ya existen, sin guardar estado entre corridas:
  - basic-memory.log: lecturas (read_note, build_context, read_content) y escrituras
  - access.log del proxy (Caddy, JSON por línea): cada POST /mcp con la IP de origen
Se cruzan por hora para saber QUÉ cliente tocó cada nota. Los POST que no explican
una lectura/escritura (búsquedas, otras tools, conexiones) quedan como «query» a cortex.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

MCP_NODE = "mcp:cortex"
CLIENT_UNKNOWN = "client:unknown"
UNKNOWN = "unknown"
# build_context y read_content se anotan igual que las escrituras («MCP tool call») pero LEEN
READ_TOOLS = ("build_context", "read_content")
WRITE_TOOLS = ("write_note", "edit_note", "move_note", "delete_note") + READ_TOOLS
LOCAL_TZ = datetime.now().astimezone().tzinfo

RX_READ = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ \|.*read_note:\d+ - "
                     r"Attempting to read note from Project: \S+ identifier: (\S+)")
RX_WRITE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ \|.*MCP tool call tool=("
                      + "|".join(WRITE_TOOLS) + r") (.*)$")
RX_REF = (re.compile(r"identifier=(\S+)"), re.compile(r"url=memory://(\S+)"),
          re.compile(r"path=(\S+)"), re.compile(r"title=([^,]+)"))


def read_tail(path: Path, n_bytes: int = 524288) -> list[str]:
    """Las últimas líneas completas de un archivo (la primera puede venir cortada)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n_bytes))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = raw.splitlines()
    if size > n_bytes and lines:
        lines = lines[1:]
    return [line for line in lines if line]


def _ts_bm(text: str) -> datetime:
    # basic-memory escribe la hora local del contenedor, sin huso
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)


def cortex_events(lines: list[str], since: datetime) -> list[dict]:
    """Líneas de basic-memory.log -> [{ts, type: read|write, ref, detail}]."""
    out = []
    for line in lines:
        m = RX_READ.match(line)
        if m:
            ts = _ts_bm(m.group(1))
            if ts >= since:
                out.append({"ts": ts, "type": "read", "ref": m.group(2), "detail": "read_note"})
            continue
        m = RX_WRITE.match(line)
        if not m:
            continue
        ts = _ts_bm(m.group(1))
        if ts < since:
            continue
        rest, ref = m.group(3), None
        for rx in RX_REF:
            found = rx.search(rest)
            if found and found.group(1).strip() not in ("None", ""):
                ref = found.group(1).strip()
                break
        out.append({"ts": ts, "type": "read" if m.group(2) in READ_TOOLS else "write",
                    "ref": ref or "cortex", "detail": m.group(2)})
    return out


def client_id(ip: str, clients: dict) -> str:
    """IP -> id del nodo cliente. Una IP que nadie nombró en la UI es el cliente genérico."""
    return f"client:{ip}" if ip in clients else CLIENT_UNKNOWN


def parse_access(line: str) -> dict | None:
    """Una línea JSON del access.log de Caddy -> {ts, ip, method, uri, status}, o None."""
    try:
        d = json.loads(line)
        req = d.get("request") or {}
        return {"ts": datetime.fromtimestamp(float(d["ts"]), tz=timezone.utc).astimezone(LOCAL_TZ),
                "ip": str(req.get("client_ip") or req.get("remote_ip") or ""),
                "method": str(req.get("method") or ""), "uri": str(req.get("uri") or ""),
                "status": int(d.get("status") or 0)}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
        return None


def is_mcp_post(rec: dict) -> bool:
    uri = rec["uri"].split("?", 1)[0]
    return rec["method"] == "POST" and (uri == "/mcp" or uri.startswith("/mcp/")) \
        and 200 <= rec["status"] < 300


def mcp_posts(lines: list[str], since: datetime, clients: dict) -> list[dict]:
    """POST /mcp exitosos en la ventana -> [{ts, origin}] (origin = id del nodo cliente)."""
    out = []
    for line in lines:
        rec = parse_access(line)
        if rec is None or not is_mcp_post(rec) or rec["ts"] < since:
            continue
        out.append({"ts": rec["ts"], "origin": client_id(rec["ip"], clients)})
    return out


def last_seen(lines: list[str]) -> dict[str, datetime]:
    """IP -> última petición a /mcp que tuvo éxito (cualquier método, solo 2xx).

    Sin token la respuesta es 401: una petición así no marca a nadie como activo ni
    añade IPs inventadas a unnamed_ips."""
    seen: dict[str, datetime] = {}
    for line in lines:
        rec = parse_access(line)
        if rec is None or not rec["ip"] or not rec["uri"].startswith("/mcp") \
                or not 200 <= rec["status"] < 300:
            continue
        if rec["ip"] not in seen or seen[rec["ip"]] < rec["ts"]:
            seen[rec["ip"]] = rec["ts"]
    return seen


def _group_queries(posts: list[dict], gap_s: float = 3.0) -> list[dict]:
    """POST sobrantes: los consecutivos del mismo origen (cadena < gap_s) son UNA consulta."""
    groups: list[dict] = []
    open_: dict[str, dict] = {}
    for p in sorted(posts, key=lambda p: p["ts"]):
        g = open_.get(p["origin"])
        if g is not None and (p["ts"] - g["last"]).total_seconds() < gap_s:
            g["n"] += 1
            g["last"] = p["ts"]
        else:
            g = {"ts": p["ts"], "last": p["ts"], "origin": p["origin"], "n": 1}
            open_[p["origin"]] = g
            groups.append(g)
    return [{"ts": g["ts"], "type": "query", "ref": "cortex",
             "detail": f"query or connection ({g['n']})", "origin": g["origin"]} for g in groups]


def cross(events: list[dict], posts: list[dict], tolerance_s: float = 2.0) -> list[dict]:
    """Asigna a cada evento de cortex el POST más cercano (±tolerance_s); sobrantes = query."""
    free = sorted(posts, key=lambda p: p["ts"])
    out = []
    for e in sorted(events, key=lambda e: e["ts"]):
        best = min(free, key=lambda p: abs((p["ts"] - e["ts"]).total_seconds()), default=None)
        if best is not None and abs((best["ts"] - e["ts"]).total_seconds()) <= tolerance_s:
            free.remove(best)
            out.append({**e, "origin": best["origin"]})
        else:
            out.append({**e, "origin": UNKNOWN})
    out += _group_queries(free)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_target(ref: str, ids: set[str], by_title: dict[str, str]) -> str:
    """Referencia de basic-memory (permalink, ruta, título) -> id de nodo, o el MCP."""
    if ref in by_title:
        return by_title[ref]
    stem = ref.rstrip("/").split("/")[-1].removesuffix(".md")
    if stem in ids:
        return stem
    lowered = {k.lower(): v for k, v in by_title.items()}
    if ref.lower() in lowered:
        return lowered[ref.lower()]
    slug = _slug(stem)
    for node_id in ids:
        if _slug(node_id) == slug:
            return node_id
    return MCP_NODE


def serialize(e: dict, target: str) -> dict:
    return {"ts": e["ts"].isoformat(timespec="seconds"), "type": e["type"], "origin": e["origin"],
            "target": target, "detail": e.get("detail", "")}


def build(events: list[dict], ids: set[str], by_title: dict[str, str], maximum: int = 300) -> list[dict]:
    out = [serialize(e, e.get("target") or resolve_target(e["ref"], ids, by_title))
           for e in sorted(events, key=lambda e: e["ts"])]
    return out[-maximum:]


class Correlator:
    """Cruza en vivo los eventos de cortex con los POST del proxy para saber su origen.

    Las dos líneas de un mismo hecho llegan por archivos distintos y en cualquier orden,
    así que cada evento se RETIENE `wait_s` antes de emitirse, para darle tiempo a su
    POST. Los POST que nadie usó salen como «query» un poco después (`wait_s + 1`),
    y los de una misma ráfaga (una sesión que se conecta) cuentan una sola vez.
    `received` es el reloj monótono del servidor; `ts` es la hora del hecho.
    """

    def __init__(self, wait_s: float = 2.5, tolerance_s: float = 2.0, gap_s: float = 3.0):
        self.wait_s, self.tolerance_s, self.gap_s = wait_s, tolerance_s, gap_s
        self._events: list[tuple[float, dict]] = []
        self._posts: list[dict] = []          # {"ts", "origin", "received", "used"}
        self._last_query: dict[str, datetime] = {}

    def feed_cortex(self, event: dict, received: float) -> None:
        self._events.append((received, event))

    def feed_post(self, post: dict, received: float) -> None:
        self._posts.append({**post, "received": received, "used": False})

    def tick(self, now: float) -> list[dict]:
        out = []
        ready = [(r, e) for r, e in self._events if r <= now - self.wait_s]
        self._events = [(r, e) for r, e in self._events if r > now - self.wait_s]
        for _, e in sorted(ready, key=lambda x: x[1]["ts"]):
            free = [p for p in self._posts if not p["used"]]
            best = min(free, key=lambda p: abs((p["ts"] - e["ts"]).total_seconds()), default=None)
            if best is not None and abs((best["ts"] - e["ts"]).total_seconds()) <= self.tolerance_s:
                best["used"] = True
                out.append({**e, "origin": best["origin"]})
            else:
                out.append({**e, "origin": UNKNOWN})
        expired = [p for p in self._posts if p["received"] <= now - self.wait_s - 1]
        self._posts = [p for p in self._posts if p["received"] > now - self.wait_s - 1]
        for p in sorted(expired, key=lambda p: p["ts"]):
            if p["used"]:
                continue
            previous = self._last_query.get(p["origin"])
            self._last_query[p["origin"]] = p["ts"]
            if previous is not None and (p["ts"] - previous).total_seconds() < self.gap_s:
                continue                                  # sigue la misma ráfaga
            out.append({"ts": p["ts"], "type": "query", "ref": "cortex",
                        "detail": "query or connection", "origin": p["origin"]})
        return out
