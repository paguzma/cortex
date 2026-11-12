"""Única puerta al CLI de Claude Code para las rutinas (modo headless, `claude -p`).

Nunca usa --bare: con --bare la autenticación es solo por ANTHROPIC_API_KEY y el token de
suscripción dejaría de servir.
"""
import json
import os
import subprocess
from pathlib import Path

from .. import paths

MCP_INTERNAL_URL = "http://127.0.0.1:8766/mcp"
CREDENTIAL_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


def mcp_config_file() -> Path:
    """Config MCP interna: el CLI habla con basic-memory directo, sin pasar por Caddy."""
    target = paths.state_dir() / "routines" / "claude-mcp.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": {"cortex": {"type": "http",
                                                            "url": MCP_INTERNAL_URL}}}))
    return target


def child_env(env: dict[str, str]) -> dict[str, str]:
    """Entorno del CLI: el del proceso, los secretos, y solo UNA credencial de Claude.

    Si están las dos, gana el token de suscripción y la API key se quita, para que el
    CLI no cobre por API sin que nadie lo haya pedido.
    """
    out = {**os.environ, **env}
    if out.get("CLAUDE_CODE_OAUTH_TOKEN"):
        out.pop("ANTHROPIC_API_KEY", None)
    return out


def run_claude(prompt: str, *, model: str, env: dict[str, str], cwd: Path,
               allowed_tools: tuple[str, ...] = (), mcp: bool = False, timeout: int = 3600,
               run=subprocess.run) -> tuple[int, str]:
    # El prompt va por stdin: por argv, más de 128 KiB hace fallar exec con E2BIG.
    cmd = ["claude", "-p", "--model", model, "--no-session-persistence",
           "--setting-sources", "user"]
    # Solo existen las herramientas built-in que la rutina pide (sin ninguna, el modelo solo
    # responde texto) y solo esas más las del MCP se aprueban. Sin acceptEdits: cualquier
    # otra cosa queda denegada en modo headless.
    builtins = [t for t in allowed_tools if not t.startswith("mcp__")]
    cmd += ["--tools", ",".join(builtins)]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    if mcp:
        cmd += ["--mcp-config", str(mcp_config_file()), "--strict-mcp-config"]
    try:
        result = run(cmd, cwd=str(cwd), env=child_env(env), input=prompt,
                     capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "the Claude CLI is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"Claude did not finish within {timeout} s"
    return result.returncode, (result.stdout or "") + (result.stderr or "")
