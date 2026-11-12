"""Servidor git «smart» de solo lectura para el marketplace, vía `git http-backend` (CGI).

Claude Code clona los marketplaces con --depth 1, que el protocolo «dumb» no soporta.
Solo se aceptan las dos rutas de lectura (upload-pack); push queda fuera.
"""
import asyncio
import os
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

ALLOWED = {("GET", "info/refs"), ("POST", "git-upload-pack")}


def parse_cgi(output: bytes) -> tuple[int, dict[str, str], bytes]:
    for separator in (b"\r\n\r\n", b"\n\n"):
        head, found, body = output.partition(separator)
        if found:
            break
    else:
        return 502, {}, b""
    status, headers = 200, {}
    for line in head.decode("latin-1").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key.lower() == "status":
            status = int(value.split()[0])
        elif key:
            headers[key] = value
    return status, headers, body


async def serve(request: Request, repo: Path) -> Response:
    path = request.path_params["path"]
    if (request.method, path) not in ALLOWED:
        return Response(status_code=404)
    if path == "info/refs" and request.query_params.get("service") != "git-upload-pack":
        return Response("only smart HTTP reads are served\n", status_code=403)
    body = await request.body()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_PROJECT_ROOT": str(repo.parent),
        "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": f"/{repo.name}/{path}",
        "REQUEST_METHOD": request.method,
        "QUERY_STRING": request.url.query,
        "CONTENT_TYPE": request.headers.get("content-type", ""),
        "CONTENT_LENGTH": str(len(body)),
        "REMOTE_USER": "cortex",
    }
    if protocol := request.headers.get("git-protocol"):
        env["GIT_PROTOCOL"] = protocol
        env["HTTP_GIT_PROTOCOL"] = protocol
    if encoding := request.headers.get("content-encoding"):
        env["HTTP_CONTENT_ENCODING"] = encoding
    proc = await asyncio.create_subprocess_exec(
        "git", "http-backend", env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate(body)
    if proc.returncode != 0:
        return Response(status_code=500)
    status, headers, payload = parse_cgi(out)
    return Response(payload, status_code=status, headers=headers)
