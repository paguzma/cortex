import subprocess

from cortex.routines.claude import child_env, run_claude


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "salida", "")


def test_no_tools_means_no_tools(data, tmp_path):
    run = Recorder()
    assert run_claude("hola", model="opus", env={}, cwd=tmp_path, run=run) == (0, "salida")
    cmd = run.calls[0][0]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd and "--mcp-config" not in cmd
    assert "--bare" not in cmd and "--no-session-persistence" in cmd


def test_tools_and_mcp(data, tmp_path):
    run = Recorder()
    run_claude("hola", model="opus", env={}, cwd=tmp_path, allowed_tools=("Read", "mcp__cortex"),
               mcp=True, run=run)
    cmd = run.calls[0][0]
    assert cmd[cmd.index("--tools") + 1] == "Read"
    assert "--permission-mode" not in cmd
    assert cmd[cmd.index("--allowedTools") + 1:cmd.index("--allowedTools") + 3] == ["Read", "mcp__cortex"]
    assert "--strict-mcp-config" in cmd


def test_subscription_token_wins_over_api_key():
    env = child_env({"CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "t" and "ANTHROPIC_API_KEY" not in env
    assert child_env({"ANTHROPIC_API_KEY": "k"})["ANTHROPIC_API_KEY"] == "k"


def test_missing_cli_and_timeout(data, tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    assert run_claude("x", model="m", env={}, cwd=tmp_path, run=missing)[0] == 127
    assert run_claude("x", model="m", env={}, cwd=tmp_path, run=slow)[0] == 124


def test_prompt_goes_through_stdin_not_argv(data, tmp_path):
    # Por argv, un prompt de más de 128 KiB hace fallar exec con E2BIG.
    run = Recorder()
    big = "x" * 300_000
    run_claude(big, model="opus", env={}, cwd=tmp_path, run=run)
    cmd, kwargs = run.calls[0]
    assert big not in cmd and cmd[:2] == ["claude", "-p"] and cmd[2].startswith("--")
    assert kwargs["input"] == big and "stdin" not in kwargs
