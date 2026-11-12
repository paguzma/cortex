"""Pruebas de conexión del asistente y estado de salud de la instancia."""
import os
import subprocess
from pathlib import Path

import httpx

from . import contract, paths
from .gitutil import git
from .secrets import InfisicalBackend, resolve_env

MCP_INTERNAL = "http://127.0.0.1:8766/mcp"
CLAUDE_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def check_infisical(url: str, client_id: str, client_secret: str, project_id: str,
                    environment: str, http: httpx.Client | None = None) -> tuple[bool, str]:
    try:
        count = len(InfisicalBackend(url, client_id, client_secret, project_id, environment,
                                     http=http).get_all())
    except httpx.HTTPStatusError as exc:
        return False, f"Infisical answered {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return False, f"could not reach Infisical ({type(exc).__name__})"
    except (KeyError, ValueError):
        return False, "unexpected response from Infisical"
    return True, f"connected: {count} secrets visible"


def claude_credential(env: dict) -> str | None:
    return next((key for key in CLAUDE_KEYS if env.get(key)), None)


def check_claude(env: dict, model: str, run=subprocess.run) -> tuple[bool, str]:
    if not claude_credential(env):
        return False, "no Claude credential configured"
    try:
        result = run(["claude", "-p", "Reply with exactly one word: ok", "--model", model],
                     env={**os.environ, **env}, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return False, "the Claude CLI is not installed"
    except subprocess.TimeoutExpired:
        return False, "Claude did not answer within 3 minutes"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200]
        return False, f"Claude failed (exit {result.returncode}): {detail}"
    return True, f"Claude answered using model {model}"


def ensure_deploy_key(key: Path) -> str:
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "cortex",
                        "-f", str(key)], check=True)
    return key.with_name(key.name + ".pub").read_text().strip()


def deploy_public_key(key: Path) -> str | None:
    pub = key.with_name(key.name + ".pub")
    return pub.read_text().strip() if pub.exists() else None


def git_ssh_command(key: Path, known_hosts: Path) -> str:
    return (f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile={known_hosts}")


def check_git_remote(url: str, key: Path, known_hosts: Path,
                     run=subprocess.run) -> tuple[bool, str]:
    env = {**os.environ, "GIT_SSH_COMMAND": git_ssh_command(key, known_hosts),
           "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = run(["git", "ls-remote", "--heads", url], env=env, capture_output=True,
                     text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "the remote did not answer within 30 seconds"
    if result.returncode != 0:
        return False, f"git could not read the remote: {result.stderr.strip()[:200]}"
    return True, "the remote is reachable with the deploy key"


def health(cfg: dict, http: httpx.Client | None = None) -> list[dict]:
    rows: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        rows.append({"name": name, "status": status, "detail": detail})

    http = http or httpx.Client(timeout=3)
    try:
        http.get(MCP_INTERNAL)
        add("MCP", "ok", "basic-memory is answering")
    except httpx.HTTPError:
        add("MCP", "fail", "basic-memory is not answering on 127.0.0.1:8766")

    try:
        add("Vault", "ok", "last commit " + git(paths.vault_dir(), "log", "-1", "--format=%h, %cr"))
    except RuntimeError:
        add("Vault", "fail", "the vault is not a valid git repository")

    try:
        edited = contract.drift(paths.vault_dir())
    except Exception as exc:  # un archivo raro en el vault no debe tumbar Health
        add("Vault contract", "warn", f"could not compare with this image: {exc}")
    else:
        if edited:
            add("Vault contract", "warn", "edited by hand, so new versions from the image are "
                "not applied: " + ", ".join(edited))
        else:
            add("Vault contract", "ok", "matches this image")

    published = (paths.plugins_public_dir() / "HEAD").exists()
    add("Marketplace", "ok" if published else "fail",
        "published at /plugins.git" if published else "not published yet")

    try:
        env = resolve_env(cfg)
        add("Secrets", "ok", f"backend: {cfg['secrets_backend']}")
    except Exception as exc:
        env = {}
        add("Secrets", "fail", f"backend {cfg['secrets_backend']}: {type(exc).__name__}")

    credential = claude_credential(env)
    if credential:
        add("Claude", "ok", f"{credential}, model {cfg['claude']['model']}")
    else:
        add("Claude", "off", "no credential: routines that need a model stay off")

    from .routines import report as routine_report
    from .routines.runner import ROUTINES, scheduled_state
    from .routines.schedule import crontab_file
    failed = [name for name in cfg["routines"]
              if (last := routine_report.read_report(name)) and last["status"] == "fail"]
    # Solo las que correrían: una sin credencial ya se ve «off» y no debe tumbar Health.
    invalid = [name for name in ROUTINES
               if scheduled_state(name, cfg, env) == "invalid-schedule"]
    problems = []
    if failed:
        problems.append("last run failed: " + ", ".join(failed))
    if invalid:
        problems.append("invalid schedule, not running: " + ", ".join(invalid))
    if problems:
        add("Routines", "fail", "; ".join(problems))
    elif crontab_file().exists():
        add("Routines", "ok", "scheduled")
    else:
        add("Routines", "fail", "no crontab: cortex-init has not run")

    remote = cfg["git_remote"]["url"]
    add("Remote backup", "ok" if remote else "off",
        remote or "not configured: the vault is only versioned locally")
    return rows
