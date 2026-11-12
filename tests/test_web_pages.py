import io
import json
import zipfile

from cortex import config, contract, paths
from cortex.secrets import LocalBackend


def test_secrets_hide_internal_and_never_show_values(admin):
    r = admin.post("/setup/secrets", data={"name": "my_key", "value": "valor-secreto"})
    assert r.status_code == 200 and "MY_KEY saved" in r.text
    page = admin.get("/setup/secrets").text
    assert "MY_KEY" in page and "valor-secreto" not in page
    assert "CORTEX_MCP_TOKEN" not in page
    r = admin.post("/setup/secrets", data={"name": "CORTEX_MCP_TOKEN", "value": "x"})
    assert r.status_code == 400
    assert LocalBackend(paths.secrets_file()).get_all()["CORTEX_MCP_TOKEN"] != "x"
    admin.post("/setup/secrets", data={"name": "MY_KEY", "action": "delete"})
    assert "MY_KEY" not in LocalBackend(paths.secrets_file()).get_all()


def test_upload_create_edit_delete_skill(admin):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("saludo/SKILL.md", "---\nname: saludo\ndescription: Saluda\n---\nHola\n")
    r = admin.post("/setup/skills/upload", files={"file": ("s.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200 and "Skill saludo published" in r.text
    r = admin.post("/setup/skills/upload", files={"file": ("s.zip", b"nope", "application/zip")})
    assert r.status_code == 400
    r = admin.post("/setup/skills/new", data={"name": "nueva"})
    assert r.headers["location"] == "/setup/skills/nueva"
    assert "name: nueva" in admin.get("/setup/skills/nueva").text
    r = admin.post("/setup/skills/nueva", data={"text": "---\nname: nueva\ndescription: d\n---\n"})
    assert r.headers["location"] == "/setup/skills"
    r = admin.post("/setup/skills/nueva", data={"text": "---\nname: otra\ndescription: d\n---\n"})
    assert r.status_code == 400
    admin.post("/setup/skills/nueva/delete")
    listing = admin.get("/setup/skills").text
    assert "saludo" in listing and "/setup/skills/nueva" not in listing
    plugin = json.loads((paths.plugins_dir() / "plugins" / "my-skills" / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == "0.1.3"


def test_connect_shows_tokens_and_settings(admin):
    admin.post("/setup/wizard/1", data={"timezone": "UTC", "base_url": "https://cortex.example"})
    tokens = LocalBackend(paths.secrets_file()).get_all()
    page = admin.get("/setup/connect").text
    assert tokens["CORTEX_MCP_TOKEN"] in page and tokens["CORTEX_PLUGINS_TOKEN"] in page
    assert f"https://cortex:{tokens['CORTEX_PLUGINS_TOKEN']}@cortex.example/plugins.git" in page
    admin.post("/setup/connect")
    assert LocalBackend(paths.secrets_file()).get_all()["CORTEX_MCP_TOKEN"] != tokens["CORTEX_MCP_TOKEN"]


def test_health_page(admin):
    page = admin.get("/setup/health").text
    for part in ("MCP", "Vault", "Marketplace", "Secrets", "Claude", "Remote backup"):
        assert part in page


def test_health_table_tells_warn_from_fail():
    from cortex.web.app import templates
    html = templates.get_template("_health_table.html").render(rows=[
        {"name": "a", "status": "warn", "detail": ""},
        {"name": "b", "status": "fail", "detail": ""}])
    assert 'class="warn">warn' in html and 'class="bad">fail' in html


def test_contract_drift_failure_is_a_warning_not_a_500(admin, monkeypatch):
    def boom(vault):
        raise OSError("SCHEMA.md es un directorio")
    monkeypatch.setattr(contract, "drift", boom)
    config.save({**config.load(), "setup_complete": True})
    assert admin.get("/setup").status_code == 200
    page = admin.get("/setup/health")
    assert page.status_code == 200 and "SCHEMA.md es un directorio" in page.text
