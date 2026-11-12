import base64

from cortex import config, paths
from cortex.secrets import LocalBackend
PASSWORD = "una-clave-bien-larga"


def test_unclaimed_redirects_to_claim(client):
    for url in ("/", "/setup", "/setup/login", "/setup/secrets"):
        r = client.get(url)
        if url == "/":
            r = client.get(r.headers["location"])
        assert r.headers["location"] == "/setup/claim"


def test_claim_rejects_wrong_code(client):
    r = client.post("/setup/claim", data={"code": "AAAA-AAAA", "password": PASSWORD,
                                          "password2": PASSWORD})
    assert r.status_code == 400 and "does not match" in r.text


def test_claim_rejects_short_or_mismatched_password(client):
    code = paths.setup_code_file().read_text()
    r = client.post("/setup/claim", data={"code": code, "password": "corta", "password2": "corta"})
    assert r.status_code == 400
    r = client.post("/setup/claim", data={"code": code, "password": PASSWORD, "password2": "x" * 12})
    assert r.status_code == 400 and "do not match" in r.text


def test_claim_accepts_lowercase_code_and_logs_in(client):
    code = paths.setup_code_file().read_text().lower()
    r = client.post("/setup/claim", data={"code": code, "password": PASSWORD, "password2": PASSWORD})
    assert r.headers["location"] == "/setup"
    assert not paths.setup_code_file().exists()
    assert client.get("/setup").headers["location"] == "/setup/wizard/1"
    assert client.get("/setup/claim").headers["location"] == "/setup/login"


def test_login_and_logout(admin):
    admin.post("/setup/logout")
    assert admin.get("/setup/secrets").headers["location"] == "/setup/login"
    assert admin.post("/setup/login", data={"password": "mala"}).status_code == 401
    assert admin.post("/setup/login", data={"password": PASSWORD}).status_code == 303
    assert admin.get("/setup/secrets").status_code == 200


def test_auth_mcp(client):
    token = LocalBackend(paths.secrets_file()).get_all()["CORTEX_MCP_TOKEN"]
    assert client.get("/auth/mcp").status_code == 401
    assert client.get("/auth/mcp", headers={"Authorization": "Bearer otro"}).status_code == 401
    assert client.get("/auth/mcp", headers={"Authorization": f"Bearer {token}"}).status_code == 204


def basic(token: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"cortex:{token}".encode()).decode()}


def test_plugins_git_requires_plugins_token(client):
    tokens = LocalBackend(paths.secrets_file()).get_all()
    url = "/plugins.git/info/refs?service=git-upload-pack"
    r = client.get(url)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get(url, headers=basic(tokens["CORTEX_MCP_TOKEN"])).status_code == 401
    r = client.get(url, headers=basic(tokens["CORTEX_PLUGINS_TOKEN"]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-git-upload-pack-advertisement"
    assert r.content.startswith(b"001e# service=git-upload-pack")


def test_plugins_git_is_read_only(client):
    token = LocalBackend(paths.secrets_file()).get_all()["CORTEX_PLUGINS_TOKEN"]
    assert client.get("/plugins.git/info/refs?service=git-receive-pack",
                      headers=basic(token)).status_code == 403
    assert client.post("/plugins.git/git-receive-pack", headers=basic(token)).status_code == 404
    assert client.get("/plugins.git/HEAD", headers=basic(token)).status_code == 404


def test_app_requires_session_key(data):
    import pytest
    from cortex.web.app import create_app
    with pytest.raises(RuntimeError):
        create_app()
