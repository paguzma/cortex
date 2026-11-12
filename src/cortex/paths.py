"""Ubicaciones dentro de /data. Se leen en cada llamada para respetar CORTEX_DATA."""
import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("CORTEX_DATA", "/data"))


def vault_dir() -> Path:
    return data_dir() / "vault"


def plugins_dir() -> Path:
    return data_dir() / "plugins"


def config_dir() -> Path:
    return data_dir() / "config"


def config_file() -> Path:
    return config_dir() / "config.json"


def secrets_file() -> Path:
    return config_dir() / "secrets.env"


def deploy_key_file() -> Path:
    return config_dir() / "deploy_key"


def known_hosts_file() -> Path:
    return config_dir() / "known_hosts"


def state_dir() -> Path:
    return data_dir() / "state"


def logs_dir() -> Path:
    return state_dir() / "logs"


def plugins_public_dir() -> Path:
    return state_dir() / "plugins.git"


def setup_code_file() -> Path:
    return state_dir() / "setup-code"
