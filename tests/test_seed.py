from cortex.gitutil import git
from cortex.seed import seed_vault


def test_seeds_empty_vault(tmp_path):
    vault = tmp_path / "vault"
    assert seed_vault(vault, "2026-10-08") is True
    for name in ("AGENTS.md", "CLAUDE.md", "SCHEMA.md", "BOOTSTRAP.md", "INDEX.md"):
        assert (vault / name).exists()
    assert (vault / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
    assert (vault / "feedback").is_dir()
    assert len(list((vault / "_templates").glob("*.md"))) == 8
    assert "2026-10-08" in (vault / "INDEX.md").read_text()
    assert "{{TODAY}}" not in (vault / "INDEX.md").read_text()
    assert git(vault, "log", "--format=%s") == "chore: vault inicial de cortex"


def test_does_not_touch_existing_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "mia.md").write_text("mía")
    assert seed_vault(vault, "2026-10-08") is False
    assert not (vault / "SCHEMA.md").exists()


def test_idempotent(tmp_path):
    vault = tmp_path / "vault"
    seed_vault(vault, "2026-10-08")
    assert seed_vault(vault, "2026-10-09") is False
    assert git(vault, "rev-list", "--count", "HEAD") == "1"


def test_schema_mentions_area_and_no_private_paths(tmp_path):
    vault = tmp_path / "vault"
    seed_vault(vault, "2026-10-08")
    schema = (vault / "SCHEMA.md").read_text()
    assert "area:" in schema
    assert "vault42" not in schema and "/opt/" not in schema
