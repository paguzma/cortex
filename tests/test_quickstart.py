import json

from cortex import auth, config, paths, quickstart


def run(capsys, *argv):
    code = quickstart.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 else out.err)


def test_first_run_claims_and_prints_settings(ready, capsys, monkeypatch):
    restarted = []
    monkeypatch.setattr(quickstart.services, "restart", lambda name: restarted.append(name))
    code, out = run(capsys, "--timezone", "America/Santiago", "--name", "casa")
    assert code == 0
    cfg = config.load()
    assert auth.verify_password(cfg["admin"]["password_hash"], out["admin_password"])
    assert len(out["admin_password"]) >= auth.MIN_PASSWORD
    assert not paths.setup_code_file().exists()
    assert cfg["instance"] == {"name": "casa", "timezone": "America/Santiago",
                               "base_url": "http://localhost:8700"}
    assert cfg["setup_complete"] is True
    assert restarted == ["supercronic"]
    assert out["setup_url"] == "http://localhost:8700/setup"
    settings = out["settings"]
    assert settings["env"]["CORTEX_MCP_TOKEN"]
    assert settings["enabledPlugins"] == {"cortex@cortex": True, "my-skills@cortex": True}
    url = settings["extraKnownMarketplaces"]["cortex"]["source"]["url"]
    assert url.startswith("http://cortex:") and url.endswith("@localhost:8700/plugins.git")
    mcp = json.loads((paths.plugins_dir() / "plugins/cortex/.mcp.json").read_text())
    assert mcp["mcpServers"]["cortex"]["url"] == "http://localhost:8700/mcp"


def test_second_run_keeps_password_and_settings(ready, capsys, monkeypatch):
    monkeypatch.setattr(quickstart.services, "restart", lambda name: None)
    _, first = run(capsys, "--timezone", "America/Santiago")
    hashed = config.load()["admin"]["password_hash"]
    code, second = run(capsys)
    assert code == 0
    assert second["admin_password"] is None
    assert config.load()["admin"]["password_hash"] == hashed
    assert config.load()["instance"]["timezone"] == "America/Santiago"
    assert second["settings"] == first["settings"]


def test_rejects_bad_timezone_without_claiming(ready, capsys):
    code, err = run(capsys, "--timezone", "Marte/Olympus")
    assert code == 2 and "unknown time zone" in err
    assert config.load()["admin"]["password_hash"] is None
    assert paths.setup_code_file().exists()


def test_rejects_bad_base_url(ready, capsys):
    code, err = run(capsys, "--base-url", "localhost:8700")
    assert code == 2 and "base-url" in err


def test_waits_for_init(data, capsys):
    code, err = run(capsys)
    assert code == 75 and "not initialized" in err


def test_ui_login_works_with_generated_password(ready, capsys, monkeypatch):
    monkeypatch.setattr(quickstart.services, "restart", lambda name: None)
    _, out = run(capsys)
    from starlette.testclient import TestClient
    from cortex.web.app import create_app
    client = TestClient(create_app(), follow_redirects=False)
    r = client.post("/setup/login", data={"password": out["admin_password"]})
    assert r.status_code == 303
