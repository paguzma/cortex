"""Secretos en texto plano dentro del vault.

SCHEMA.md prohíbe guardar valores de secretos, pero nada lo impide: un agente o una
migración puede copiar una password a una nota y desde ahí cualquier search_notes la
devuelve. Esto la encuentra y dice DÓNDE está, nunca el valor.

Es un aviso y no una violación: un vault que ya trae secretos haría fallar la
consolidación cada noche hasta limpiarlos, y limpiarlos (rotar, redactar) es una
decisión de su dueño, no de una rutina.
"""
import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".obsidian", ".trash"}

KNOWN_PREFIX = re.compile(
    r"(?<![\w-])(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
JWT = re.compile(r"(?<![\w.-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
URL_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^:\s/@]+:([^@\s/]+)@")
# Una palabra clave, un separador (con el markdown que suele rodearlo) y un valor.
KEY_VALUE = re.compile(
    r"(?i)(?:pass(?:word)?|passwd|api[_-]?key|apikey|token|secret|contraseña)"
    r"[\"'`*]*\s*[:=][\"'`*]*\s*[\"'`]?([^\s\"'`|,)]+)")
# Lo que parece valor pero es una referencia: variables, plantillas, punteros.
REFERENCE = re.compile(r"^(\\?\$|\{|<|!secret|os\.environ|‹redactado|en$|ver$|vive$)", re.I)
PLACEHOLDER = re.compile(r"^(.)\1{5,}$|^(password|changeme|example|secret|token)$", re.I)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str


def _looks_secret(value: str) -> bool:
    """Un valor real mezcla tipos de carácter; una palabra suelta no."""
    value = value.strip(".;:")
    if len(value) < 8 or REFERENCE.match(value) or PLACEHOLDER.match(value):
        return False
    classes = sum((any(c.islower() for c in value), any(c.isupper() for c in value),
                   any(c.isdigit() for c in value), any(not c.isalnum() for c in value)))
    return classes >= 3 or (len(value) >= 20 and classes >= 2 and any(c.isdigit() for c in value))


def scan_text(text: str) -> list[Finding]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if PRIVATE_KEY.search(line):
            kind = "private-key"
        elif KNOWN_PREFIX.search(line):
            kind = "known-prefix"
        elif JWT.search(line):
            kind = "jwt"
        elif any(_looks_secret(m.group(1)) for m in URL_PASSWORD.finditer(line)):
            kind = "url-password"
        elif any(_looks_secret(m.group(1)) for m in KEY_VALUE.finditer(line)):
            kind = "key-value"
        else:
            continue
        found.append(Finding("", n, kind))
    return found


def scan(vault: Path) -> list[Finding]:
    found = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if SKIP_DIRS & set(rel.parts):
            continue
        for f in scan_text(path.read_text(errors="ignore")):
            found.append(Finding(rel.as_posix(), f.line, f.kind))
    return found


def warnings(vault: Path, shown: int = 10) -> list[str]:
    found = scan(vault)
    if not found:
        return []
    where = ", ".join(f"{f.path}:{f.line} ({f.kind})" for f in found[:shown])
    more = ", …" if len(found) > shown else ""
    noun = "secret" if len(found) == 1 else "secrets"
    return [f"{len(found)} possible plaintext {noun} (move the value to your secret store "
            f"and leave only its name): {where}{more}"]
