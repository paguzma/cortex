"""split.py — sacar de una nota lo tachado y llevarlo a su historial.

Paso determinista de la rutina clean: no usa modelo. Un tachado es
«~~viejo~~ ← superseded [AAAA-MM-DD][:]». Se quita SOLO el tramo tachado y su
marca: lo que viene después (el hecho nuevo) queda en la nota. La línea original
completa va al historial, que vive fuera del vault para que nadie la lea ni la
búsqueda la encuentre."""
from __future__ import annotations

import re

from .notes import split_note

# «… ← superseded 2026-10-05 por la versión con skills»: el «por » que queda suelto también se va.
RX_STRUCK = re.compile(r"(?:~~(?:(?!~~).)+~~\s*)+←\s*superseded(?:\s+\d{4}-\d{2}-\d{2})?\s*:?\s*(?:por\s+)?")
_EMPTY = {"", "-"}


def separate(note_text: str, history_path: str) -> tuple[str, list[str]]:
    """-> (nota sin tachados, líneas originales que salieron). El frontmatter no se toca."""
    _, raw, body = split_note(note_text)
    kept, moved = [], []
    for line in body.split("\n"):
        if not RX_STRUCK.search(line):
            kept.append(line)
            continue
        moved.append(line)
        new = RX_STRUCK.sub("", line).rstrip()
        if new.strip() not in _EMPTY:
            kept.append(new)
    if not moved:
        return note_text, []
    # La marca la lee el modelo (el prompt pide conservar «Historial: …»): no se traduce.
    mark = f"Historial: {history_path}"
    result = "\n".join(kept).rstrip("\n")
    if mark not in result:
        result += f"\n\n{mark}"
    return raw + result + "\n", moved


def append_history(existing: str | None, note_path: str, date: str, moved: list[str]) -> str:
    """El historial solo crece al final; lo que ya tenía queda idéntico."""
    base = existing if existing else (
        f"# Historial de {note_path}\n\n"
        "Líneas que salieron de la nota al limpiarla (rutina clean de cortex). Nunca se borran.\n")
    return base.rstrip("\n") + f"\n\n## {date} — cortex clean\n\n" + "\n".join(moved) + "\n"
