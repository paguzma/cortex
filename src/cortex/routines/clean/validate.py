"""validate.py — el que decide si una limpieza se aplica. Sin modelo.

Garantiza que no se pierde ni se inventa ningún dato concreto, que lo estructural
no cambia y que en las vecinas solo se tacha. NO garantiza que una fusión conserve
cada matiz: contra eso, pocas notas por noche, las propuestas guardadas y git."""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from .notes import facts, section, split_note

FIXED = ("type", "created", "permalink")
EDITABLE = ("title", "aliases", "status", "updated")   # lo único del frontmatter que puede cambiar
_MISSING = object()
RX_RELATIONS = re.compile(r"(?m)^## Relaciones[ \t]*\n.*?(?=^## |\Z)", re.S)
RX_RELATIONS_HEADING = re.compile(r"(?m)^## Relaciones[ \t]*$")
# Ítem de lista (también dentro de una cita). No imitamos la gramática de basic-memory:
# se decide por exclusión en _looks_like_relation.
RX_LIST_ITEM = re.compile(r"^[\s>]*(?:[-*+]|\d+[.)])\s+")
RX_PREFIX = re.compile(r"^\s*(?:- )?")
MAX_LISTED = 10
RX_NEIGHBOR_STRIKE = re.compile(r"^(\s*(?:- )?)~~(.+)~~ ← superseded \d{4}-\d{2}-\d{2}: (.+)$")


@dataclass
class Verdict:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)

    def error(self, text: str) -> None:
        self.ok = False
        self.errors.append(text)


def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _sample(items_set: set[str]) -> str:
    items = sorted(items_set)
    extra = f" (+{len(items) - MAX_LISTED} more)" if len(items) > MAX_LISTED else ""
    return ", ".join(items[:MAX_LISTED]) + extra


def _outside_relations(body: str) -> list[str]:
    """Las líneas del cuerpo que no están en la primera sección ## Relaciones."""
    m = RX_RELATIONS.search(body)
    rest = body[:m.start()] + body[m.end():] if m else body
    return rest.split("\n")


def _looks_like_relation(line: str) -> bool:
    """Ítem de lista con [[ cuyo texto previo no es claramente una frase: un solo token,
    vacío, o algo que empieza con comillas. Ante la duda, es relación."""
    m = RX_LIST_ITEM.match(line)
    if not m or "[[" not in line[m.end():]:
        return False
    before = line[m.end():].split("[[", 1)[0].strip()
    is_sentence = any(c.isspace() for c in before) and not before.startswith(('"', "'"))
    return not is_sentence


def _editable_text(fm: dict) -> str:
    """title, aliases y status como texto, para buscarles datos concretos."""
    return "\n".join([str(fm.get("title", "")), *_as_list(fm.get("aliases")), str(fm.get("status", ""))])


def validate_target(base: str, candidate: str) -> Verdict:
    """base = la nota ya sin tachados (split.separate); candidate = lo que devolvió el modelo.

    Falla cerrado: lo que no puede comprobar, lo rechaza."""
    v = Verdict()
    fm_b, raw_b, body_b = split_note(base)
    fm_c, raw_c, body_c = split_note(candidate)
    if not fm_b or any(k not in fm_b for k in FIXED):
        v.error("the original note has no readable frontmatter (type/created/permalink)")
        return v
    if not raw_c:
        v.error("the candidate has no frontmatter")
        return v
    for k in sorted(set(fm_b) | set(fm_c), key=str):
        if k in EDITABLE:
            continue
        if fm_b.get(k, _MISSING) != fm_c.get(k, _MISSING):
            v.error(f"changed frontmatter key `{k}`")
    if "updated" in fm_b and "updated" not in fm_c:
        v.error("removed frontmatter key `updated`")
    if "updated" in fm_c and not _is_iso_date(fm_c["updated"]):
        v.error(f"`updated` is not a YYYY-MM-DD date: {fm_c['updated']!r}")
    if section(body_b, "Relaciones") != section(body_c, "Relaciones"):
        v.error("changed the ## Relaciones section")
    nb, nc = len(RX_RELATIONS_HEADING.findall(body_b)), len(RX_RELATIONS_HEADING.findall(body_c))
    if nb != nc:
        v.error(f"changed the number of ## Relaciones sections: {nb} → {nc}")
    outside_b = set(_outside_relations(body_b))
    for line in _outside_relations(body_c):
        if line not in outside_b and _looks_like_relation(line):
            v.error(f"possible new relation outside ## Relaciones: {line.strip()}")
    fb, fc = facts(body_b), facts(body_c)
    if fb - fc:
        v.error(f"lost data: {_sample(fb - fc)}")
    invented = (fc | facts(_editable_text(fm_c))) - fb - facts(raw_b)
    if invented:
        v.error(f"invented data: {_sample(invented)}")
    sb, sc = len(base.encode()), len(candidate.encode())
    if sc > sb:
        v.error(f"grew: {sb} → {sc} bytes")
    if fm_b.get("title") != fm_c.get("title"):
        v.changes.append(f"title: «{fm_b.get('title')}» → «{fm_c.get('title')}»")
    if fm_b.get("status") != fm_c.get("status"):
        v.changes.append(f"status: {fm_b.get('status')} → {fm_c.get('status')}")
    ab, ac = _as_list(fm_b.get("aliases")), _as_list(fm_c.get("aliases"))
    v.changes += [f"removed alias `{a}`" for a in ab if a not in ac]
    v.changes += [f"added alias `{a}`" for a in ac if a not in ab]
    return v


def _real_date(text: str) -> bool:
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_iso_date(value) -> bool:
    """`updated` con o sin comillas: exactamente AAAA-MM-DD y una fecha real."""
    text = str(value)
    return _real_date(text[:10]) and text == text[:10]


def _strike_problem(a: str, b: str, slug: str, target_facts: frozenset[str]) -> str | None:
    """None si «b» es un tachado válido de la línea original «a»; si no, por qué no."""
    if not a.strip():
        return "struck a blank line"
    if re.match(r"^\s*#", a):
        return "struck a heading"
    m = RX_NEIGHBOR_STRIKE.match(b)
    if not m or m.group(1) + m.group(2) != a:
        return "not a valid strikethrough of the original"
    if m.group(1) != RX_PREFIX.match(a).group(0) or (
            a.lstrip().startswith("- ") and m.group(2).startswith("- ")):
        return "the list prefix ended up inside the strikethrough"
    if not _real_date(re.search(r"\d{4}-\d{2}-\d{2}", b[m.end(2):m.start(3)]).group(0)):
        return "the strikethrough date is not a real date"
    reason = m.group(3)
    if f"[[{slug}]]" not in reason:
        return f"the reason does not link to [[{slug}]]"
    if "~~" in reason or "\n" in reason or "\r" in reason:
        return "the reason has ~~ or line breaks"
    new = facts(reason) - {f"wikilink:{slug}"} - facts(a) - set(target_facts)
    if new:
        return f"the reason invented data: {_sample(new)}"
    return None


def validate_neighbor(original: str, new: str, slug: str,
                      target_facts: frozenset[str] = frozenset()) -> Verdict:
    """En una vecina solo se tacha: cada línea cambiada es la original envuelta en ~~ ~~.

    target_facts = facts(<nota objetivo>): el motivo puede citarlos; nada más que eso,
    lo que ya decía la línea tachada y el enlace al objetivo."""
    v = Verdict()
    if "\r" in original or (original.startswith("---\n") and not split_note(original)[1]):
        v.error("unrecognizable frontmatter")
        return v
    lo, ln = original.split("\n"), new.split("\n")
    if len(lo) != len(ln):
        v.error(f"changed the line count: {len(lo)} → {len(ln)}")
        return v
    fm_end = split_note(original)[1].count("\n")
    for i, (a, b) in enumerate(zip(lo, ln)):
        if a == b:
            continue
        if i < fm_end:
            v.error(f"line {i + 1}: touched the frontmatter")
            continue
        problem = _strike_problem(a, b, slug, target_facts)
        if problem:
            v.error(f"line {i + 1}: {problem}")
        else:
            v.changes.append(f"struck: {a.strip()} — reason: {RX_NEIGHBOR_STRIKE.match(b).group(3)}")
    return v


def validate_history(old: str | None, new: str) -> Verdict:
    v = Verdict()
    if old and not new.startswith(old.rstrip("\n") + "\n"):
        v.error("the history did not only grow: previous content changed")
    return v
