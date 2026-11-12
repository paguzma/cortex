"""cortex-init: prepara /data antes de que arranquen los servicios. Es idempotente."""
import json
import shutil
from datetime import date
from pathlib import Path

from . import auth, config, contract, paths, seed
from .gitutil import init_repo
from .marketplace import Market
from .routines.schedule import write_crontab
from .secrets import INTERNAL, LocalBackend

DEFAULT_BASE_URL = "http://localhost:8700"


def write_bm_config(home: Path) -> None:
    """Config de basic-memory dentro de /data/state, enlazada desde ~/.basic-memory."""
    directory = paths.state_dir() / "basic-memory"
    directory.mkdir(parents=True, exist_ok=True)
    cfg_file = directory / "config.json"
    data = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
    data.update({
        "projects": {"cortex": {"path": str(paths.vault_dir()), "mode": "local"}},
        "default_project": "cortex",
        "auto_update": False,
        "semantic_embedding_cache_dir": str(paths.state_dir() / "fastembed"),
    })
    cfg_file.write_text(json.dumps(data, indent=2) + "\n")
    link = home / ".basic-memory"
    if link.is_symlink():
        if link.resolve() == directory.resolve():
            return
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.symlink_to(directory)


def main() -> int:
    for directory in (paths.config_dir(), paths.state_dir(), paths.logs_dir(),
                      paths.data_dir() / "archive"):
        directory.mkdir(parents=True, exist_ok=True)

    cfg = config.load()
    if not cfg["session_key"]:
        cfg["session_key"] = auth.new_token()
        config.save(cfg)

    local = LocalBackend(paths.secrets_file())
    existing = local.get_all()
    for key in INTERNAL:
        if not existing.get(key):
            local.set(key, auth.new_token())

    seed.seed_vault(paths.vault_dir(), date.today().isoformat())
    try:
        synced = contract.sync(paths.vault_dir())
    except Exception as exc:  # el contrato no debe impedir que arranque el resto
        print(f"cortex: vault contract not updated, will retry on next start: {exc}",
              flush=True)
        synced = {"updated": [], "added": [], "edited": []}
    for rel in synced["updated"] + synced["added"]:
        print(f"cortex: vault contract updated: {rel}", flush=True)
    for rel in synced["edited"]:
        print(f"cortex: {rel} was edited by hand and differs from the image; left as is",
              flush=True)
    base_url = cfg["instance"]["base_url"] or DEFAULT_BASE_URL
    market = Market(paths.plugins_dir(), paths.plugins_public_dir())
    market.seed()
    market.sync_base(seed.assets() / "plugin", base_url)
    market.publish()
    write_bm_config(Path.home())
    archive = paths.data_dir() / "archive"
    if not (archive / ".git").exists():
        init_repo(archive)
    write_crontab(cfg)

    if not cfg["admin"]["password_hash"]:
        code = auth.ensure_setup_code(paths.setup_code_file())
        print(f"cortex: instance not claimed. Setup code: {code}", flush=True)
        print(f"cortex: open {base_url}/setup to finish the setup", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
