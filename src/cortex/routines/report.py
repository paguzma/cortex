"""Reporte vigente e historial de cada rutina, y aviso opcional de fallas.

Una rutina nocturna que cambia cosas necesita poder revisarse días después, no solo la
última noche: por eso además del reporte vigente quedan las últimas corridas.
"""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from .. import paths
from .base import Result

MAX_DETAIL = 20_000
HISTORY = 30


def reports_dir() -> Path:
    return paths.state_dir() / "routines" / "reports"


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _alert(cfg: dict, report: dict, http: httpx.Client | None) -> str | None:
    url = cfg.get("alert_url", "")
    if not url or report["status"] != "fail":
        return None
    payload = {"instance": cfg["instance"]["name"], "routine": report["routine"],
               "status": report["status"], "summary": report["summary"], "ts": report["ts"]}
    try:
        (http or httpx.Client(timeout=10)).post(url, json=payload).raise_for_status()
    except httpx.HTTPError as exc:
        return f"alert failed: {type(exc).__name__}"
    return None


def write_report(name: str, result: Result, cfg: dict, now: datetime | None = None,
                 http: httpx.Client | None = None) -> dict:
    detail = result.detail
    if len(detail) > MAX_DETAIL:
        detail = detail[:MAX_DETAIL] + "\n[… cut at 20 KB]"
    report = {"routine": name,
              "ts": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
              "status": result.status, "summary": result.summary, "detail": detail}
    directory = reports_dir()
    _atomic_write(directory / f"{name}.json", json.dumps(report, ensure_ascii=False))
    history = directory / "history" / f"{name}.jsonl"
    lines = history.read_text(encoding="utf-8").splitlines() if history.exists() else []
    lines = (lines + [json.dumps(report, ensure_ascii=False)])[-HISTORY:]
    _atomic_write(history, "\n".join(lines) + "\n")
    # El aviso va después de escribir: si la URL falla, el reporte igual quedó.
    if warning := _alert(cfg, report, http):
        print(f"cortex-routine {name}: {warning}", flush=True)
    return report


def read_report(name: str) -> dict | None:
    target = reports_dir() / f"{name}.json"
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_history(name: str) -> list[dict]:
    target = reports_dir() / "history" / f"{name}.jsonl"
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))
