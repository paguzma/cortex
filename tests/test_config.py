import json
import stat

from cortex import config, paths


def test_defaults_when_missing(data):
    assert config.load() == config.DEFAULTS
    assert config.load() is not config.DEFAULTS


def test_roundtrip_and_mode(data):
    cfg = config.load()
    cfg["instance"]["name"] = "mi-cortex"
    config.save(cfg)
    assert config.load()["instance"]["name"] == "mi-cortex"
    assert stat.S_IMODE(paths.config_file().stat().st_mode) == 0o600


def test_new_default_keys_appear_in_old_config(data):
    paths.config_dir().mkdir(parents=True)
    paths.config_file().write_text(json.dumps({"instance": {"name": "viejo"}}))
    cfg = config.load()
    assert cfg["instance"]["name"] == "viejo"
    assert cfg["instance"]["timezone"] == "UTC"
    assert cfg["claude"]["model"] == "opus"
