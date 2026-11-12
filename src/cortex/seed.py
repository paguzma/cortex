"""Siembra del vault bare-bone en un /data vacío."""
import shutil
from importlib.resources import files
from pathlib import Path

from .gitutil import commit_all, init_repo

SEED_MESSAGE = "chore: vault inicial de cortex"


def assets() -> Path:
    return Path(str(files("cortex") / "assets"))


def _is_empty(directory: Path) -> bool:
    return not directory.exists() or not any(p for p in directory.iterdir() if p.name != ".git")


def seed_vault(vault: Path, today: str) -> bool:
    """Copia el vault inicial solo si `vault` está vacío. Devuelve True si sembró."""
    if not _is_empty(vault):
        return False
    shutil.copytree(assets() / "vault", vault, dirs_exist_ok=True)
    for note in vault.rglob("*.md"):
        text = note.read_text()
        if "{{TODAY}}" in text:
            note.write_text(text.replace("{{TODAY}}", today))
    if not (vault / ".git").exists():
        init_repo(vault)
    commit_all(vault, SEED_MESSAGE)
    return True
