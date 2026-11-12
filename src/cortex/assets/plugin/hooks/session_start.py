#!/usr/bin/env python3
"""Hook SessionStart: inyecta el INDEX del vault como contexto de la sesión.

Lee la URL del MCP del `.mcp.json` del plugin y el token de `CORTEX_MCP_TOKEN`. Si cortex no
responde, sale en silencio: la sesión arranca igual, solo que sin el mapa.

Corre con el python3 del sistema, que en macOS es 3.9: nada de sintaxis más nueva.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# Todo el hook (hasta cuatro peticiones) cabe en DEADLINE segundos, por debajo de los 15 s
# que le da hooks.json: si cortex está lento, se rinde solo antes de que Claude Code lo mate.
DEADLINE = 10
CLOSE_TIMEOUT = 1
# por debajo del límite de 10 000 caracteres de additionalContext, con margen para el preámbulo
BUDGET = 9500
# el SCHEMA pide «- [título](ruta) — gancho», pero una entrada con otro separador (o sin
# él) conserva al menos el título: perderla saca la nota del mapa
ENTRY_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\([^)]*\)(?P<rest>.*)$")
SEPARATORS = "—–-:"
ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))


def _post(url: str, token: str, payload: dict, session: str | None,
          deadline: float) -> tuple[dict | None, str | None]:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("cortex no respondió a tiempo")
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {token}"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=left) as resp:
        session = resp.headers.get("Mcp-Session-Id") or session
        body = resp.read().decode()
    if not body.strip():
        return None, session
    if resp.headers.get_content_type() == "text/event-stream":
        data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        body = data[-1] if data else "{}"
    return json.loads(body), session


def _close(url: str, token: str, session: str) -> None:
    """Cierra la sesión MCP para no dejar una abierta en el servidor por cada arranque."""
    req = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": f"Bearer {token}", "Mcp-Session-Id": session})
    try:
        urllib.request.urlopen(req, timeout=CLOSE_TIMEOUT).close()
    except Exception:  # noqa: BLE001 — el servidor la expira igual
        pass


def read_index() -> str:
    url = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["cortex"]["url"]
    token = os.environ["CORTEX_MCP_TOKEN"]
    deadline = time.monotonic() + DEADLINE
    _, session = _post(url, token, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "cortex-session-start", "version": "1"}},
    }, None, deadline)
    try:
        _post(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, deadline)
        result, _ = _post(url, token, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "read_note", "arguments": {"identifier": "INDEX"}},
        }, session, deadline)
    finally:
        if session:
            _close(url, token, session)
    text = "".join(c.get("text", "") for c in result["result"]["content"])
    # read_note envuelve la nota en {"result": "..."}
    try:
        text = json.loads(text)["result"]
    except (ValueError, KeyError, TypeError):
        pass
    return text


def compact(index: str, budget: int = BUDGET) -> str:
    """Deja solo secciones y entradas, sin rutas; recorta los ganchos hasta caber.

    Claude Code corta un additionalContext largo a un avance de 2 KB, y el INDEX completo
    (hasta 200 líneas) no cabe: con el título basta para llegar a la nota con read_note.
    Un INDEX sin entradas (un vault recién creado) da "": no hay mapa que inyectar.
    """
    entries = []
    for line in index.splitlines():
        if line.startswith("## "):
            entries.append(("", line, ""))
        elif match := ENTRY_RE.match(line):
            hook = match["rest"].strip().lstrip(SEPARATORS).strip()
            entries.append(("- ", match["title"], hook))
    if not any(prefix for prefix, _, _ in entries):
        return ""
    for width in (None, 80, 60, 40, 20, 0):
        out = "\n".join(
            prefix + title + (f" — {hook[:width]}" if hook and width != 0 else "")
            for prefix, title, hook in entries)
        if len(out) <= budget:
            return out
    return out[:budget]


def main() -> None:
    try:
        index = compact(read_index())
    except Exception:  # noqa: BLE001 — sin cortex la sesión arranca igual
        return
    if not index:
        return
    context = ("Memoria cortex: este es el INDEX del vault (título — gancho). Úsalo como mapa: "
               "lee la nota con read_note(título) o busca con /mem-search; guarda con "
               "/mem-save.\n\n" + index)
    json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": context}}, sys.stdout)


if __name__ == "__main__":
    main()
