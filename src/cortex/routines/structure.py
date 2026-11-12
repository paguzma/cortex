"""Validador determinista del contrato de SCHEMA.md.

No se puede impedir que un agente escriba fuera de contrato: cualquiera con el MCP puede
llamar write_note. Esto es la mitad detectiva: corre después de cada consolidación y lo
que reporta es la verdad sobre la salud del vault, no lo que el agente dice de sí mismo.
"""
import collections
import hashlib
import re
from pathlib import Path

from . import secret_scan

TYPE_BY_FOLDER = {
    "entities/people": "person",
    "entities/projects": "project",
    "entities/infra": "infra",
    "entities/tools": "tool",
    "decisions": "decision",
    "feedback": "feedback",
    "references": "reference",
    "episodes": "episode",
}
REQUIRED = ("title", "type", "status", "created", "updated")
INDEX_MAX = 200
# líneas que se repiten legítimamente y no son duplicación de contenido
NOISE = re.compile(r"^\s*[-*]?\s*(##|ver |pertenece_a|corre_en|usado_por)", re.I)
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
DATED = re.compile(r"^\d{4}-\d{2}")


def frontmatter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    out = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def _notes(vault: Path) -> dict[str, tuple[Path, str]]:
    notes = {}
    for folder, expected in TYPE_BY_FOLDER.items():
        directory = vault / folder
        if not directory.exists():
            continue
        pattern = "**/*.md" if expected == "episode" else "*.md"
        for f in sorted(directory.glob(pattern)):
            notes[f.stem] = (f, expected)
    return notes


def check(vault: Path) -> list[dict]:
    problems: list[dict] = []

    def flag(kind: str, note: str, detail: str) -> None:
        problems.append({"kind": kind, "note": note, "detail": detail})

    notes = _notes(vault)
    texts = {stem: f.read_text(errors="ignore") for stem, (f, _) in notes.items()}
    rel = {stem: f.relative_to(vault).as_posix() for stem, (f, _) in notes.items()}
    archived = {f.stem for f in (vault / "_archive").rglob("*.md")} if (vault / "_archive").exists() else set()

    for stem, (f, expected) in notes.items():
        text, fm = texts[stem], frontmatter(texts[stem])
        missing = [c for c in REQUIRED if c not in fm]
        if missing:
            flag("frontmatter", rel[stem], f"missing fields: {', '.join(missing)}")
        if fm.get("type") and fm["type"] != expected:
            flag("type", rel[stem], f"type={fm['type']} but it lives in a {expected} folder")
        if "## Relaciones" not in text and expected != "episode":
            flag("relations", rel[stem], "no ## Relaciones section")
        if not WIKILINK.search(text):
            flag("isolated", rel[stem], "links to no other note")
        if expected == "episode" and not DATED.match(stem):
            flag("name", rel[stem], "episode name does not start with a date")

    # Una nota archivada NO es un enlace roto: sigue existiendo fuera de la carpeta viva.
    for stem, text in texts.items():
        for link in sorted({l.strip() for l in WIKILINK.findall(text)}):
            if link not in notes and link not in archived:
                flag("broken-link", rel[stem], f"[[{link}]] does not exist")

    # Una nota sin enlaces entrantes es una nota que build_context nunca va a encontrar.
    incoming: collections.Counter = collections.Counter()
    for text in texts.values():
        for link in {l.strip() for l in WIKILINK.findall(text)}:
            incoming[link] += 1
    for stem in notes:
        if incoming[stem] == 0 and not DATED.match(stem):
            flag("orphan", rel[stem], "no note links to it")

    # Observaciones idénticas en dos notas = el mismo hecho en dos lugares.
    seen: dict[str, str] = {}
    for stem, text in texts.items():
        for line in text.splitlines():
            line = line.strip()
            if len(line) < 90 or NOISE.match(line):
                continue
            digest = hashlib.sha1(line.encode()).hexdigest()
            if digest in seen and seen[digest] != rel[stem]:
                flag("duplicate", rel[stem], f"same observation as {seen[digest]}: “{line[:70]}…”")
            else:
                seen[digest] = rel[stem]

    # El índice: presupuesto, entradas inventadas y notas sin línea.
    index = vault / "INDEX.md"
    if not index.exists():
        flag("index", "INDEX.md", "does not exist")
        return problems
    text = index.read_text(errors="ignore")
    lines = len(text.splitlines())
    if lines > INDEX_MAX:
        flag("index", "INDEX.md", f"{lines} lines, over the budget of {INDEX_MAX}")
    targets = set(re.findall(r"\]\(([^)]+\.md)\)", text))
    for target in sorted(targets):
        if not (vault / target).exists():
            flag("index", "INDEX.md", f"points to {target}, which does not exist")
    for link in sorted({l.strip() for l in WIKILINK.findall(text)}):
        if link not in notes:
            flag("index", "INDEX.md", f"wikilink [[{link}]] does not resolve")
    # Con el INDEX en su tope, exigir una línea por nota es imposible de cumplir: lo que no
    # cabe se poda (SCHEMA.md) y se encuentra por búsqueda. Ahí pasa a ser un aviso.
    if lines < INDEX_MAX:
        for path in _unindexed(rel.values(), targets):
            flag("index", path, "has no line in INDEX.md")
    return problems


def _unindexed(rels, targets: set[str]) -> list[str]:
    return [r for r in rels if not r.startswith("episodes/") and r not in targets]


def warnings(vault: Path, secrets: bool = True) -> list[str]:
    """Lo que conviene saber pero no es una violación."""
    out = _index_full_warning(vault)
    if secrets:
        out += secret_scan.warnings(vault)
    return out


def _index_full_warning(vault: Path) -> list[str]:
    index = vault / "INDEX.md"
    if not index.exists():
        return []
    text = index.read_text(errors="ignore")
    if len(text.splitlines()) < INDEX_MAX:
        return []
    targets = set(re.findall(r"\]\(([^)]+\.md)\)", text))
    rels = [f.relative_to(vault).as_posix() for f, _ in _notes(vault).values()]
    missing = _unindexed(rels, targets)
    if not missing:
        return []
    shown = ", ".join(missing[:10]) + (", …" if len(missing) > 10 else "")
    return [f"INDEX.md is full: {len(missing)} notes without a line ({shown})"]


def note_count(vault: Path) -> int:
    return len(_notes(vault))


def summary_line(vault: Path, problems: list[dict]) -> str:
    total = note_count(vault)
    if not problems:
        return f"STRUCTURE OK — {total} notes, no violations"
    kinds = collections.Counter(p["kind"] for p in problems)
    return (f"STRUCTURE: {len(problems)} violations in {total} notes "
            f"({', '.join(f'{k}={v}' for k, v in kinds.most_common())})")


def render(vault: Path, problems: list[dict], limit: int | None = None,
           secrets: bool = True) -> str:
    lines = [summary_line(vault, problems)]
    shown = problems if limit is None else problems[:limit]
    lines += [f"  [{p['kind']}] {p['note']}: {p['detail']}" for p in shown]
    if len(shown) < len(problems):
        lines.append(f"  … {len(problems) - len(shown)} more violations not shown")
    lines += [f"  [warning] {w}" for w in warnings(vault, secrets=secrets)]
    return "\n".join(lines)
