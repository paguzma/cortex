"""Configuración no secreta de la instancia (/data/config/config.json)."""
import copy
import json
import os
from pathlib import Path

from . import paths

DEFAULTS: dict = {
    "instance": {"name": "cortex", "timezone": "UTC", "base_url": ""},
    "secrets_backend": "local",
    "infisical": {"url": "", "project_id": "", "environment": "prod"},
    "claude": {"model": "opus", "token_loaded_at": None},
    "git_remote": {"url": ""},
    "admin": {"password_hash": None},
    "routines": {
        "consolidate": {"enabled": True, "schedule": "30 4 * * *", "model": ""},
        "clean": {"enabled": True, "schedule": "0 5 * * *", "model": "", "apply": False},
        "snapshot": {"enabled": True, "schedule": "0 * * * *", "model": ""},
    },
    "alert_url": "",
    "clients": {},
    "setup_complete": False,
    "session_key": None,
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path | None = None) -> dict:
    path = path or paths.config_file()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, json.loads(path.read_text()))


def save(cfg: dict, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
