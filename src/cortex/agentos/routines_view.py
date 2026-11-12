"""Lo que dijo cada rutina en su última corrida, para el panel.

La única fuente es el reporte que cada corrida deja (routines/report.py): el vigente
y el historial de las últimas corridas. Funciones puras: el recolector hace la E/S y
esto decide qué se muestra.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

MAX_SUMMARY = 160


def _iso(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def is_ok(report: dict | None) -> bool | None:
    """True/False según el último reporte; None si la rutina nunca corrió."""
    if not report:
        return None
    return report.get("status") == "ok"


def healthy(report: dict | None) -> bool:
    """Sana = su último reporte salió ok, o todavía no corrió nunca."""
    return is_ok(report) is not False


def run_view(report: dict) -> dict:
    """Un reporte (vigente o del historial) -> solo los campos que usa el lector."""
    return {"ts": report.get("ts"), "ok": is_ok(report),
            "summary": report.get("summary") or "", "detail": report.get("detail") or ""}


def entries(names: list[str], reports: dict[str, dict | None],
            histories: dict[str, list[dict]]) -> tuple[list[dict], dict[str, dict]]:
    """Una entrada por rutina: el resumen va en state.json y el detalle en logs.json.

    reports: {rutina: read_report()}; histories: {rutina: read_history()} (más nueva primero).
    """
    items, full = [], {}
    for name in names:
        rep = reports.get(name)
        entry = {"routine": name, "ts": None, "ok": None, "summary": None, "source": None}
        detail = ""
        if rep:
            entry = {**entry, "ts": rep.get("ts"), "ok": is_ok(rep),
                     "summary": (rep.get("summary") or "")[:MAX_SUMMARY], "source": "report"}
            detail = rep.get("detail") or ""
        items.append(entry)
        full[name] = {"entry": entry, "detail": detail,
                      "history": [run_view(r) for r in histories.get(name, []) if isinstance(r, dict)]}
    return items, full


def runs_per_day(histories: dict[str, list[dict]], today: date, days: int = 14) -> list[dict]:
    """Corridas por día y rutina de los últimos `days` días, leídas del historial."""
    first = today - timedelta(days=days - 1)
    counts: dict[tuple[str, str], dict] = {}
    for name, runs in histories.items():
        for r in runs:
            ts = _iso(r.get("ts")) if isinstance(r, dict) else None
            if ts is None or ts.date() < first or ts.date() > today:
                continue
            key = (ts.date().isoformat(), name)
            c = counts.setdefault(key, {"day": key[0], "routine": name, "ok": 0, "fail": 0})
            c["ok" if r.get("status") == "ok" else "fail"] += 1
    return sorted(counts.values(), key=lambda c: (c["day"], c["routine"]))


def finished_in(reports: dict[str, dict | None], since: datetime, until: datetime) -> list[dict]:
    """Reportes cuyo fin cae en la ventana -> [{routine, ts, ok}], para los pulsos «run»."""
    out = []
    for name, rep in reports.items():
        ts = _iso((rep or {}).get("ts"))
        if ts is not None and ts.tzinfo is not None and since <= ts <= until:
            out.append({"routine": name, "ts": ts, "ok": is_ok(rep)})
    return sorted(out, key=lambda r: r["ts"])
