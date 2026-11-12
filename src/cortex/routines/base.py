"""Tipos compartidos por todas las rutinas."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

STATUSES = ("ok", "fail")


@dataclass
class Result:
    status: str          # "ok" | "fail"
    summary: str         # una línea, para la tabla de rutinas
    detail: str = ""     # texto libre; se corta a 20 KB al guardarse

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be ok or fail, not {self.status!r}")


@dataclass
class Context:
    cfg: dict                 # config.load()
    env: dict[str, str]       # secretos resueltos (secrets.resolve_env)
    vault: Path               # /data/vault
    archive: Path             # /data/archive (repo git del historial de limpiar)
    state: Path               # /data/state/routines/<rutina> (estado propio de la rutina)
    model: str                # modelo efectivo: el de la rutina o el global
    claude: Callable[..., tuple[int, str]] = field(repr=False, default=None)
    # claude(prompt, *, model, env, cwd, allowed_tools=(), mcp=False, timeout=3600) -> (rc, output)
