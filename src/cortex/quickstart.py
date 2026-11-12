"""cortex-quickstart: deja lista una instancia nueva sin pasar por la UI.

Pensado para que un agente instale cortex por alguien que no es técnico:

    docker exec cortex cortex-quickstart --timezone America/Santiago

Reclama la instancia con una contraseña de admin generada, configura nombre, zona horaria y
URL, salta la credencial de Claude y el respaldo git (se configuran después en /setup) y
imprime un JSON con la contraseña y el bloque para ~/.claude/settings.json. Volver a correrlo
no cambia la contraseña: solo aplica las opciones que se pasen y vuelve a imprimir el bloque.
"""
import argparse
import json
import secrets as pysecrets
import sys
import zoneinfo

from . import auth, config, paths, seed, services
from .marketplace import Market
from .secrets import LocalBackend

DEFAULT_BASE_URL = "http://localhost:8700"


def client_settings(base_url: str, mcp_token: str, plugins_token: str) -> dict:
    # Importado aquí: la UI arrastra Starlette y plantillas que este comando no necesita.
    from .web.app import client_settings as settings
    return settings(base_url, mcp_token, plugins_token)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cortex-quickstart", description=__doc__.splitlines()[0])
    parser.add_argument("--name", help="nombre de la instancia (por defecto: cortex)")
    parser.add_argument("--timezone", help="zona horaria IANA, p. ej. America/Santiago (por defecto: UTC)")
    parser.add_argument("--base-url", help=f"URL con que los clientes llegan a cortex (por defecto: {DEFAULT_BASE_URL})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    cfg = config.load()
    first_run = not cfg["admin"]["password_hash"]

    timezone = args.timezone or (None if not first_run else cfg["instance"]["timezone"])
    if timezone is not None:
        try:
            zoneinfo.ZoneInfo(timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            print(f"cortex-quickstart: unknown time zone: {timezone!r}", file=sys.stderr)
            return 2
    base_url = args.base_url or (None if cfg["instance"]["base_url"] else DEFAULT_BASE_URL)
    if base_url is not None:
        base_url = base_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            print("cortex-quickstart: --base-url must start with http:// or https://", file=sys.stderr)
            return 2

    tokens = LocalBackend(paths.secrets_file()).get_all()
    if not tokens.get("CORTEX_MCP_TOKEN") or not tokens.get("CORTEX_PLUGINS_TOKEN"):
        print("cortex-quickstart: the instance is not initialized yet; wait for the container "
              "to finish starting and try again", file=sys.stderr)
        return 75

    password = None
    if first_run:
        password = pysecrets.token_urlsafe(18)
        cfg["admin"]["password_hash"] = auth.hash_password(password)
        paths.setup_code_file().unlink(missing_ok=True)

    timezone_changed = timezone is not None and timezone != cfg["instance"]["timezone"]
    if args.name:
        cfg["instance"]["name"] = args.name
    if timezone is not None:
        cfg["instance"]["timezone"] = timezone
    if base_url is not None:
        cfg["instance"]["base_url"] = base_url
    cfg["setup_complete"] = True
    config.save(cfg)

    if timezone_changed:
        services.restart("supercronic")
    if base_url is not None:
        Market(paths.plugins_dir(), paths.plugins_public_dir()).sync_base(
            seed.assets() / "plugin", cfg["instance"]["base_url"])

    print(json.dumps({
        "admin_password": password,
        "setup_url": cfg["instance"]["base_url"] + "/setup",
        "settings": client_settings(cfg["instance"]["base_url"], tokens["CORTEX_MCP_TOKEN"],
                                    tokens["CORTEX_PLUGINS_TOKEN"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
