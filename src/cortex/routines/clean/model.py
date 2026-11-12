"""model.py — todo lo que habla con el modelo.

La llamada pasa por ctx.claude (la única puerta al CLI), sin herramientas y sin MCP:
el modelo solo lee el prompt y devuelve texto. Qué credencial paga lo decide cada
usuario en la configuración del producto, no esta rutina."""
from __future__ import annotations

import re

from ..base import Context
from .notes import split_note

RX_LINE = re.compile(r"^(\s*(?:- )?)(.*)$")
TIMEOUT = 900


def cwd_for(ctx: Context):
    """Carpeta de trabajo del modelo: vacía y propia, fuera del vault."""
    cwd = ctx.state / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def call(ctx: Context, prompt: str, timeout: int = TIMEOUT) -> str:
    """Una llamada al modelo. Un código de salida distinto de cero es una falla (RuntimeError)."""
    rc, output = ctx.claude(prompt, model=ctx.model, env=ctx.env, cwd=cwd_for(ctx),
                            allowed_tools=(), mcp=False, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"claude exited with {rc}: {(output or '')[-300:]}")
    return output or ""


def extract(text: str, tag: str) -> str | None:
    """Contenido de la última apertura que tiene cierre después; quita un bloque de código envolvente."""
    pair = None
    for op in re.finditer(rf"<{tag}>", text, re.I):
        close = re.search(rf"</{tag}>", text[op.end():], re.I)
        if close:
            pair = (op.end(), op.end() + close.start())
    if pair is None:
        return None
    body = text[pair[0]:pair[1]]
    body = re.sub(r"^\n", "", body)
    body = re.sub(r"\n$", "", body)
    f = re.fullmatch(r"\s*```[\w-]*\n(.*?)\n?```\s*", body, re.S)
    return f.group(1) if f else body


def prompt_tidy(note: str, date: str, moved: list[str] | None = None) -> str:
    history = ""
    if moved:   # solo lectura: para que entienda los fragmentos que dejó lo tachado al irse
        lines = "\n".join(moved)
        history = f"""
<lo_que_salio_al_historial>
{lines}
</lo_que_salio_al_historial>
Esas líneas ya salieron de la nota: son historia. Úsalas solo para entender el contexto. Si una línea de la nota quedó como un fragmento sin sujeto porque lo tachado se fue al historial, reescríbela en una frase completa con el estado de hoy, o elimínala si no aporta nada vigente. No vuelvas a poner en la nota ningún dato que solo aparezca en lo que salió al historial.
"""
    return f"""Eres el limpiador nocturno del vault de memoria «cortex». Hoy es {date}.
Te paso una nota. Devuélvela ordenada para que diga la verdad DE HOY, sin ruido.

Reglas, todas obligatorias:
- Reescribe el párrafo inicial (antes de «## Observaciones») en 1 a 3 frases con el estado actual.
- Fusiona las observaciones que dicen lo mismo; conserva la versión más precisa. No borres información vigente.
- Ajusta `title`, `aliases` y `status` (active | superseded | archived) al estado actual: quita los
  aliases que describen algo que la propia nota dice que ya no es.
- Cambia `updated` a '{date}'. No cambies `type`, `created` ni `permalink`.
- Copia la sección «## Relaciones» IDÉNTICA. Conserva la línea «Historial: …» si existe.
- No agregues ningún dato concreto (IP, puerto, ruta, versión, fecha, URL, wikilink, texto entre
  backticks, ID de CT/VM) que no esté ya en la nota, y no quites ninguno de los que están.
- La nota no puede quedar más larga que la que te paso.
- Responde SOLO con la nota completa (frontmatter incluido) entre <nota> y </nota>.
{history}
<nota_actual>
{note}
</nota_actual>"""


def prompt_neighbors(slug: str, target: str, neighbor_path: str, neighbor: str) -> str:
    return f"""Eres el limpiador nocturno del vault de memoria «cortex».
La nota «{slug}» describe el estado actual de algo. Otra nota la enlaza. Busca en la otra nota
las líneas que CONTRADICEN el estado actual de «{slug}»: afirman en presente algo que hoy ya no es
verdad. No marques líneas que solo la mencionan, ni hechos pasados contados como pasados, ni el
frontmatter, ni líneas ya tachadas (~~ ~~).

Responde SOLO con un JSON entre <json> y </json>: una lista de objetos
{{"linea": "<la línea exacta, copiada carácter por carácter>", "motivo": "<por qué ya no es cierta, una frase>"}}.
Lista vacía si no hay ninguna.
El motivo es una frase corta sin datos concretos (ni IPs, rutas, puertos, versiones, números, fechas ni enlaces), salvo los que aparecen en la nota «{slug}».
Marca una línea SOLO si es falsa completa hoy. Si solo una parte quedó desactualizada, o si la línea habla de otra cosa (otro proyecto, otro sistema) y solo menciona «{slug}» de paso, NO la marques. Ante la duda, no la marques.

<estado_actual nota="{slug}">
{target}
</estado_actual>

<otra_nota ruta="{neighbor_path}">
{neighbor}
</otra_nota>"""


def strike(text: str, items: list[dict], date: str, slug: str) -> tuple[str, list[str], list[str]]:
    """Tacha en `text` las líneas pedidas, con el formato que exige validate_neighbor.

    Cada item es lo que produce el modelo según prompt_neighbors: {"linea", "motivo"}
    (esos nombres son parte del contrato con el modelo y no se traducen)."""
    lines = text.split("\n")
    fm_end = split_note(text)[1].count("\n")
    applied, ignored = [], []
    for it in items:
        if not isinstance(it, dict):
            ignored.append(str(it))
            continue
        line, reason = str(it.get("linea", "")), " ".join(str(it.get("motivo", "")).split())
        idx = next((i for i in range(fm_end, len(lines)) if lines[i].rstrip() == line.rstrip()), None)
        if (idx is None or "~~" in line or "~~" in reason or not line.strip()
                or line.strip().startswith("#") or not reason):
            ignored.append(line)
            continue
        if f"[[{slug}]]" not in reason:
            reason += f" (ver [[{slug}]])"
        prefix, rest = RX_LINE.match(lines[idx]).groups()
        lines[idx] = f"{prefix}~~{rest}~~ ← superseded {date}: {reason}"
        applied.append(line)
    return "\n".join(lines), applied, ignored
