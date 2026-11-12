"""Crontab de supercronic generado desde config.json."""
import re
import sys
from pathlib import Path

from .. import paths

# Rango de cada campo: minuto, hora, día del mes, mes, día de la semana. supercronic
# muere con una sola línea fuera de rango y se lleva consigo todas las rutinas. Los límites
# están medidos contra supercronic v0.2.33 (`supercronic -test`): el paso no pasa del
# máximo del campo, y el 7 vale como domingo solo, no como fin de un rango.
RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
ITEM_RE = re.compile(r"^(\*|(\d+)(?:-(\d+))?)(?:/(\d+))?$")
RUNNER = "/usr/local/bin/cortex-routine"


def _valid_item(item: str, lo: int, hi: int) -> bool:
    m = ITEM_RE.match(item)
    if not m:
        return False
    _, start, end, step = m.groups()
    if step is not None and not 1 <= int(step) <= hi:
        return False
    if item == "7" and hi == 6:
        return True
    if start is None:
        return True
    a = int(start)
    b = int(end) if end is not None else a
    return lo <= a <= b <= hi


def validate_schedule(expr: str) -> bool:
    fields = expr.strip().split(" ")
    if len(fields) != len(RANGES):
        return False
    return all(_valid_item(item, lo, hi)
               for field, (lo, hi) in zip(fields, RANGES)
               for item in field.split(","))


def crontab_file() -> Path:
    return paths.state_dir() / "routines" / "crontab"


def invalid_schedules(cfg: dict) -> list[str]:
    """Rutinas encendidas cuyo horario no pasa la validación (p. ej. uno guardado con un
    validador anterior): quedan fuera del crontab, así que no corren."""
    return [name for name, routine in cfg["routines"].items()
            if routine["enabled"] and not validate_schedule(routine["schedule"])]


def render_crontab(cfg: dict) -> str:
    lines = ["# Generado por cortex desde config.json: se reescribe en cada cambio."]
    invalid = invalid_schedules(cfg)
    for name, routine in cfg["routines"].items():
        if name in invalid:
            lines.append(f"# {name}: horario inválido {routine['schedule']!r}, no se programa")
            print(f"cortex: routine {name} not scheduled: invalid schedule {routine['schedule']!r}",
                  file=sys.stderr, flush=True)
        elif routine["enabled"]:
            lines.append(f"{routine['schedule'].strip()} {RUNNER} {name}")
    return "\n".join(lines) + "\n"


def write_crontab(cfg: dict) -> Path:
    target = crontab_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_crontab(cfg)
    if not target.exists() or target.read_text() != text:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(target)
    return target
