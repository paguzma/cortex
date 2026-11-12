"""notes.py — leer una nota del vault: partes, datos concretos y quién la enlaza.

Funciones puras. Las usan el validador (no se pierde ni se inventa ningún dato
concreto) y el orquestador (vecindario de una nota). Un falso positivo en los
datos solo vuelve más estricto al validador: nunca hace perder información."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

SCOPE = ("entities/", "references/")
_TRAILING = ".,;:)"
CLASSES = {
    "wikilink": re.compile(r"\[\[([^\]|#]+)"),
    "url": re.compile(r"https?://[^\s)>\]`'\"]+"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "port": re.compile(r"(?:\b(?:\d{1,3}\.){3}\d{1,3}|\b[A-Za-z][\w.-]*|(?<![\w:])):(\d{2,5})\b"),
    "path": re.compile(r"(?<![\w/:.~-])~?/[\w.@+-]+(?:/[\w.@+-]+)*/?"),
    "version": re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"),
    "date": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "ctvm": re.compile(r"\b(?:CT|VM|LXC) ?\d{3}\b"),
    "code": re.compile(r"`([^`\n]+)`"),
    "number": re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?!\d|[.,]\d)"),
}


def in_scope(path: str) -> bool:
    return path.startswith(SCOPE) and path.endswith(".md")


def split_note(text: str) -> tuple[dict, str, str]:
    """-> (frontmatter, frontmatter crudo con sus ---, cuerpo). Sin frontmatter: ({}, "", texto)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, "", text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), m.group(0), text[m.end():]


def section(body: str, title: str) -> str | None:
    """La sección «## titulo» completa (con su encabezado) hasta el próximo «## »."""
    m = re.search(rf"(?m)^## {re.escape(title)}[ \t]*\n.*?(?=^## |\Z)", body, re.S)
    return m.group(0).rstrip() if m else None


def facts(text: str) -> set[str]:
    """Datos concretos como «clase:valor». Las rutas y URLs pierden la puntuación final."""
    out = set()
    for kind, rx in CLASSES.items():
        for m in rx.finditer(text):
            value = m.group(1) if rx.groups else m.group(0)
            if kind in ("url", "path"):
                value = value.rstrip(_TRAILING)
            if value:
                out.add(f"{kind}:{value.strip()}")
    return out


def linking_to(texts: dict[str, str], target_path: str) -> list[str]:
    """Notas en alcance que enlazan al objetivo por su nombre de archivo (el SCHEMA lo exige)."""
    rx = re.compile(r"\[\[" + re.escape(Path(target_path).stem) + r"[\]|#]")
    return sorted(p for p, t in texts.items() if p != target_path and in_scope(p) and rx.search(t))
