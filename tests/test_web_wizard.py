import json

from cortex import checks, config, paths
from cortex.secrets import LocalBackend


def test_step_instance(admin):
    r = admin.post("/setup/wizard/1", data={"name": "casa", "timezone": "America/Santiago",
                                             "base_url": "http://10.0.0.5:8700/"})
    assert r.headers["location"] == "/setup/wizard/2"
    cfg = config.load()
    assert cfg["instance"] == {"name": "casa", "timezone": "America/Santiago",
                               "base_url": "http://10.0.0.5:8700"}
    mcp = json.loads((paths.plugins_dir() / "plugins" / "cortex" / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cortex"]["url"] == "http://10.0.0.5:8700/mcp"


def test_step_instance_validates(admin):
    r = admin.post("/setup/wizard/1", data={"timezone": "Marte/Base", "base_url": "http://x"})
    assert r.status_code == 400 and "time zone" in r.text
    r = admin.post("/setup/wizard/1", data={"timezone": "UTC", "base_url": "ftp://x"})
    assert r.status_code == 400


def test_step_secrets_infisical_keeps_creds_local(admin):
    r = admin.post("/setup/wizard/2", data={
        "backend": "infisical", "url": "https://inf.example/", "project_id": "p",
        "environment": "", "client_id": "cid", "client_secret": "csec", "action": "save"})
    assert r.headers["location"] == "/setup/wizard/3"
    cfg = config.load()
    assert cfg["secrets_backend"] == "infisical"
    assert cfg["infisical"] == {"url": "https://inf.example", "project_id": "p",
                                "environment": "prod"}
    assert LocalBackend(paths.secrets_file()).get_all()["INFISICAL_CLIENT_SECRET"] == "csec"
    # dejar vacío el secreto no lo borra
    admin.post("/setup/wizard/2", data={"backend": "infisical", "url": "https://inf.example",
                                        "project_id": "p", "client_secret": ""})
    assert LocalBackend(paths.secrets_file()).get_all()["INFISICAL_CLIENT_SECRET"] == "csec"


def test_step_claude_saves_token_and_date(admin):
    r = admin.post("/setup/wizard/3", data={"model": "", "kind": "oauth", "value": "tok"})
    assert r.headers["location"] == "/setup/wizard/4"
    cfg = config.load()
    assert cfg["claude"]["model"] == "opus" and cfg["claude"]["token_loaded_at"]
    secrets = LocalBackend(paths.secrets_file()).get_all()
    assert secrets["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    admin.post("/setup/wizard/3", data={"model": "claude-opus-5-5", "kind": "api", "value": "key"})
    secrets = LocalBackend(paths.secrets_file()).get_all()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in secrets and secrets["ANTHROPIC_API_KEY"] == "key"
    assert config.load()["claude"]["token_loaded_at"] is None
    page = admin.get("/setup/wizard/3").text
    assert 'value="key"' not in page


def test_step_claude_test_button(admin, monkeypatch):
    seen = {}

    def fake(env, model):
        seen.update(env=env, model=model)
        return True, "Claude answered using model opus"
    monkeypatch.setattr(checks, "check_claude", fake)
    r = admin.post("/setup/wizard/3", data={"model": "opus", "kind": "oauth", "value": "tok",
                                             "action": "test"})
    assert r.status_code == 200 and "Claude answered" in r.text
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert "CORTEX_MCP_TOKEN" not in seen["env"]


def test_step_claude_skip(admin):
    r = admin.post("/setup/wizard/3", data={"action": "skip", "value": "ignorado"})
    assert r.headers["location"] == "/setup/wizard/4"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in LocalBackend(paths.secrets_file()).get_all()


def test_step_backup(admin):
    r = admin.post("/setup/wizard/4", data={"remote_url": "git@example.com:a/b.git", "action": "key"})
    assert r.status_code == 200 and "ssh-ed25519" in r.text
    r = admin.post("/setup/wizard/4", data={"remote_url": "", "action": "finish"})
    assert r.headers["location"] == "/setup/connect"
    assert config.load()["setup_complete"] is True
    assert admin.get("/setup").status_code == 200
