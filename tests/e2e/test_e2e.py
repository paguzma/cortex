"""Punta a punta contra un contenedor real. Lo lanza tests/e2e/run.sh."""
import asyncio
import io
import json
import os
import re
import subprocess
import time
import zipfile

import httpx
import pytest

URL = os.environ.get("CORTEX_E2E_URL", "")
CODE = os.environ.get("CORTEX_E2E_CODE", "")
PASSWORD = "contraseña-de-prueba-larga"
pytestmark = pytest.mark.skipif(not URL, reason="needs a running container (tests/e2e/run.sh)")


async def mcp_roundtrip(token: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(f"{URL}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                schema = await session.call_tool("read_note", {"identifier": "SCHEMA"})
                assert "Contrato de escritura" in schema.content[0].text
                written = await session.call_tool("write_note", {
                    "title": "prueba-e2e", "directory": "references",
                    "content": "# Prueba\n\nNota escrita por la prueba de punta a punta."})
                assert not written.is_error, written.content[0].text
                for _ in range(20):
                    found = await session.call_tool("search_notes", {"query": "prueba-e2e"})
                    if "prueba-e2e" in found.content[0].text:
                        return
                    await asyncio.sleep(1)
                raise AssertionError("the note never showed up in search")


def test_full_flow(tmp_path):
    ui = httpx.Client(base_url=URL, follow_redirects=True, timeout=60)
    r = ui.post("/setup/claim", data={"code": CODE, "password": PASSWORD, "password2": PASSWORD})
    assert r.status_code == 200 and "Set up cortex" in r.text
    r = ui.post("/setup/wizard/1", data={"name": "e2e", "timezone": "America/Santiago",
                                          "base_url": URL})
    assert "Secrets" in r.text
    ui.post("/setup/wizard/2", data={"backend": "local", "action": "save"})
    ui.post("/setup/wizard/3", data={"action": "skip"})
    r = ui.post("/setup/wizard/4", data={"remote_url": "", "action": "finish"})
    assert "Connect a machine" in r.text
    mcp_token = re.search(r'data-token="mcp">([^<]+)<', r.text).group(1)
    plugins_token = re.search(r'data-token="plugins">([^<]+)<', r.text).group(1)

    assert httpx.post(f"{URL}/mcp", json={}).status_code == 401
    asyncio.run(mcp_roundtrip(mcp_token))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("saludo/SKILL.md", "---\nname: saludo\ndescription: Saluda\n---\n\nHola.\n")
    r = ui.post("/setup/skills/upload", files={"file": ("saludo.zip", buf.getvalue(), "application/zip")})
    assert "Skill saludo published" in r.text

    host = URL.split("://", 1)[1]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    clone = tmp_path / "marketplace"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"http://cortex:{plugins_token}@{host}/plugins.git",
                    str(clone)], check=True, env=env)
    assert (clone / "plugins/my-skills/skills/saludo/SKILL.md").exists()
    assert json.loads((clone / "plugins/my-skills/.claude-plugin/plugin.json").read_text())["version"] == "0.1.1"
    mcp_json = json.loads((clone / "plugins/cortex/.mcp.json").read_text())
    assert mcp_json["mcpServers"]["cortex"]["url"] == f"{URL}/mcp"

    bad = subprocess.run(["git", "clone", "-q", f"http://cortex:wrong@{host}/plugins.git",
                          str(tmp_path / "denied")], capture_output=True, env=env)
    assert bad.returncode != 0

    health = ui.get("/setup/health").text
    assert re.search(r"<td>MCP</td><td class=\"ok\">ok</td>", health)

    check_routines(ui)
    check_agentos(ui)


def check_agentos(ui: httpx.Client) -> None:
    anonymous = httpx.get(f"{URL}/os/", follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"] == "/setup/login?next=/os/"
    assert httpx.get(f"{URL}/os/data/state.json", follow_redirects=False).status_code == 303
    assert ui.get("/os", follow_redirects=False).status_code == 308
    page = ui.get("/os/")
    assert page.status_code == 200 and "app.js" in page.text
    state = None
    for _ in range(30):
        r = ui.get("/os/data/state.json")
        if r.status_code == 200:
            state = r.json()
            break
        time.sleep(1)
    assert state is not None, "the collector never wrote state.json"
    # SSE atraviesa Caddy sin buffer: el primer evento llega enseguida.
    with ui.stream("GET", "/os/events", timeout=15) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        first = next(stream.iter_lines())
        assert first is not None


def check_routines(ui: httpx.Client) -> None:
    container = os.environ["CORTEX_E2E_CONTAINER"]
    crontab = subprocess.run(["docker", "exec", container, "cat", "/data/state/routines/crontab"],
                             capture_output=True, text=True, check=True).stdout
    assert "/usr/local/bin/cortex-routine snapshot" in crontab

    page = ui.get("/setup/routines").text
    assert page.count("off (no credential)") == 2

    # «Run now» desde la UI: la nota escrita por MCP queda commiteada en el vault.
    ui.post("/setup/routines/snapshot/run")
    for _ in range(30):
        page = ui.get("/setup/routines/snapshot").text
        if "Last 1 runs" in page:
            break
        time.sleep(1)
    assert "git: " in page and "no remote" in page
    log = subprocess.run(["docker", "exec", container, "git", "-C", "/data/vault", "log",
                          "--format=%s"], capture_output=True, text=True, check=True).stdout
    assert "chore(vault): snapshot" in log

    # El scheduler está vivo y s6 lo puede reiniciar con el mismo usuario de la UI.
    restart = subprocess.run(["docker", "exec", container, "/command/s6-svc", "-r",
                              "/run/service/supercronic"], capture_output=True, text=True)
    assert restart.returncode == 0, restart.stderr
