from cortex import config


def test_session_endpoint(client):
    r = client.get("/auth/session")
    assert r.status_code == 303 and r.headers["location"] == "/setup/login?next=/os/"


def test_session_endpoint_with_admin(admin):
    assert admin.get("/auth/session").status_code == 204
    admin.post("/setup/logout")
    assert admin.get("/auth/session").status_code == 303


def test_clients_crud(admin):
    r = admin.post("/setup/clients", data={"name": "laptop", "ip": " 192.168.1.20 "})
    assert r.status_code == 200 and "192.168.1.20 is now shown as laptop" in r.text
    assert config.load()["clients"] == {"192.168.1.20": "laptop"}
    assert admin.post("/setup/clients", data={"name": "x", "ip": "no-ip"}).status_code == 400
    assert admin.post("/setup/clients", data={"name": "", "ip": "10.0.0.1"}).status_code == 400
    admin.post("/setup/clients", data={"ip": "192.168.1.20", "action": "delete"})
    assert config.load()["clients"] == {}


def test_nav_links_agentos(admin):
    page = admin.get("/setup/clients").text
    assert 'href="/os/"' in page


def test_login_returns_to_agentos(admin):
    admin.post("/setup/logout")
    assert admin.get("/auth/session").headers["location"] == "/setup/login?next=/os/"
    assert 'value="/os/"' in admin.get("/setup/login?next=/os/").text
    r = admin.post("/setup/login", data={"password": _password(), "next": "/os/"})
    assert r.headers["location"] == "/os/"


def test_login_never_redirects_outside(admin):
    from cortex.web.app import safe_next
    for bad in ("https://evil.example", "//evil.example", "/os/..//evil", "/otra", None, ""):
        assert safe_next(bad) == "/setup"
    admin.post("/setup/logout")
    r = admin.post("/setup/login", data={"password": _password(), "next": "https://evil.example"})
    assert r.headers["location"] == "/setup"


def _password():
    return "una-clave-bien-larga"
