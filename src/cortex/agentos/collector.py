"""Junta el estado de la instancia en state.json (+ notes.json y logs.json) para el panel.

Regla de oro: **muestra, nunca almacena**. La verdad vive donde siempre: el vault, los
reportes de rutinas, la config, los logs. Esto solo la lee y la deja junta; si los
archivos se borran, el siguiente ciclo los regenera.

Cada bloque lleva su propia marca de tiempo y su propio estado: cuando una fuente falla,
el panel lo marca en vez de mentir con un cero o esconder la caja.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config, paths
from . import graph as graph_mod
from . import pending as pending_mod
from . import pulses
from . import routines_view

MCP_URL = "http://127.0.0.1:8766/mcp"
PULSE_WINDOW_S = 150
PENDING_SCOPE = ("entities", "references", "decisions")
# feedback entra en la revisión: ahí viven las preferencias, lo que más cambia sin avisar
REVIEW_SCOPE = ("entities", "references", "decisions", "feedback")


def output_dir() -> Path:
    return paths.state_dir() / "agentos"


def bm_log() -> Path:
    return paths.state_dir() / "basic-memory" / "basic-memory.log"


def access_log() -> Path:
    return paths.logs_dir() / "access.log"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def block(fn):
    """Envuelve cada recolector: si falla, el panel lo sabe en vez de quedar en blanco."""
    def inner(*args):
        try:
            d = fn(*args)
            d["ok"] = True
        except Exception as exc:  # noqa: BLE001 — una fuente caída no tumba el resto
            d = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        d["read_at"] = _now()
        return d
    inner.__name__ = fn.__name__
    return inner


# ---------- fuentes ----------

def mcp_status(url: str | None = None, timeout: float = 3.0) -> str:
    """«up» si el MCP responde algo (cualquier estado HTTP); «down» si no se puede conectar."""
    url = url or MCP_URL
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout):
            return "up"
    except urllib.error.HTTPError:
        return "up"
    except (urllib.error.URLError, OSError, ValueError):
        return "down"


def git_dates(vault: Path) -> dict[str, str]:
    """Última fecha de commit por archivo, en una sola pasada de git log."""
    out = _run(["git", "-C", str(vault), "log", "--format=@%cI", "--name-only"], timeout=60)
    dates, current = {}, ""
    for line in out.splitlines():
        if line.startswith("@"):
            current = line[1:]
        elif line and line not in dates:
            dates[line] = current
    return dates


def read_skills() -> list[dict]:
    from ..marketplace import Market, parse_frontmatter  # noqa: PLC0415
    market = Market(paths.plugins_dir(), paths.plugins_public_dir())
    out = []
    for sk in market.list_skills():
        md = market.repo / "plugins" / sk["plugin"] / "skills" / sk["name"] / "SKILL.md"
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        try:
            area = str(parse_frontmatter(text).get("area") or "").strip()
        except Exception:  # noqa: BLE001 — un frontmatter roto no tumba el grafo
            area = ""
        out.append({"name": sk["name"], "plugin": sk["plugin"], "description": sk["description"],
                    "text": text, "area": area or graph_mod.GENERAL,
                    "path": f"plugins/{sk['plugin']}/skills/{sk['name']}/SKILL.md"})
    return out


def routine_snapshot(cfg: dict) -> dict:
    """Todo lo de las rutinas que comparten varios bloques, leído una sola vez."""
    from ..routines import report, runner  # noqa: PLC0415
    try:
        from ..secrets import resolve_env  # noqa: PLC0415
        env = resolve_env(cfg)
    except Exception:  # noqa: BLE001
        env = {}
    names = list(runner.ROUTINES)
    reports = {n: report.read_report(n) for n in names}
    histories = {n: report.read_history(n) for n in names}
    items = []
    for n in names:
        rcfg = cfg.get("routines", {}).get(n, {})
        try:
            state = runner.scheduled_state(n, cfg, env)
        except Exception:  # noqa: BLE001
            state = "unknown"
        rep = reports[n]
        items.append({"name": n, "enabled": bool(rcfg.get("enabled")), "schedule": rcfg.get("schedule", ""),
                      "state": state, "needs_claude": runner.ROUTINES[n].needs_claude,
                      "last_ts": (rep or {}).get("ts"), "last_status": (rep or {}).get("status"),
                      "summary": (rep or {}).get("summary") or "",
                      "ok": routines_view.is_ok(rep),
                      # fuera del crontab no corre sola: es un problema aunque la última corrida saliera bien
                      "healthy": routines_view.healthy(rep) and state != "invalid-schedule"})
    try:
        current = runner.current_run()
    except Exception:  # noqa: BLE001
        current = None
    return {"names": names, "reports": reports, "histories": histories, "items": items,
            "current": current}


def client_list(cfg: dict) -> list[dict]:
    """Los clientes con nombre (config) y el genérico para las IP sin nombre."""
    seen = pulses.last_seen(pulses.read_tail(access_log()))
    named = cfg.get("clients") or {}
    out = []
    for ip, name in sorted(named.items(), key=lambda kv: str(kv[1]).lower()):
        ts = seen.get(ip)
        out.append({"id": f"client:{ip}", "ip": ip, "label": str(name) or ip,
                    "last_seen": ts.isoformat(timespec="seconds") if ts else None})
    unnamed = {ip: ts for ip, ts in seen.items() if ip not in named}
    last = max(unnamed.values(), default=None)
    out.append({"id": pulses.CLIENT_UNKNOWN, "ip": None, "label": "client",
                "summary": "any IP not named in setup", "unnamed_ips": sorted(unnamed),
                "last_seen": last.isoformat(timespec="seconds") if last else None})
    return out


# ---------- bloques ----------

@block
def routines_block(snap: dict) -> dict:
    current = snap["current"]
    return {"items": snap["items"], "running": current["routine"] if current else None}


@block
def status_block(snap: dict, vault: Path) -> dict:
    """Barra de estado: si el núcleo trabaja (y qué rutina), rutinas sanas, edad del vault."""
    current = snap["current"]
    commit = _run(["git", "-C", str(vault), "log", "-1", "--format=%cI"])
    mtimes = []
    for f in vault.rglob("*.md"):
        if ".git" in f.parts:
            continue
        try:
            mtimes.append(f.stat().st_mtime)
        except OSError:
            continue
    last_write = (datetime.fromtimestamp(max(mtimes)).astimezone().isoformat(timespec="seconds")
                  if mtimes else None)
    return {"core": {"state": "working" if current else "idle",
                     "who": [current["routine"]] if current else []},
            "routines_ok": sum(1 for i in snap["items"] if i["healthy"]),
            "routines_total": len(snap["items"]),
            "vault_commit": commit or None, "vault_write": last_write}


@block
def vault_block(vault: Path, notes: list[dict]) -> dict:
    from ..routines import structure  # noqa: PLC0415
    folders: dict[str, int] = {}
    for n in notes:
        if n["layer"] == "core":
            continue
        parts = n["rel"].split("/")
        key = "/".join(parts[:2]) if parts[0] in ("entities", "episodes") and len(parts) > 2 else parts[0]
        folders[key] = folders.get(key, 0) + 1
    try:
        problems = structure.check(vault)
    except Exception:  # noqa: BLE001
        problems = None
    commits = _run(["git", "-C", str(vault), "rev-list", "--count", "HEAD"])
    last = _run(["git", "-C", str(vault), "log", "-1", "--format=%cI|%s"])
    when, _, subject = last.partition("|")
    return {"notes": sum(folders.values()), "by_folder": dict(sorted(folders.items())),
            "problems": None if problems is None else len(problems),
            "healthy": None if problems is None else not problems,
            "commits": int(commits) if commits.isdigit() else None,
            "last_commit": {"date": when or None, "subject": subject[:90]}}


@block
def pending_block(vault: Path, notes: list[dict], snap: dict, cfg: dict) -> dict:
    texts = {n["rel"]: (n["text"], n["meta"].get("updated")) for n in notes
             if n["rel"].split("/")[0] in PENDING_SCOPE}
    review = {n["rel"]: (n["text"], n["meta"].get("updated"), n["meta"].get("type")) for n in notes
              if n["rel"].split("/")[0] in REVIEW_SCOPE}
    from ..domains import store  # noqa: PLC0415
    return pending_mod.collect(texts, snap["reports"], cfg, date.today(), store.load(), review=review)


@block
def mcp_block() -> dict:
    return {"status": mcp_status(), "url": "/mcp"}


@block
def clients_block(cfg: dict) -> dict:
    return {"items": client_list(cfg)}


@block
def graph_block(notes, skills, snap, clients, mcp) -> dict:
    runs = routines_view.runs_per_day(snap["histories"], date.today())
    g, bodies = graph_mod.build(notes, skills, snap["items"], clients, mcp, git_dates(paths.vault_dir()), runs)
    g["_bodies"] = bodies
    return g


def indexes(g: dict) -> tuple[set[str], dict[str, str], dict[str, list[str]]]:
    """(ids, título -> id de las notas, rutina -> ids que toca): lo que usan los pulsos."""
    nodes = g.get("nodes", [])
    ids = {n["id"] for n in nodes}
    by_title = {n["label"]: n["id"] for n in nodes if n.get("layer") in graph_mod.NOTE_LAYERS}
    touches: dict[str, list[str]] = {}
    for a, b in g.get("edges", []):
        for r, o in ((a, b), (b, a)):
            if r.startswith("routine:"):
                touches.setdefault(r, []).append(o)
    return ids, by_title, touches


def run_events(finished: list[dict], touches: dict[str, list[str]]) -> list[dict]:
    """Cada rutina que terminó bien pulsa hacia lo que toca en el grafo."""
    out = []
    for r in finished:
        if not r["ok"]:
            continue
        rid = "routine:" + r["routine"]
        for target in sorted(touches.get(rid, [])):
            out.append({"ts": r["ts"], "type": "run", "origin": rid, "target": target,
                        "ref": target, "detail": r["routine"]})
    return out


@block
def pulses_block(g: dict, snap: dict, cfg: dict) -> dict:
    """Eventos REALES de los últimos 150 s, leídos de los logs (nada se guarda entre ciclos)."""
    until = datetime.now().astimezone()
    since = until - timedelta(seconds=PULSE_WINDOW_S)
    ids, by_title, touches = indexes(g)
    events = pulses.cross(pulses.cortex_events(pulses.read_tail(bm_log()), since),
                          pulses.mcp_posts(pulses.read_tail(access_log()), since, cfg.get("clients") or {}))
    events += run_events(routines_view.finished_in(snap["reports"], since, until), touches)
    return {"since": since.isoformat(timespec="seconds"), "until": until.isoformat(timespec="seconds"),
            "events": pulses.build(events, ids, by_title)}


# ---------- escritura ----------

def _write_json(target: Path, data) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)   # atómico: el panel nunca lee un archivo a medio escribir


def collect() -> dict:
    """Un ciclo completo: lee todo, escribe state.json, notes.json y logs.json. Devuelve el estado."""
    cfg = config.load()
    vault = paths.vault_dir()
    try:
        notes = graph_mod.read_vault(vault)
    except Exception:  # noqa: BLE001
        notes = []
    try:
        snap = routine_snapshot(cfg)
    except Exception:  # noqa: BLE001 — sin rutinas el resto del panel sigue
        snap = {"names": [], "reports": {}, "histories": {}, "items": [], "current": None}
    try:
        skills = read_skills()
    except Exception:  # noqa: BLE001
        skills = []
    mcp = mcp_block()
    clients = clients_block(cfg)
    g = graph_block(notes, skills, snap, clients.get("items", []), mcp.get("status"))
    bodies = g.pop("_bodies", {})
    log_items, logs_full = routines_view.entries(snap["names"], snap["reports"], snap["histories"])
    state = {
        "generated": _now(),
        "instance": {"name": (cfg.get("instance") or {}).get("name") or "cortex"},
        "status": status_block(snap, vault),
        "routines": routines_block(snap),
        "logs": {"items": log_items},
        "pending": pending_block(vault, notes, snap, cfg),
        "vault": vault_block(vault, notes),
        "mcp": mcp,
        "clients": clients,
        "graph": g,
        "pulses": pulses_block(g, snap, cfg),
    }
    out = output_dir()
    _write_json(out / "state.json", state)
    _write_json(out / "notes.json", bodies)
    _write_json(out / "logs.json", logs_full)
    return state


def main() -> int:
    state = collect()
    failed = [k for k, v in state.items() if isinstance(v, dict) and v.get("ok") is False]
    print(f"agentos: state written · {len(state) - 1} blocks"
          + (f" · FAILED: {', '.join(failed)}" if failed else " · all ok"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
