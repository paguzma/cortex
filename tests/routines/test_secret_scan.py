import pytest

from cortex.routines import consolidate, secret_scan, structure
from cortex.routines.base import Context

# Valores inventados con la misma forma que los reales que aparecieron en un vault.
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJhYmMxMjMiLCJpYXQiOjE3MDAwMDAwMDB9.Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA"
PVE = "PVEAPIToken=claude@pve!cc=8f14e45f-ceea-4b7a-9d3c-1a2b3c4d5e6f"


@pytest.mark.parametrize("line, kind", [
    (f"```\n{JWT}\n```", "jwt"),
    (f"- **Token:** `{PVE}`", "key-value"),
    ("- **Password:** Xk9#mQ2vLp7zT", "key-value"),
    ("PASS=aB3dE5fG7hJ9kL1mN3pQ", "key-value"),
    ('api_key: "Q2hhbmdlTWVQbGVhc2UxMjM0NTY3OA=="', "key-value"),
    ("password: S3cr3t-Synapse-2026", "key-value"),
    ("DATABASE_URL=postgres://synapse:Hunter2xYz9@db:5432/synapse", "url-password"),
    ("OPENAI_KEY=sk-proj-AbCdEf0123456789AbCdEf0123", "known-prefix"),
    ("glpat" + "-AbCdEf0123456789AbCd", "known-prefix"),  # partido: que los escáneres de push no lo tomen por real
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
])
def test_finds_plaintext_secrets(line, kind):
    assert [f.kind for f in secret_scan.scan_text(line)] == [kind]


@pytest.mark.parametrize("line", [
    "curl -H 'Authorization: Bearer $TOKEN' https://ha/api",
    "TOKEN=$(secreto HOME_ASSISTANT_TOKEN)",
    '  password: \\$SYNAPSE_DB_PASS_ROTATED',
    'curl -s -H \\"X-Api-Key: \\$APIKEY_RADARR4K\\" http://localhost:7879/api/v3/queue',
    "wifi_password: !secret wifi_password",
    "api_key: ${{ secrets.ESPHOME_KEY }}",
    "password: <la-de-infisical>",
    'HA_TOKEN = os.environ["HA_TOKEN"]',
    "- [config] Pass = en env.txt (ver [[creds-env-txt]])",
    "- [referencia] la key vive en Infisical /prod/GROQ_KEY",
    "LDAP_BIND_PASSWORD  = password   # dev",
    "Rotar `SURE_MCP_TOKEN` = requiere reiniciar el bot",
    "password: xxxxxxxxxxxx",
    "token: ************",
    "- **Password:** ‹redactado 2026-10-22: el valor vive en Infisical o en la historia git del vault›",
    "[Zoom](https://us02web.zoom.us/j/88477591386?pwd=aB3dE5fG7hJ9kL1mN3pQrS5tU7vW9xY1)",
])
def test_ignores_references_variables_and_placeholders(line):
    assert secret_scan.scan_text(line) == []


def test_reports_location_and_never_the_value(tmp_path):
    (tmp_path / "entities/infra").mkdir(parents=True)
    (tmp_path / "entities/infra/nas.md").write_text(f"# NAS\n\n- **Token:** `{PVE}`\n")
    found = secret_scan.scan(tmp_path)
    assert [(f.path, f.line, f.kind) for f in found] == [("entities/infra/nas.md", 3, "key-value")]
    text = "\n".join(structure.warnings(tmp_path))
    assert "entities/infra/nas.md:3" in text
    assert "8f14e45f" not in text and "claude@pve" not in text


def test_scans_every_folder_including_the_human_pkm(tmp_path):
    # Donde aparecieron los secretos reales: notas viejas fuera de las carpetas del SCHEMA.
    (tmp_path / "vault42/02 - Areas").mkdir(parents=True)
    (tmp_path / "vault42/02 - Areas/portainer.md").write_text("- **Password:** Xk9#mQ2vLp7zT\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/COMMIT_EDITMSG").write_text("- **Password:** Xk9#mQ2vLp7zT\n")
    assert [f.path for f in secret_scan.scan(tmp_path)] == ["vault42/02 - Areas/portainer.md"]


def test_a_secret_is_a_warning_not_a_violation(tmp_path):
    (tmp_path / "INDEX.md").write_text("# INDEX\n")
    (tmp_path / "vault42").mkdir()
    (tmp_path / "vault42/x.md").write_text(f"{JWT}\n")
    assert structure.check(tmp_path) == []
    assert structure.summary_line(tmp_path, []).startswith("STRUCTURE OK")
    assert any("possible plaintext secret" in w for w in structure.warnings(tmp_path))


def test_the_consolidating_agent_does_not_get_pointed_at_secrets(tmp_path):
    # El veredicto lo reporta; el prompt del agente no, para que no vaya a abrir esas notas.
    (tmp_path / "INDEX.md").write_text("# INDEX\n")
    (tmp_path / "vault42").mkdir()
    (tmp_path / "vault42/x.md").write_text(f"{JWT}\n")
    ctx = Context(cfg={}, env={}, vault=tmp_path, archive=tmp_path / "a", state=tmp_path / "s", model="m")
    assert "vault42/x.md" not in consolidate.prompt(ctx)
    assert "vault42/x.md" in structure.render(tmp_path, [])


def test_many_findings_are_summarised(tmp_path):
    (tmp_path / "INDEX.md").write_text("# INDEX\n")
    for i in range(15):
        (tmp_path / f"n{i:02}.md").write_text(f"{JWT}\n")
    lines = [w for w in structure.warnings(tmp_path) if "secret" in w]
    assert len(lines) == 1
    assert lines[0].startswith("15 possible plaintext secrets") and "…" in lines[0]
