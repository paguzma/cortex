"""Snapshot git determinista del vault y push opcional al remoto.

Corre al final de cada rutina pase lo que pase: si un agente degradó algo, git es la vía
de volver atrás, y un respaldo remoto solo replica historia commiteada.
"""
import os
import subprocess
from datetime import date

from .. import checks, paths
from ..gitutil import commit_all, git
from .base import Context, Result


def _push_env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": checks.git_ssh_command(paths.deploy_key_file(),
                                                      paths.known_hosts_file())}


def _sync_origin(ctx: Context, url: str) -> None:
    remotes = git(ctx.vault, "remote").split()
    if "origin" not in remotes:
        git(ctx.vault, "remote", "add", "origin", url)
    elif git(ctx.vault, "remote", "get-url", "origin") != url:
        git(ctx.vault, "remote", "set-url", "origin", url)


def snapshot(ctx: Context, message: str | None = None) -> Result:
    message = message or f"chore(vault): snapshot {date.today().isoformat()}"
    dirty = len(git(ctx.vault, "status", "--porcelain").splitlines())
    if commit_all(ctx.vault, message):
        committed = f"{dirty} changes → {git(ctx.vault, 'rev-parse', '--short', 'HEAD')}"
    else:
        committed = "no changes"

    url = ctx.cfg["git_remote"]["url"]
    if not url:
        return Result("ok", f"git: {committed} · no remote")
    _sync_origin(ctx, url)
    push = subprocess.run(["git", "-C", str(ctx.vault), "push", "-q", "origin", "HEAD:main"],
                          capture_output=True, text=True, env=_push_env(), timeout=120)
    if push.returncode != 0:
        return Result("fail", f"git: {committed} · push failed, retrying next run",
                      push.stderr.strip()[:4000])
    return Result("ok", f"git: {committed} · pushed")


def run(ctx: Context) -> Result:
    return snapshot(ctx)
