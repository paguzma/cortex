"""Consolidación nocturna: el agente reorganiza, el validador determinista decide."""
import time

from .. import seed
from ..domains import consolidate_pass
from . import structure
from .base import Context, Result

ALLOWED_TOOLS = ("Read", "Glob", "Grep", "mcp__cortex")
TIMEOUT = 3600
# Cuántas violaciones ve el agente: con miles, el prompt crece sin límite y el agente
# no las arregla todas en una noche de todos modos.
MAX_VIOLATIONS = 300


def prompt(ctx: Context) -> str:
    template = (seed.assets() / "routines" / "consolidate.md").read_text()
    # Los secretos no van al prompt: el agente no debe ir a abrir esas notas.
    before = structure.render(ctx.vault, structure.check(ctx.vault), limit=MAX_VIOLATIONS,
                              secrets=False)
    return template.replace("{{STRUCTURE}}", before)


def run(ctx: Context) -> Result:
    started = time.monotonic()
    rc, output = ctx.claude(prompt(ctx), model=ctx.model, env=ctx.env, cwd=ctx.vault,
                            allowed_tools=ALLOWED_TOOLS, mcp=True, timeout=TIMEOUT)
    seconds = int(time.monotonic() - started)
    if rc != 0 or not output.strip():
        return Result("fail", f"the agent failed (exit {rc}) · {seconds}s", output[-8000:])

    # Dominios: solo escribe area: en notas que no lo tenían, y lo verifica.
    try:
        domains_summary, domains_detail = consolidate_pass.run(ctx)
    except Exception as exc:  # noqa: BLE001 — un paso de dominios fallido no tapa la consolidación
        domains_summary, domains_detail = f"domains failed: {type(exc).__name__}", str(exc)

    # La verificación independiente: no le creemos al reporte del propio agente.
    problems = structure.check(ctx.vault)
    verdict = structure.render(ctx.vault, problems)
    detail = (f"{output}\n\n--- domains ---\n{domains_summary}\n{domains_detail}"
              f"\n\n--- independent check ---\n{verdict}")
    if problems:
        return Result("fail", f"REVIEW: {structure.summary_line(ctx.vault, problems)}"
                              f" · {domains_summary}", detail)
    return Result("ok", f"vault healthy · {structure.note_count(ctx.vault)} notes · {seconds}s"
                        f" · {domains_summary}", detail)
