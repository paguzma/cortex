"""pick.py — qué notas limpiar esta noche. Sin modelo.

Primero lo que cambió de estado en las últimas 24 h (para que el ruido no
alcance a juntarse); después la cola de desorden (la deuda que ya existe). Una
nota ya limpiada no vuelve hasta crecer un 30 % o sumar tachados nuevos. Una que
el validador rechazó (o que frenó la válvula) espera REJECTION_DAYS días, salvo
que cambie su tamaño: así un rechazo no se repite cada noche."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .notes import in_scope
from .split import RX_STRUCK

GROWTH = 1.3
MIN_BYTES = 8000
REJECTION_DAYS = 7
RX_KEY = re.compile(r"^[A-Za-z_][\w-]*:(\s|$)")  # una clave del frontmatter, no prosa


def summarize(text: str) -> dict:
    lines = text.split("\n")
    return {"bytes": len(text.encode()),
            "struck": sum(1 for l in lines if RX_STRUCK.search(l)),
            "observations": sum(1 for l in lines if l.startswith("- ["))}


def state_changed(diff: str) -> bool:
    """¿El diff de una nota cambió su estado? status, un superseded nuevo o prosa del cuerpo
    (el párrafo inicial). Agregar una observación o tocar `updated` no cuenta."""
    for l in diff.split("\n"):
        if not l.startswith("+") or l.startswith("+++"):
            continue
        t = l[1:].strip()
        if t.startswith("status:") or "← superseded" in t:
            return True
        if t and not t.startswith(("- ", "#", "---")) and not RX_KEY.match(t):
            return True
    return False


def _came_back(n: dict, reg: dict | None) -> bool:
    return reg is None or n["bytes"] >= reg["bytes"] * GROWTH or n["struck"] > reg["struck"]


def _waiting(n: dict, rejection, now: datetime) -> bool:
    """¿Rechazada hace menos de REJECTION_DAYS días y con los mismos bytes? Un ts sin huso es hora local."""
    if not isinstance(rejection, dict):
        return False
    try:
        ts = datetime.fromisoformat(str(rejection["ts"]))
    except (KeyError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return now - ts < timedelta(days=REJECTION_DAYS) and rejection.get("bytes") == n["bytes"]


def pick(notes: dict[str, dict], changed: set[str], registry: dict[str, dict],
         limit: int = 2, rejections: dict | None = None,
         now: datetime | None = None) -> list[tuple[str, str]]:
    rejections = rejections or {}
    now = now or datetime.now().astimezone()
    notes = {p: n for p, n in notes.items() if not _waiting(n, rejections.get(p), now)}
    out: list[tuple[str, str]] = []
    for p in sorted((p for p in changed if in_scope(p) and p in notes),
                    key=lambda p: (-notes[p]["struck"], p)):
        out.append((p, "changed state in the last 24 h"))
    queue = [p for p, n in notes.items()
             if in_scope(p) and p not in changed
             and (n["struck"] >= 1 or n["bytes"] >= MIN_BYTES) and _came_back(n, registry.get(p))]
    queue.sort(key=lambda p: (-notes[p]["struck"], -notes[p]["bytes"], -notes[p]["observations"], p))
    for p in queue:
        n = notes[p]
        out.append((p, f"disorder queue: {n['struck']} struck, {n['bytes'] // 1024} KB, "
                       f"{n['observations']} observations"))
    return out[:limit]
