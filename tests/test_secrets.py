import stat

import httpx
import pytest

from cortex import config
from cortex.secrets import (InfisicalBackend, LocalBackend, ReadOnlyBackend, SecretError,
                            backend_from_config, resolve_env)


def test_local_set_get_delete(tmp_path):
    b = LocalBackend(tmp_path / "s.env")
    b.set("API_KEY", "a=b")
    assert b.get_all() == {"API_KEY": "a=b"}
    assert stat.S_IMODE((tmp_path / "s.env").stat().st_mode) == 0o600
    b.delete("API_KEY")
    assert b.names() == []


@pytest.mark.parametrize("name", ["lower", "1ABC", "A-B", ""])
def test_local_rejects_bad_names(tmp_path, name):
    with pytest.raises(SecretError):
        LocalBackend(tmp_path / "s.env").set(name, "x")


def test_local_rejects_newlines(tmp_path):
    with pytest.raises(SecretError):
        LocalBackend(tmp_path / "s.env").set("A", "x\ny")


def fake_infisical(status=200):
    def handler(request: httpx.Request):
        if request.url.path == "/api/v1/auth/universal-auth/login":
            if status != 200:
                return httpx.Response(status, json={"message": "no"})
            return httpx.Response(200, json={"accessToken": "tok"})
        assert request.headers["authorization"] == "Bearer tok"
        assert request.url.params["workspaceId"] == "p1"
        return httpx.Response(200, json={"secrets": [
            {"secretKey": "GROQ", "secretValue": "g"},
            {"secretKey": "CORTEX_MCP_TOKEN", "secretValue": "intruso"},
        ]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_infisical_reads():
    b = InfisicalBackend("http://inf", "id", "sec", "p1", "prod", http=fake_infisical())
    assert b.get_all()["GROQ"] == "g"


def test_infisical_auth_error():
    b = InfisicalBackend("http://inf", "id", "sec", "p1", "prod", http=fake_infisical(401))
    with pytest.raises(httpx.HTTPStatusError):
        b.get_all()


def test_infisical_is_read_only():
    b = InfisicalBackend("http://inf", "id", "sec", "p1", "prod", http=fake_infisical())
    with pytest.raises(ReadOnlyBackend):
        b.set("A", "1")


def test_resolve_env_local_hides_internal(tmp_path):
    local = LocalBackend(tmp_path / "s.env")
    local.set("CORTEX_MCP_TOKEN", "t")
    local.set("USER_KEY", "u")
    assert resolve_env(config.load(tmp_path / "none.json"), local) == {"USER_KEY": "u"}


def test_resolve_env_infisical_merges_and_filters(tmp_path):
    local = LocalBackend(tmp_path / "s.env")
    local.set("INFISICAL_CLIENT_ID", "id")
    local.set("INFISICAL_CLIENT_SECRET", "sec")
    local.set("CORTEX_MCP_TOKEN", "t")
    cfg = config.load(tmp_path / "none.json")
    cfg["secrets_backend"] = "infisical"
    cfg["infisical"].update(url="http://inf", project_id="p1")
    assert isinstance(backend_from_config(cfg, local), InfisicalBackend)
    assert resolve_env(cfg, local, http=fake_infisical()) == {"GROQ": "g"}
