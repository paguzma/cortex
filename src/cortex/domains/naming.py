"""Lo único que hace el modelo con los dominios: ponerle nombre a cada grupo."""
import json
import re

from ..agentos.graph import summary
from .assign import valid_name
from .cluster import Group

MAX_NOTES_SHOWN = 12


def prompt(groups: list[Group], notes: dict[str, dict], existing: dict[str, int]) -> str:
    lines = [
        "Eres el que nombra los dominios de una memoria personal en notas markdown.",
        "Un dominio es un área grande de la vida o el trabajo de su dueño (por ejemplo:",
        "casa, trabajo, finanzas, salud). Abajo hay grupos de notas que el grafo de enlaces",
        "juntó y que todavía no tienen dominio.",
        "",
        "Para cada grupo decide:",
        "- si pertenece a un dominio que ya existe: usa ese nombre exacto;",
        "- si es un área nueva: inventa un nombre corto, en minúsculas, una o dos palabras",
        "  unidas por guion (ej. `casa`, `salud-familia`). Nunca `general`.",
        "- si el grupo no tiene un tema común claro, omítelo.",
        "Prefiere los dominios existentes: pocos dominios grandes son mejores que muchos chicos.",
        "",
        "Dominios existentes (notas):",
    ]
    lines += [f"- {name} ({count})" for name, count in sorted(existing.items())] or ["- (ninguno todavía)"]
    for group in groups:
        lines += ["", f"## Grupo {group.id}" + (f" (su vecindario ya es «{group.fits}»)" if group.fits else "")]
        for stem in group.notes[:MAX_NOTES_SHOWN]:
            note = notes[stem]
            title = note["meta"].get("title") or stem
            lines.append(f"- {title}: {summary(note['body'])[:140]}")
        if len(group.notes) > MAX_NOTES_SHOWN:
            lines.append(f"- … y {len(group.notes) - MAX_NOTES_SHOWN} notas más")
    lines += ["", "Responde SOLO con un arreglo JSON, sin texto alrededor:",
              '[{"group": "<id del grupo>", "domain": "<nombre>"}]']
    return "\n".join(lines)


def parse(output: str, group_ids: set[str]) -> dict[str, str]:
    """Grupo -> dominio, solo con entradas válidas. Cualquier otra cosa se ignora."""
    match = re.search(r"\[.*\]", output, re.S)
    if not match:
        return {}
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return {}
    out = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        group, domain = str(item.get("group", "")), str(item.get("domain", "")).strip().lower()
        if group in group_ids and valid_name(domain):
            out[group] = domain
    return out
