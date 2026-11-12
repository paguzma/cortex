"""Piezas compartidas por las pruebas de la rutina clean: un vault y un historial en
tmp_path, un ctx.claude falso y un Context armado a mano. Nunca se llama al CLI real."""
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from cortex.gitutil import IDENTITY
from cortex.routines.base import Context, Result
from cortex.routines.clean import clean

TODAY = "2026-10-07"

TARGET = """---
title: gamedesk (workstation Linux)
type: infra
status: active
created: 2026-08-25
updated: '2026-10-05'
aliases:
- gconsole
- worker cuda
permalink: cortex/entities/infra/gamedesk
---

`gconsole`: el PC de juegos de Ana (as of 2026-10-05).

## Observaciones
- ~~[estado] worker CUDA del homelab~~ ← superseded 2026-10-05: solo PC de juegos
- [estado] Formateado 2026-09-29
- [estado] Formateado el 2026-09-29, con Windows

## Relaciones
- pertenece_a [[ana]]
"""
NEIGHBOR = """---
title: UPS desktop
type: infra
created: 2026-08-25
permalink: cortex/entities/infra/ups-desktop
---

UPS.

## Relaciones
- protegerá [[gamedesk]]
"""

NAS = """---
title: NAS viejo
type: infra
created: 2026-08-25
permalink: cortex/entities/infra/nas-viejo
---

NAS.

## Observaciones
- ~~[estado] activo~~ ← superseded 2026-10-05: apagado

## Relaciones
- vive_en [[pve]]
"""


def git_raw(repo: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(["git", *IDENTITY, "-C", str(repo), *args], capture_output=True, text=True,
                       check=True, env=env)
    return r.stdout


def commit_old(repo: Path, message: str) -> None:
    """Commit con fecha vieja: así nada cuenta como «cambió en las últimas 24 h»."""
    old = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-01T10:00:00", "GIT_COMMITTER_DATE": "2026-10-01T10:00:00"}
    git_raw(repo, "add", "-A")
    git_raw(repo, "commit", "-qm", message, env=old)


class FakeClaude:
    """El «modelo» falso responde según el prompt: ordenar la nota o revisar el vecindario.

    `mode`: normal | INVENT (mete una IP) | TOUCH (alguien edita la nota mientras corre) |
    SIGTERM (la rutina recibe SIGTERM durante la llamada) | FAIL (sale con código 3)."""

    def __init__(self, vault: Path):
        self.vault = vault
        self.mode = "normal"
        self.calls: list[dict] = []

    def __call__(self, prompt, *, model, env, cwd, allowed_tools=(), mcp=False, timeout=3600):
        self.calls.append({"prompt": prompt, "model": model, "env": env, "cwd": Path(cwd),
                           "allowed_tools": allowed_tools, "mcp": mcp, "timeout": timeout})
        if self.mode == "FAIL":
            return 3, "boom"
        if "<nota_actual>" in prompt:
            note = prompt.split("<nota_actual>\n", 1)[1].split("\n</nota_actual>", 1)[0]
            note = (note.replace("title: gamedesk (workstation Linux)", "title: gconsole (PC de juegos)")
                        .replace("- worker cuda\n", "")
                        .replace("- [estado] Formateado 2026-09-29\n- [estado] Formateado el 2026-09-29, con Windows\n",
                                 "- [estado] Formateado 2026-09-29, con Windows\n"))
            if self.mode == "SIGTERM":
                os.kill(os.getpid(), signal.SIGTERM)
            if self.mode == "TOUCH" and "infra/gamedesk" in note:
                with open(self.vault / "entities/infra/gamedesk.md", "a") as f:
                    f.write("- [estado] tocado a mano\n")
            if self.mode == "INVENT":
                note = note.replace("con Windows", "con Windows en 10.9.9.9")
            return 0, "<nota>\n" + note + "\n</nota>"
        slug = prompt.split("La nota «", 1)[1].split("»", 1)[0]
        return 0, ('<json>[{"linea": "- protegerá [[%s]]", "motivo": "gconsole es solo PC de juegos"}]</json>'
                   % slug)


def make_ctx(vault: Path, archive: Path, state: Path, claude, *, apply: bool = False,
             model: str = "test-model") -> Context:
    cfg = {"routines": {"clean": {"enabled": True, "model": "", "apply": apply}}}
    return Context(cfg=cfg, env={"SOME_SECRET": "x"}, vault=vault, archive=archive, state=state,
                   model=model, claude=claude)


@dataclass
class World:
    root: Path
    vault: Path
    archive: Path
    state: Path
    claude: FakeClaude
    apply: bool = False

    def ctx(self) -> Context:
        return make_ctx(self.vault, self.archive, self.state, self.claude, apply=self.apply)

    def run(self, **kw) -> Result:
        kw.setdefault("today", TODAY)
        return clean.run(self.ctx(), **kw)

    def read(self, rel: str) -> str:
        return (self.vault / rel).read_text()

    def add_second_target(self, nas: str = NAS, own_neighbor: bool = True) -> None:
        """Otra nota en la cola de desorden (más chica: sale segunda) con su propia vecina."""
        (self.vault / "entities/infra/nas-viejo.md").write_text(nas)
        if own_neighbor:
            (self.vault / "entities/infra/ups-nas.md").write_text(
                NEIGHBOR.replace("UPS desktop", "UPS nas").replace("ups-desktop", "ups-nas")
                        .replace("[[gamedesk]]", "[[nas-viejo]]"))
        commit_old(self.vault, "nas")

    def registry(self, proposed: bool = False) -> dict:
        import json
        name = "registry-proposed.json" if proposed else "registry.json"
        return json.loads((self.state / name).read_text())

    def status(self, repo: Path) -> str:
        return subprocess.run(["git", "-C", str(repo), "status", "--short"], capture_output=True,
                              text=True).stdout

    def log(self, repo: Path) -> str:
        return subprocess.run(["git", "-C", str(repo), "log", "--oneline"], capture_output=True,
                              text=True).stdout


@pytest.fixture(autouse=True)
def isolated_git(tmp_path, monkeypatch):
    """La config git de la máquina (hooks, firma) no puede meterse en las pruebas."""
    cfg = tmp_path / "gitconfig"
    cfg.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def world(tmp_path) -> World:
    vault, archive, state = tmp_path / "vault", tmp_path / "archive", tmp_path / "state"
    (vault / "entities/infra").mkdir(parents=True)
    (vault / "entities/infra/gamedesk.md").write_text(TARGET)
    (vault / "entities/infra/ups-desktop.md").write_text(NEIGHBOR)
    for repo in (vault, archive):
        repo.mkdir(exist_ok=True)
        git_raw(repo, "init", "-q", "-b", "main")
    commit_old(vault, "base")
    return World(root=tmp_path, vault=vault, archive=archive, state=state, claude=FakeClaude(vault))
