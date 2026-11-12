"""El hook SessionStart del plugin: corre con el python3 del cliente, no con el de cortex."""
import ast
import importlib.util
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cortex.seed import assets

HOOK = assets() / "plugin" / "hooks" / "session_start.py"


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("session_start", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- python del sistema (macOS trae 3.9) ---

def _annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def test_hook_parses_as_python_3_9():
    ast.parse(HOOK.read_text(), feature_version=(3, 9))


def test_hook_annotations_are_lazy_or_3_9_safe():
    # `str | None` se parsea en 3.9 pero revienta al evaluarse: o las anotaciones son
    # perezosas (from __future__ import annotations) o no usan `|`.
    tree = ast.parse(HOOK.read_text())
    lazy = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body)
    unions = [ast.unparse(a) for a in _annotations(tree)
              if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(a))]
    assert lazy or not unions, f"anotaciones con | sin __future__: {unions}"


def _python_3_9():
    """El 3.9 que uv tenga instalado (`uv python install 3.9`) o el del PATH."""
    if shutil.which("uv"):
        found = subprocess.run(["uv", "python", "find", "--no-project", "3.9"],
                               capture_output=True, text=True)
        if found.returncode == 0:
            return found.stdout.strip()
    return shutil.which("python3.9")


PYTHON_3_9 = _python_3_9()


@pytest.mark.skipif(not PYTHON_3_9, reason="sin python 3.9: uv python install 3.9")
def test_hook_loads_under_python_3_9(tmp_path):
    # sin .mcp.json el hook falla dentro de main() y sale en silencio
    run = subprocess.run([PYTHON_3_9, str(HOOK)], capture_output=True, text=True,
                         env={"CLAUDE_PLUGIN_ROOT": str(tmp_path)})
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


# --- compact ---

INDEX = """# INDEX

## Proyectos
- [cortex](proyectos/cortex.md) — memoria compartida
- [frame](proyectos/frame.md) - recuperar FRAME1.EXE
- [tbd](proyectos/tbd.md)
- [flowrs](proyectos/flowrs.md) fork TUI, local-only
## Personas
"""


def test_compact_keeps_titles_whatever_the_separator(hook):
    assert hook.compact(INDEX).splitlines() == [
        "## Proyectos",
        "- cortex — memoria compartida",
        "- frame — recuperar FRAME1.EXE",
        "- tbd",
        "- flowrs — fork TUI, local-only",
        "## Personas",
    ]


def test_compact_of_an_index_without_entries_is_empty(hook):
    assert hook.compact("# INDEX\n\n## Proyectos\n## Personas\n") == ""


def test_main_emits_nothing_for_an_empty_index(hook, monkeypatch, capsys):
    monkeypatch.setattr(hook, "read_index", lambda: "# INDEX\n\n## Proyectos\n")
    hook.main()
    assert capsys.readouterr().out == ""


# --- sesión MCP ---

class FakeServer:
    """urlopen falso: responde initialize/initialized/read_note y anota cada petición."""

    def __init__(self, fail_on=None):
        self.requests = []
        self.fail_on = fail_on

    def __call__(self, req, timeout):
        payload = json.loads(req.data) if req.data else None
        method = payload.get("method") if payload else None
        self.requests.append((req.get_method(), method, req.get_header("Mcp-session-id")))
        if method == self.fail_on:
            raise OSError("se cayó")
        body = b""
        if method == "tools/call":
            note = json.dumps({"result": INDEX})
            body = json.dumps({"result": {"content": [{"type": "text", "text": note}]}}).encode()
        return FakeResponse(body)


class FakeResponse(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Headers(dict):
    def __init__(self):
        super().__init__({"Mcp-Session-Id": "s-1"})

    def get_content_type(self):
        return "application/json"


@pytest.fixture
def plugin_root(hook, tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"cortex": {"url": "http://cortex.local/mcp"}}}))
    monkeypatch.setattr(hook, "ROOT", tmp_path)
    monkeypatch.setenv("CORTEX_MCP_TOKEN", "t")
    return tmp_path


def test_read_index_closes_the_mcp_session(hook, plugin_root, monkeypatch):
    server = FakeServer()
    monkeypatch.setattr(hook.urllib.request, "urlopen", server)
    assert hook.read_index() == INDEX
    assert server.requests[-1] == ("DELETE", None, "s-1")


def test_read_index_closes_the_session_even_if_read_note_fails(hook, plugin_root, monkeypatch):
    server = FakeServer(fail_on="tools/call")
    monkeypatch.setattr(hook.urllib.request, "urlopen", server)
    with pytest.raises(OSError):
        hook.read_index()
    assert server.requests[-1] == ("DELETE", None, "s-1")


class SlowServer(FakeServer):
    """Cada petición se come todo su timeout: así se ve cuánto puede tardar el hook."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.timeouts = []

    def __call__(self, req, timeout):
        self.timeouts.append(timeout)
        self.clock[0] += timeout
        return super().__call__(req, timeout)


def test_the_whole_hook_fits_in_its_claude_code_timeout(hook, plugin_root, monkeypatch):
    # Cuatro peticiones de 5 s cada una sumaban 20 s contra los 15 s del hook: con cortex
    # lento, Claude Code mataba el hook antes de que se rindiera solo.
    clock = [0.0]
    monkeypatch.setattr(hook.time, "monotonic", lambda: clock[0])
    server = SlowServer(clock)
    monkeypatch.setattr(hook.urllib.request, "urlopen", server)
    try:
        hook.read_index()
    except Exception:  # noqa: BLE001 — rendirse es válido; lo que importa es cuándo
        pass
    limit = json.loads((Path(hook.__file__).parent / "hooks.json").read_text())
    limit = limit["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]
    assert clock[0] < limit - 2
    assert server.requests[-1][0] == "DELETE"
