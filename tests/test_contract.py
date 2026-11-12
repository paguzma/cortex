import json
import shutil

import httpx
import pytest

from cortex import checks, config, contract, init, paths, seed
from cortex.gitutil import git

OLD_SCHEMA = "---\ntitle: SCHEMA\n---\n\n# SCHEMA viejo\n"
REAL_ASSETS = seed.assets()


@pytest.fixture
def old_image(tmp_path, monkeypatch):
    """Una imagen anterior: los assets de hoy con otro SCHEMA.md y sin un template."""
    root = tmp_path / "old-assets"
    shutil.copytree(REAL_ASSETS / "vault", root / "vault")
    (root / "plugin").symlink_to(REAL_ASSETS / "plugin")
    (root / "vault" / "SCHEMA.md").write_text(OLD_SCHEMA)
    (root / "vault" / "_templates" / "tool.md").unlink()

    def use(assets_root):
        monkeypatch.setattr(seed, "assets", lambda: assets_root)
        monkeypatch.setattr(contract, "assets", lambda: assets_root)
    use(root)
    return lambda: use(REAL_ASSETS)


def _boot(capsys) -> str:
    init.main()
    return capsys.readouterr().out


def _legacy_vault(old_image, capsys):
    """Vault sembrado por una imagen vieja que no llevaba registro del contrato."""
    _boot(capsys)
    contract.state_file().unlink()
    old_image()


def test_legacy_vault_gets_new_schema_committed(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    assert (vault / "SCHEMA.md").read_text() == OLD_SCHEMA
    out = _boot(capsys)
    new = (REAL_ASSETS / "vault" / "SCHEMA.md").read_text()
    assert (vault / "SCHEMA.md").read_text() == new
    assert (vault / "_templates" / "tool.md").exists()
    assert "vault contract updated: SCHEMA.md" in out
    assert git(vault, "log", "-1", "--format=%s") == contract.COMMIT_MESSAGE
    assert git(vault, "show", "--name-only", "--format=", "HEAD").split() == [
        "SCHEMA.md", "_templates/tool.md"]
    assert git(vault, "status", "--porcelain") == ""
    assert contract.drift(vault) == []


def test_hand_edited_schema_is_kept_and_reported(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    edited = OLD_SCHEMA + "\nUna regla mía.\n"
    (vault / "SCHEMA.md").write_text(edited)
    git(vault, "commit", "-qam", "mi regla")
    head = git(vault, "rev-parse", "HEAD")
    out = _boot(capsys)
    assert (vault / "SCHEMA.md").read_text() == edited
    assert "SCHEMA.md was edited by hand" in out
    assert git(vault, "log", "-2", "--format=%s").split("\n")[1] == "mi regla"
    assert git(vault, "rev-parse", "HEAD~1") == head
    assert contract.drift(vault) == ["SCHEMA.md"]
    rows = {r["name"]: r for r in checks.health(
        config.load(), http=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200))))}
    assert rows["Vault contract"]["status"] == "warn"
    assert "SCHEMA.md" in rows["Vault contract"]["detail"]


def test_registered_version_is_used_after_the_first_update(data, old_image, capsys):
    _boot(capsys)
    old_image()
    _boot(capsys)
    vault = paths.vault_dir()
    assert (vault / "SCHEMA.md").read_bytes() == (REAL_ASSETS / "vault" / "SCHEMA.md").read_bytes()
    head = git(vault, "rev-parse", "HEAD")
    _boot(capsys)
    assert git(vault, "rev-parse", "HEAD") == head


def test_uncommitted_user_changes_stay_out_of_the_commit(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    (vault / "INDEX.md").write_text("a medio editar\n")
    _boot(capsys)
    assert "INDEX.md" not in git(vault, "show", "--name-only", "--format=", "HEAD")
    assert git(vault, "status", "--porcelain") == "M INDEX.md"


def test_deleted_file_is_not_restored(data, capsys):
    _boot(capsys)
    vault = paths.vault_dir()
    git(vault, "rm", "-q", "BOOTSTRAP.md")
    git(vault, "commit", "-qm", "sin bootstrap")
    out = _boot(capsys)
    assert not (vault / "BOOTSTRAP.md").exists()
    assert "BOOTSTRAP.md was edited by hand" in out


def test_fresh_vault_is_in_sync(data, capsys):
    _boot(capsys)
    assert contract.drift(paths.vault_dir()) == []
    assert set(contract._load()["installed"]) == set(contract.contract_files())


def _basic_memory_rewrite(path, permalink):
    """Lo que hace basic-memory al indexar: permalink en el frontmatter (lo crea si falta)
    y sin salto de línea final."""
    text = path.read_text()
    if text.startswith("---\n"):
        text = text.replace("\n---\n", f"\npermalink: {permalink}\n---\n", 1)
    else:
        text = f"---\ntitle: X\ntype: note\npermalink: {permalink}\n---\n\n{text}"
    path.write_text(text.rstrip("\n"))


def test_basic_memory_rewrites_are_not_edits(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    _basic_memory_rewrite(vault / "SCHEMA.md", "cortex/schema")
    _basic_memory_rewrite(vault / "CLAUDE.md", "cortex/claude")
    git(vault, "commit", "-qam", "snapshot")
    out = _boot(capsys)
    assert "was edited by hand" not in out
    assert (vault / "SCHEMA.md").read_bytes() == (REAL_ASSETS / "vault" / "SCHEMA.md").read_bytes()
    assert contract.drift(vault) == []
    _basic_memory_rewrite(vault / "SCHEMA.md", "cortex/schema")
    assert contract.drift(vault) == []


def test_version_1_registry_with_raw_blob_ids_is_migrated(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    seed_commit = contract._seed_commit(vault)
    raw = {rel: git(vault, "rev-parse", f"{seed_commit}:{rel}") for rel in contract.TOP}
    contract.state_file().write_text(json.dumps({"installed": raw}))
    _basic_memory_rewrite(vault / "SCHEMA.md", "cortex/schema")
    git(vault, "commit", "-qam", "snapshot")
    out = _boot(capsys)
    assert "vault contract updated: SCHEMA.md" in out
    assert contract._load()["version"] == contract.REGISTRY_VERSION


@pytest.fixture
def new_image(tmp_path, monkeypatch):
    """Una imagen nueva: los assets de hoy con los cambios que se pidan, aplicados al texto."""
    def make(changes):
        root = tmp_path / "new-assets"
        shutil.rmtree(root, ignore_errors=True)
        shutil.copytree(REAL_ASSETS / "vault", root / "vault")
        (root / "plugin").symlink_to(REAL_ASSETS / "plugin")
        for rel, change in changes.items():
            path = root / "vault" / rel
            path.write_text(change(path.read_text()))
        monkeypatch.setattr(seed, "assets", lambda: root)
        monkeypatch.setattr(contract, "assets", lambda: root)
    return make


def _add_field(text, field):
    return text.replace("\n---\n", f"\n{field}\n---\n", 1)


def test_frontmatter_edit_is_kept_when_the_image_changes_the_body(data, new_image, capsys):
    _boot(capsys)
    vault = paths.vault_dir()
    template = vault / "_templates" / "project.md"
    edited = _add_field(template.read_text(), 'owner: ""')
    template.write_text(edited)
    git(vault, "commit", "-qam", "mi campo")
    assert contract.drift(vault) == ["_templates/project.md"]
    new_image({"_templates/project.md": lambda t: t + "\nUna sección nueva.\n"})
    out = _boot(capsys)
    assert template.read_text() == edited
    assert "_templates/project.md was edited by hand" in out


def test_frontmatter_added_by_the_image_is_applied(data, new_image, capsys):
    _boot(capsys)
    vault = paths.vault_dir()
    new_image({"_templates/project.md": lambda t: _add_field(t, "tags: []")})
    out = _boot(capsys)
    assert "vault contract updated: _templates/project.md" in out
    assert "tags: []" in (vault / "_templates" / "project.md").read_text()
    assert contract.drift(vault) == []


def test_crlf_and_yaml_formatting_are_not_edits(data, capsys):
    _boot(capsys)
    vault = paths.vault_dir()
    template = vault / "_templates" / "project.md"
    text = template.read_text().replace("aliases: []", "aliases: [ ]")
    template.write_bytes(text.replace("\n", "\r\n").encode())
    assert contract.drift(vault) == []


def test_version_2_registry_with_body_ids_is_migrated(data, old_image, new_image, capsys):
    _boot(capsys)
    old_image()
    _boot(capsys)
    vault = paths.vault_dir()
    assert git(vault, "log", "-1", "--format=%s") == contract.COMMIT_MESSAGE
    v2 = {rel: contract._v2_id((vault / rel).read_bytes()) for rel in contract.contract_files()}
    contract.state_file().write_text(json.dumps({"installed": v2, "version": 2}))
    _basic_memory_rewrite(vault / "SCHEMA.md", "cortex/schema")
    git(vault, "commit", "-qam", "snapshot")
    new_image({"SCHEMA.md": lambda t: t + "\nUna regla nueva.\n",
               "_templates/project.md": lambda t: _add_field(t, "tags: []")})
    out = _boot(capsys)
    assert "vault contract updated: SCHEMA.md" in out
    assert "vault contract updated: _templates/project.md" in out
    assert "edited by hand" not in out
    assert contract._load()["version"] == contract.REGISTRY_VERSION


def test_git_failure_leaves_the_vault_as_it_was_and_retries(data, old_image, capsys):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()
    head = git(vault, "rev-parse", "HEAD")
    lock = vault / ".git" / "index.lock"
    lock.write_text("")
    out = _boot(capsys)
    assert "vault contract not updated" in out
    assert (vault / "SCHEMA.md").read_text() == OLD_SCHEMA
    assert not (vault / "_templates" / "tool.md").exists()
    assert not contract.state_file().exists()
    assert git(vault, "rev-parse", "HEAD") == head
    lock.unlink()
    assert git(vault, "status", "--porcelain") == ""
    out = _boot(capsys)
    assert "vault contract updated: SCHEMA.md" in out
    assert git(vault, "log", "-1", "--format=%s") == contract.COMMIT_MESSAGE
    assert git(vault, "status", "--porcelain") == ""


def test_interrupted_write_keeps_the_previous_file_whole(data, old_image, capsys, monkeypatch):
    _legacy_vault(old_image, capsys)
    vault = paths.vault_dir()

    def cut(src, dst):
        raise OSError("corte")
    monkeypatch.setattr(contract.os, "replace", cut)
    out = _boot(capsys)
    assert "vault contract not updated" in out
    assert (vault / "SCHEMA.md").read_text() == OLD_SCHEMA
    assert not (vault / "_templates" / "tool.md").exists()
    assert not list(vault.rglob("*.tmp"))
    assert git(vault, "status", "--porcelain") == ""


def test_unreadable_registry_is_reported(data, capsys):
    _boot(capsys)
    contract.state_file().write_text('{"installed": {')
    contract.sync(paths.vault_dir())
    assert "is not valid JSON" in capsys.readouterr().err


def test_frontmatter_with_non_string_keys_is_compared(data, new_image, capsys):
    _boot(capsys)
    vault = paths.vault_dir()
    keys = "on: true\n2024: x\nnull: y"
    new_image({"SCHEMA.md": lambda t: _add_field(t, keys)})
    out = _boot(capsys)
    assert "vault contract updated: SCHEMA.md" in out
    assert contract.drift(vault) == []
    edited = _add_field((vault / "SCHEMA.md").read_text(), "2025: z")
    (vault / "SCHEMA.md").write_text(edited)
    git(vault, "commit", "-qam", "mi clave")
    assert contract.drift(vault) == ["SCHEMA.md"]
    new_image({"SCHEMA.md": lambda t: _add_field(t, keys) + "\nOtra regla.\n"})
    out = _boot(capsys)
    assert "SCHEMA.md was edited by hand" in out
    assert (vault / "SCHEMA.md").read_text() == edited


def test_claude_md_keeps_ignoring_basic_memory_title_if_a_new_image_adds_frontmatter(
        data, tmp_path, monkeypatch, capsys):
    """Lo instalado se guardó sin title/type: si una imagen futura trae CLAUDE.md con
    frontmatter, el CLAUDE.md que solo reescribió basic-memory sigue contando como no editado."""
    _boot(capsys)
    vault = paths.vault_dir()
    _basic_memory_rewrite(vault / "CLAUDE.md", "cortex/claude")
    git(vault, "commit", "-qam", "snapshot")
    root = tmp_path / "new-assets"
    shutil.copytree(REAL_ASSETS / "vault", root / "vault")
    (root / "plugin").symlink_to(REAL_ASSETS / "plugin")
    new = "---\ntitle: CLAUDE\ntype: note\n---\n\n# CLAUDE nuevo\n"
    (root / "vault" / "CLAUDE.md").write_text(new)
    monkeypatch.setattr(seed, "assets", lambda: root)
    monkeypatch.setattr(contract, "assets", lambda: root)
    out = _boot(capsys)
    assert "was edited by hand" not in out
    assert (vault / "CLAUDE.md").read_text() == new
    assert contract.drift(vault) == []


def test_bare_covers_every_image_file_without_frontmatter():
    """BARE va fijo para que los ids no dependan de la imagen, pero no puede faltarle uno: un
    archivo sin frontmatter fuera de BARE se vería editado en cuanto basic-memory lo indexe.
    (Al revés sí vale: un archivo de BARE puede ganar frontmatter en una imagen futura.)"""
    plain = {rel for rel in contract.contract_files()
             if contract._split((contract.source() / rel).read_bytes())[0] is None}
    assert plain <= contract.BARE
