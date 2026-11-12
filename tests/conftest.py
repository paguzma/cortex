import pytest


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Un /data vacío y un HOME aislado para cada prueba."""
    root = tmp_path / "data"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    monkeypatch.setenv("CORTEX_DATA", str(root))
    monkeypatch.setenv("HOME", str(home))
    return root


@pytest.fixture
def ready(data, capsys):
    """Una instancia recién preparada por cortex-init, sin reclamar."""
    from cortex import init
    init.main()
    capsys.readouterr()
    return data


@pytest.fixture
def client(ready):
    from starlette.testclient import TestClient
    from cortex.web.app import create_app
    return TestClient(create_app(), follow_redirects=False)


PASSWORD = "una-clave-bien-larga"


@pytest.fixture
def admin(client):
    """Cliente con la instancia reclamada y la sesión de admin abierta."""
    from cortex import paths
    code = paths.setup_code_file().read_text()
    r = client.post("/setup/claim", data={"code": code, "password": PASSWORD,
                                          "password2": PASSWORD})
    assert r.status_code == 303
    return client
