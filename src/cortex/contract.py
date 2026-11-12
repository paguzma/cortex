"""Lleva los archivos de contrato de la imagen a un vault ya sembrado.

Un archivo se reemplaza solo si su contenido es el que cortex instaló, es decir, si nadie
lo editó. Lo instalado se guarda en /data/state/contract.json como id de blob de git; un
vault sembrado antes de ese registro toma como instalada la versión de su commit de siembra.
Un archivo editado a mano no se toca y queda como aviso en health. Un archivo borrado cuenta
como editado: no se repone (para volver a la versión de la imagen, copiarla a mano desde
assets/vault). Un vault sin el commit de siembra ni registro (p. ej. importado de otro lado)
no tiene contra qué comparar, así que todo archivo que difiera de la imagen se ve editado.

Las comparaciones leen el frontmatter como YAML y comparan sus campos y el cuerpo, sin
mirar formato, CRLF ni espacios al borde. basic-memory reescribe los archivos que indexa
(agrega `permalink:`, crea el frontmatter con `title`/`type`/`permalink` si falta, quita el
salto de línea final) y eso no cuenta como edición: se ignora `permalink` siempre, y
`title`/`type` solo en los archivos que la imagen trae sin frontmatter (BARE). Esa lista va
fija y no se deduce de la imagen: los ids guardados en el registro se calcularon sin
`title`/`type`, y si una imagen futura le pone frontmatter a CLAUDE.md dejarían de coincidir
y el archivo se vería editado. El costo es que en esos archivos un cambio a mano de
`title`/`type` no cuenta como edición.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

from . import paths
from .gitutil import git
from .seed import SEED_MESSAGE, assets

TOP = ("AGENTS.md", "CLAUDE.md", "SCHEMA.md", "BOOTSTRAP.md")
COMMIT_MESSAGE = "chore(contract): contrato del vault actualizado desde la imagen"


def source() -> Path:
    return assets() / "vault"


def contract_files() -> list[str]:
    templates = sorted(p.relative_to(source()).as_posix()
                       for p in (source() / "_templates").glob("*.md"))
    return [*TOP, *templates]


REGISTRY_VERSION = 3


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _split(data: bytes) -> tuple[dict | None, str]:
    """(frontmatter, cuerpo). Un frontmatter que no es un mapa YAML se compara como texto."""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    raw = text[4:end] if end >= 4 else ""
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        meta = None
    if not isinstance(meta, dict):
        meta = {"": raw.strip()}
    return meta, text[end + 4:]


# Los que la imagen trae sin frontmatter: el `title`/`type` que tengan lo puso basic-memory.
# Si se agrega uno, los ids ya guardados para él cambian: el arranque siguiente lo verá editado.
BARE = frozenset({"CLAUDE.md"})


def _bare(rel: str) -> bool:
    return rel in BARE


def _str_keys(value):
    """YAML admite claves que no son texto (`on:` es bool, `2024:` es int): se pasan a str
    para que json.dumps pueda ordenarlas."""
    if isinstance(value, dict):
        return {str(k): _str_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_str_keys(v) for v in value]
    return value


def normalized(rel: str, data: bytes) -> bytes:
    """Frontmatter y cuerpo en forma canónica, sin lo que inyecta basic-memory."""
    meta, text = _split(data)
    meta = _str_keys(meta or {})
    meta.pop("permalink", None)
    if _bare(rel):
        meta.pop("title", None)
        meta.pop("type", None)
    head = json.dumps(meta, sort_keys=True, ensure_ascii=False, default=str) if meta else ""
    return f"{head}\n---\n{text.strip()}\n".encode()


def content_id(rel: str, data: bytes) -> str:
    return blob_id(normalized(rel, data))


def _v2_id(data: bytes) -> str:
    """El id de la versión 2 del registro: el cuerpo sin frontmatter."""
    text = data.decode("utf-8", errors="replace")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:]
    return blob_id(text.strip().encode() + b"\n")


def state_file() -> Path:
    return paths.state_dir() / "contract.json"


def _write(path: Path, data: bytes) -> None:
    """Escritura atómica: un corte deja la versión anterior entera, nunca una a medias."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load() -> dict:
    try:
        text = state_file().read_text()
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        print(f"cortex: {state_file()} is not valid JSON; using the vault's seed commit",
              file=sys.stderr, flush=True)
        return {}


def _save(installed: dict) -> None:
    state_file().parent.mkdir(parents=True, exist_ok=True)
    state = {"installed": installed, "version": REGISTRY_VERSION}
    _write(state_file(), (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())


def _blob(vault: Path, spec: str) -> bytes | None:
    try:
        return git(vault, "cat-file", "blob", spec).encode()
    except RuntimeError:
        return None


def _cortex_versions(vault: Path, rel: str) -> list[bytes]:
    """Lo que cortex escribió en `rel` según el git del vault (siembra y actualizaciones),
    de la más nueva a la más vieja, y al final la versión de la imagen."""
    try:
        commits = git(vault, "log", "--format=%H", "--fixed-strings", f"--grep={SEED_MESSAGE}",
                      f"--grep={COMMIT_MESSAGE}", "--", rel).split()
    except RuntimeError:
        commits = []
    found = [data for c in commits if (data := _blob(vault, f"{c}:{rel}")) is not None]
    asset = source() / rel
    return found + ([asset.read_bytes()] if asset.exists() else [])


def _installed(vault: Path) -> dict:
    """El registro con ids de contenido. Uno de la versión 1 guardaba ids de blob crudos:
    se traducen leyendo el blob del git del vault. Uno de la versión 2 guardaba el id del
    cuerpo sin frontmatter: se busca, entre lo que cortex escribió, la versión con ese
    cuerpo. Se descarta el que no se encuentre."""
    state = _load()
    installed = state.get("installed", {})
    version = state.get("version")
    if version == REGISTRY_VERSION:
        return installed
    out = {}
    for rel, old in installed.items():
        if version == 2:
            data = next((d for d in _cortex_versions(vault, rel) if _v2_id(d) == old), None)
        else:
            data = _blob(vault, old)
        if data is not None:
            out[rel] = content_id(rel, data)
    return out


def _seed_commit(vault: Path) -> str | None:
    try:
        found = git(vault, "log", "--format=%H", "--fixed-strings",
                    f"--grep={SEED_MESSAGE}", "--reverse")
    except RuntimeError:
        return None
    return found.split("\n")[0] or None


def _seeded_id(vault: Path, commit: str | None, rel: str) -> str | None:
    if not commit:
        return None
    data = _blob(vault, f"{commit}:{rel}")
    return content_id(rel, data) if data is not None else None


def _revert(vault: Path, changed: list[str], written: list[tuple[Path, bytes | None]]) -> None:
    try:
        git(vault, "reset", "-q", "--", *changed)
    except RuntimeError:
        pass
    for target, before in written:
        try:
            if before is None:
                target.unlink(missing_ok=True)
            elif target.read_bytes() != before:
                _write(target, before)
        except OSError:
            pass


def sync(vault: Path) -> dict[str, list[str]]:
    """Actualiza los archivos no editados y commitea solo esos. Devuelve qué pasó con cada uno.

    Si la escritura o el commit fallan (p. ej. un .git/index.lock huérfano), deja los archivos
    como estaban, no toca el registro y relanza el error: el siguiente arranque lo reintenta."""
    installed = _installed(vault)
    seed_commit = None if installed else _seed_commit(vault)
    result: dict[str, list[str]] = {"updated": [], "added": [], "edited": []}
    for rel in contract_files():
        new = (source() / rel).read_bytes()
        new_id = content_id(rel, new)
        target = vault / rel
        known = installed.get(rel) or _seeded_id(vault, seed_commit, rel)
        if not target.exists():
            if known:
                result["edited"].append(rel)
                continue
            result["added"].append(rel)
        else:
            current = content_id(rel, target.read_bytes())
            if current == new_id:
                installed[rel] = new_id
                continue
            if current != known:
                result["edited"].append(rel)
                if known:
                    installed[rel] = known
                continue
            result["updated"].append(rel)
        installed[rel] = new_id
    changed = result["updated"] + result["added"]
    written: list[tuple[Path, bytes | None]] = []
    if changed:
        try:
            for rel in changed:
                target = vault / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                written.append((target, target.read_bytes() if target.exists() else None))
                _write(target, (source() / rel).read_bytes())
            git(vault, "add", "--", *changed)
            git(vault, "commit", "-q", "-m", COMMIT_MESSAGE, "-m", "\n".join(changed),
                "--", *changed)
        except Exception:
            _revert(vault, changed, written)
            raise
    _save(installed)
    return result


def drift(vault: Path) -> list[str]:
    """Archivos de contrato que difieren de la versión de la imagen (editados o borrados)."""
    out = []
    for rel in contract_files():
        target = vault / rel
        if not target.exists() or (normalized(rel, target.read_bytes())
                                    != normalized(rel, (source() / rel).read_bytes())):
            out.append(rel)
    return out
