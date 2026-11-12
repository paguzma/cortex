"""Propuestas de dominios que esperan a la persona (Pending de Agent OS y página Domains)."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .. import paths


def proposals_file() -> Path:
    return paths.state_dir() / "domains" / "proposals.json"


def load() -> list[dict]:
    try:
        data = json.loads(proposals_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    target = proposals_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def add(items: list[dict]) -> int:
    current = load()
    known = {p["id"] for p in current}
    fresh = [dict(i, ts=datetime.now().astimezone().isoformat(timespec="seconds"))
             for i in items if i["id"] not in known]
    if fresh:
        _save(current + fresh)
    return len(fresh)


def dismiss(proposal_id: str) -> None:
    _save([p for p in load() if p["id"] != proposal_id])


def forget_domain(domain: str) -> None:
    """Al renombrar o fusionar un dominio, sus propuestas dejan de tener sentido."""
    _save([p for p in load() if domain not in (p.get("domain"), p.get("source"), p.get("target"))])
