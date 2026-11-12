"""Escritura determinista del campo `area:`. Nada más se toca, y se verifica.

El modelo nunca edita notas: decide nombres. Quien escribe es este módulo, y un
validador comprueba archivo por archivo que lo único distinto es la línea `area:`.
"""
import os
import re
import tempfile
from pathlib import Path

AREA_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
RESERVED = {"general"}
FRONTMATTER = re.compile(r"\A---\n(.*?\n)---\n", re.S)
AREA_LINE = re.compile(r"^area:.*\n", re.M)


class AreaError(ValueError):
    pass


def valid_name(name: str) -> bool:
    return bool(AREA_RE.match(name)) and name not in RESERVED


def current_area(text: str) -> str | None:
    match = FRONTMATTER.match(text)
    if not match:
        return None
    line = re.search(r"^area:\s*(.*)$", match.group(1), re.M)
    return line.group(1).strip().strip("'\"") or None if line else None


def set_area(text: str, area: str) -> str:
    if not valid_name(area):
        raise AreaError(f"invalid domain name: {area!r}")
    match = FRONTMATTER.match(text)
    if not match:
        raise AreaError("the note has no frontmatter")
    block = match.group(1)
    new_block = AREA_LINE.sub(f"area: {area}\n", block) if AREA_LINE.search(block) \
        else block + f"area: {area}\n"
    return f"---\n{new_block}---\n" + text[match.end():]


def _without_area(text: str) -> str:
    match = FRONTMATTER.match(text)
    if not match:
        return text
    return "---\n" + AREA_LINE.sub("", match.group(1)) + "---\n" + text[match.end():]


def only_area_changed(before: str, after: str) -> bool:
    return _without_area(before) == _without_area(after)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(vault: Path, assignments: dict[str, str], overwrite: bool = False,
          skipped: list[str] | None = None) -> list[str]:
    """Escribe area: en cada nota (ruta relativa -> dominio). Todo o nada.

    Sin `overwrite`, una nota que ya tiene area: se salta: lo asignado no se cambia solo.
    Una nota sin frontmatter también se salta (y se anota en `skipped`): no hay dónde
    escribir area:, y no debe tumbar el resto del lote."""
    originals: dict[Path, str] = {}
    changed: list[str] = []
    try:
        for rel, area in sorted(assignments.items()):
            path = vault / rel
            before = path.read_text(encoding="utf-8")
            if current_area(before) and not overwrite:
                continue
            if not FRONTMATTER.match(before):
                if skipped is not None:
                    skipped.append(rel)
                continue
            after = set_area(before, area)
            if after == before:
                continue
            if not only_area_changed(before, after):
                raise AreaError(f"{rel}: more than the area line would change")
            originals[path] = before
            _atomic_write(path, after)
            if not only_area_changed(before, path.read_text(encoding="utf-8")):
                raise AreaError(f"{rel}: the file changed while writing it")
            changed.append(rel)
    except Exception:
        for path, text in originals.items():
            _atomic_write(path, text)
        raise
    return changed


def rename(vault: Path, old: str, new: str) -> int:
    """Pasa todas las notas del dominio `old` a `new` (renombrar o fusionar)."""
    if old == new:
        return 0
    targets = {}
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if any(part.startswith((".", "_")) for part in Path(rel).parts):
            continue
        if current_area(path.read_text(encoding="utf-8", errors="ignore")) == old:
            targets[rel] = new
    return len(apply(vault, targets, overwrite=True))
