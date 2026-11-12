from cortex import envcli
from cortex.secrets import LocalBackend
from cortex import paths


def test_without_command_returns_2(data):
    assert envcli.main([]) == 2
    assert envcli.main(["--"]) == 2


def test_execs_with_secrets(data, monkeypatch):
    LocalBackend(paths.secrets_file()).set("USER_KEY", "u")
    called = {}
    monkeypatch.setattr(envcli.os, "execvpe",
                        lambda f, a, e: called.update(file=f, args=a, env=e))
    envcli.main(["--", "echo", "hola"])
    assert called["file"] == "echo"
    assert called["args"] == ["echo", "hola"]
    assert called["env"]["USER_KEY"] == "u"


def test_returns_75_when_backend_fails(data, monkeypatch):
    def boom(cfg):
        raise ConnectionError("x")
    monkeypatch.setattr(envcli.secrets, "resolve_env", boom)
    assert envcli.main(["--", "true"]) == 75
