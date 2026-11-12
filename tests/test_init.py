import json
from pathlib import Path

from cortex import config, init, paths
from cortex.gitutil import git
from cortex.secrets import INTERNAL, LocalBackend


def test_first_run_prepares_everything(data, capsys):
    assert init.main() == 0
    out = capsys.readouterr().out
    assert "Setup code: " in out
    assert (paths.vault_dir() / "SCHEMA.md").exists()
    assert (paths.plugins_public_dir() / "info" / "refs").exists()
    tokens = LocalBackend(paths.secrets_file()).get_all()
    assert all(tokens.get(k) for k in INTERNAL)
    assert config.load()["session_key"]
    link = Path.home() / ".basic-memory"
    assert link.is_symlink()
    bm = json.loads((link / "config.json").read_text())
    assert bm["projects"]["cortex"]["path"] == str(paths.vault_dir())
    assert bm["auto_update"] is False


def test_second_run_changes_nothing(data, capsys):
    init.main()
    first_code = paths.setup_code_file().read_text()
    tokens = LocalBackend(paths.secrets_file()).get_all()
    vault_head = git(paths.vault_dir(), "rev-parse", "HEAD")
    plugins_head = git(paths.plugins_dir(), "rev-parse", "HEAD")
    init.main()
    assert paths.setup_code_file().read_text() == first_code
    assert LocalBackend(paths.secrets_file()).get_all() == tokens
    assert git(paths.vault_dir(), "rev-parse", "HEAD") == vault_head
    assert git(paths.plugins_dir(), "rev-parse", "HEAD") == plugins_head


def test_claimed_instance_prints_no_code(data, capsys):
    init.main()
    cfg = config.load()
    cfg["admin"]["password_hash"] = "x"
    config.save(cfg)
    capsys.readouterr()
    init.main()
    assert "Setup code" not in capsys.readouterr().out


def test_keeps_existing_bm_settings(data):
    init.main()
    bm_file = paths.state_dir() / "basic-memory" / "config.json"
    data_bm = json.loads(bm_file.read_text())
    data_bm["log_level"] = "DEBUG"
    bm_file.write_text(json.dumps(data_bm))
    init.main()
    assert json.loads(bm_file.read_text())["log_level"] == "DEBUG"
