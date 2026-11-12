"""cortex-routine <name>: lo que corre cada línea del crontab (y el botón «Run now»)."""
import fcntl
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .. import checks, config, paths
from ..secrets import resolve_env
from . import consolidate, report, snapshot
from .base import Context, Result
from .claude import run_claude
from .schedule import validate_schedule


def _clean(ctx: Context) -> Result:
    from .clean import run
    return run(ctx)


@dataclass(frozen=True)
class Spec:
    run: Callable[[Context], Result]
    needs_claude: bool


ROUTINES: dict[str, Spec] = {
    "consolidate": Spec(consolidate.run, True),
    "clean": Spec(_clean, True),
    "snapshot": Spec(snapshot.run, False),
}


def effective_model(cfg: dict, name: str) -> str:
    return cfg["routines"][name].get("model") or cfg["claude"]["model"] or "opus"


def routine_state(name: str, cfg: dict, env: dict) -> str:
    if not cfg["routines"][name]["enabled"]:
        return "disabled"
    if ROUTINES[name].needs_claude and not checks.claude_credential(env):
        return "no-credential"
    return "ready"


def scheduled_state(name: str, cfg: dict, env: dict) -> str:
    """El estado de routine_state visto desde el crontab: una rutina que correría pero cuyo
    horario no pasa la validación quedó fuera del crontab, así que no corre sola."""
    state = routine_state(name, cfg, env)
    if state == "ready" and not validate_schedule(cfg["routines"][name]["schedule"]):
        return "invalid-schedule"
    return state


def routines_dir() -> Path:
    directory = paths.state_dir() / "routines"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def running_file() -> Path:
    return routines_dir() / "running.json"


def current_run() -> dict | None:
    try:
        data = json.loads(running_file().read_text())
    except (OSError, ValueError):
        return None
    try:
        os.kill(data["pid"], 0)
    except (OSError, KeyError, TypeError):
        return None
    return data


def _execute(name: str, cfg: dict, claude) -> Result | None:
    spec = ROUTINES[name]
    try:
        env = resolve_env(cfg)
    except Exception as exc:
        if spec.needs_claude:
            return Result("fail", f"could not read the secrets ({type(exc).__name__})")
        env = {}
    state = routine_state(name, cfg, env)
    if state != "ready":
        print(f"cortex-routine {name}: skipped ({state})", flush=True)
        return None
    own_state = routines_dir() / name
    own_state.mkdir(parents=True, exist_ok=True)
    ctx = Context(cfg=cfg, env=env, vault=paths.vault_dir(), archive=paths.data_dir() / "archive",
                  state=own_state, model=effective_model(cfg, name), claude=claude)
    try:
        result = spec.run(ctx)
    except Exception as exc:
        result = Result("fail", f"unexpected error: {type(exc).__name__}: {str(exc)[:120]}",
                        traceback.format_exc())
    if name != "snapshot":
        try:
            snap = snapshot.snapshot(ctx, f"chore(vault): snapshot after {name} {date.today()}")
            result = Result(result.status, f"{result.summary} · {snap.summary}", result.detail)
        except Exception as exc:
            result = Result("fail", f"{result.summary} · snapshot failed: {type(exc).__name__}",
                            result.detail + "\n\n" + traceback.format_exc())
    return result


def run_routine(name: str, claude=run_claude) -> dict | None:
    if name not in ROUTINES:
        raise KeyError(name)
    lock = (routines_dir() / "lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # nunca dos rutinas a la vez
    try:
        cfg = config.load()
        running_file().write_text(json.dumps(
            {"routine": name, "pid": os.getpid(),
             "since": datetime.now().astimezone().isoformat(timespec="seconds")}))
        result = _execute(name, cfg, claude)
        return None if result is None else report.write_report(name, result, cfg)
    finally:
        running_file().unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ROUTINES:
        print(f"usage: cortex-routine <{'|'.join(ROUTINES)}>", file=sys.stderr)
        return 2
    written = run_routine(args[0])
    if written:
        print(f"cortex-routine {args[0]}: {written['status']} · {written['summary']}", flush=True)
        return 0 if written["status"] == "ok" else 1
    return 0
