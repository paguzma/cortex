import io
import json
import shutil
import subprocess
import zipfile

import pytest

from cortex.gitutil import git
from cortex.marketplace import BASE, USER, Market, SkillError, validate_skill_md
from cortex.seed import assets

SKILL = "---\nname: {n}\ndescription: Hace algo útil\n---\n\n# {n}\n"


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


@pytest.fixture
def market(tmp_path):
    m = Market(tmp_path / "plugins", tmp_path / "public.git")
    m.seed()
    m.sync_base(assets() / "plugin", "http://cortex.local:8700")
    return m


ASSET_VERSION = json.loads((assets() / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"]


def bumped(v: str, n: int = 1) -> str:
    major, minor, patch = v.split(".")
    return f"{major}.{minor}.{int(patch) + n}"


def version(m: Market, plugin: str) -> str:
    return json.loads((m.repo / "plugins" / plugin / ".claude-plugin" / "plugin.json").read_text())["version"]


def test_seed_and_initial_sync(market):
    manifest = json.loads((market.repo / ".claude-plugin" / "marketplace.json").read_text())
    assert [p["name"] for p in manifest["plugins"]] == [BASE, USER]
    mcp = json.loads((market.base_dir / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cortex"]["url"] == "http://cortex.local:8700/mcp"
    assert "${CORTEX_MCP_TOKEN}" in mcp["mcpServers"]["cortex"]["headers"]["Authorization"]
    assert version(market, BASE) == ASSET_VERSION
    assert {s["name"] for s in market.list_skills()} == {"mem-search", "mem-save", "mem-close"}
    assert market.seed() is False


def test_sync_is_idempotent(market):
    assert market.sync_base(assets() / "plugin", "http://cortex.local:8700") is False


def test_url_change_bumps_version(market):
    assert market.sync_base(assets() / "plugin", "https://otra.example") is True
    assert version(market, BASE) == bumped(ASSET_VERSION)


def test_new_assets_after_url_bump_still_go_up(market, tmp_path):
    market.sync_base(assets() / "plugin", "https://otra.example")  # publicado: asset + 1
    newer = tmp_path / "assets-plugin"
    shutil.copytree(assets() / "plugin", newer)
    pj = newer / ".claude-plugin" / "plugin.json"
    data = json.loads(pj.read_text())
    data["version"] = bumped(ASSET_VERSION)
    pj.write_text(json.dumps(data))
    assert market.sync_base(newer, "https://otra.example") is True
    assert version(market, BASE) == bumped(ASSET_VERSION, 2)
    assert (market.base_dir / ".asset-version").read_text().strip() == bumped(ASSET_VERSION)


@pytest.mark.parametrize("files", [
    {"SKILL.md": SKILL.format(n="saludo"), "ref/a.txt": "x"},
    {"saludo/SKILL.md": SKILL.format(n="saludo"), "saludo/ref/a.txt": "x",
     "__MACOSX/saludo/._SKILL.md": "basura"},
])
def test_add_skill_zip(market, files):
    assert market.add_skill_zip(make_zip(files)) == "saludo"
    dest = market.user_dir / "skills" / "saludo"
    assert (dest / "SKILL.md").exists() and (dest / "ref" / "a.txt").exists()
    assert not (market.user_dir / "skills" / "__MACOSX").exists()
    assert version(market, USER) == "0.1.1"


@pytest.mark.parametrize("files, error", [
    ({"../evil/SKILL.md": SKILL.format(n="x")}, "unsafe"),
    ({"README.md": "hola"}, "SKILL.md not found"),
    ({"SKILL.md": "sin frontmatter"}, "frontmatter"),
    ({"SKILL.md": "---\nname: Mala_Nombre\ndescription: x\n---\n"}, "lowercase"),
    ({"SKILL.md": "---\nname: ok\n---\n"}, "description"),
])
def test_add_skill_zip_rejects(market, files, error):
    with pytest.raises(SkillError, match=error):
        market.add_skill_zip(make_zip(files))


def test_rejects_non_zip(market):
    with pytest.raises(SkillError, match="zip"):
        market.add_skill_zip(b"no soy un zip")


def test_create_edit_delete(market):
    market.save_skill_md("nota", SKILL.format(n="nota"), create=True)
    assert "nota" in market.read_skill_md("nota")
    market.save_skill_md("nota", SKILL.format(n="nota") + "más\n")
    assert market.read_skill_md("nota").endswith("más\n")
    with pytest.raises(SkillError):
        market.save_skill_md("nota", SKILL.format(n="otra"))
    with pytest.raises(SkillError):
        market.save_skill_md("no-existe", SKILL.format(n="no-existe"))
    market.delete_skill("nota")
    with pytest.raises(SkillError):
        market.read_skill_md("nota")
    with pytest.raises(SkillError):
        market.read_skill_md("../../etc")


def test_base_skills_not_editable(market):
    with pytest.raises(SkillError):
        market.read_skill_md("mem-search")
    assert all(not s["editable"] for s in market.list_skills() if s["plugin"] == BASE)


def test_publish_serves_latest_commit(market, tmp_path):
    market.add_skill_zip(make_zip({"SKILL.md": SKILL.format(n="saludo")}))
    assert (market.public / "info" / "refs").exists()
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(market.public), str(clone)], check=True)
    assert (clone / "plugins" / USER / "skills" / "saludo" / "SKILL.md").exists()
    assert git(clone, "rev-parse", "HEAD") == git(market.repo, "rev-parse", "HEAD")


def test_validate_skill_md_expected_name():
    assert validate_skill_md(SKILL.format(n="a")) == "a"
    with pytest.raises(SkillError):
        validate_skill_md(SKILL.format(n="a"), expected_name="b")


def test_concurrent_saves_do_not_corrupt_plugin_json(market):
    # Un doble clic en «Save» lanza dos escrituras en hilos a la vez.
    from concurrent.futures import ThreadPoolExecutor
    market.save_skill_md("nota", SKILL.format(n="nota"), create=True)
    start = version(market, USER)
    with ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(market.save_skill_md, "nota", SKILL.format(n="nota") + f"v{i}\n")
                   for i in range(20)]
    errors = [f.exception() for f in futures if f.exception()]
    assert errors == []
    major, minor, patch = start.split(".")
    assert version(market, USER) == f"{major}.{minor}.{int(patch) + 20}"
    assert git(market.repo, "status", "--porcelain").strip() == ""


def test_version_write_is_atomic(market, monkeypatch):
    # Si la escritura se corta, plugin.json conserva la versión anterior entera.
    from cortex import marketplace
    plugin_json = market.user_dir / ".claude-plugin" / "plugin.json"
    before = plugin_json.read_text()

    def boom(*a, **kw):
        raise OSError("disco lleno")
    monkeypatch.setattr(marketplace.os, "replace", boom)
    with pytest.raises(OSError):
        marketplace.bump_patch(plugin_json)
    assert plugin_json.read_text() == before


# Huella del plugin base por versión. sync_base solo publica si sube la versión del
# asset: cambiar una skill sin subirla deja a los clientes con la vieja para siempre.
# Al cambiar algo en assets/plugin, sube la versión y agrega aquí su huella.
PLUGIN_FINGERPRINTS = {
    "0.1.0": "8fdde767a946",
    "0.1.1": "0757cb21b2d2",
    "0.1.3": "cf5ca8101c3b",
    "0.1.4": "8778db9c822b",
    "0.1.5": "f32c9a73b78e",
}


def _plugin_fingerprint() -> str:
    import hashlib
    root = assets() / "plugin"
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == ".claude-plugin/plugin.json" or path.name == ".DS_Store" \
                or "__pycache__" in path.parts:
            continue
        digest.update(rel.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def test_plugin_assets_change_only_with_a_version_bump():
    current = json.loads((assets() / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert PLUGIN_FINGERPRINTS.get(current) == _plugin_fingerprint(), (
        f"assets/plugin cambió sin subir su versión ({current}): súbela y registra la huella")
