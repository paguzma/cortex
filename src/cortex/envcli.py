"""cortex-env -- <comando>: ejecuta un comando con los secretos de la instancia en el entorno."""
import os
import sys

from . import config, secrets


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: cortex-env -- <command> [args...]", file=sys.stderr)
        return 2
    try:
        extra = secrets.resolve_env(config.load())
    except Exception as exc:  # nunca mostrar detalles: podrían traer valores
        print(f"cortex-env: could not resolve secrets ({type(exc).__name__})", file=sys.stderr)
        return 75
    os.execvpe(args[0], args, {**os.environ, **extra})
    return 0  # inalcanzable salvo en pruebas
