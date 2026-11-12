"""El grafo del panel: notas del vault + skills + rutinas + MCP + clientes, con sus enlaces.

Cada nodo tiene dos coordenadas independientes, y el panel las usa así:
  layer -> el RADIO en la vista de anillos (núcleo al centro, clientes afuera)
  area  -> el ÁNGULO: un sector por dominio dentro de cada anillo

El dominio (area) nunca se adivina con listas fijas: lo da el frontmatter `area:` de la
nota; si no lo tiene, vota entre sus vecinos (cada voto pesa 1/grado del vecino: enlazar
a una nota que todo enlaza dice poco); si nadie vota, es `general`.
"""
from __future__ import annotations

import re
from pathlib import Path

GENERAL = "general"
LAYERS = ["core", "skills", "rules", "memory", "routines", "mcps", "clients"]
NOTE_LAYERS = ("core", "rules", "memory")
FOLDERS = ("entities", "decisions", "feedback", "references", "episodes")
KIND_BY_FOLDER = {"episodes": "episode", "decisions": "decision", "references": "reference",
                  "feedback": "rule"}
KIND_BY_ENTITY = {"people": "person", "projects": "project", "infra": "infra", "tools": "tool"}
# rutinas del producto -> notas núcleo que tocan (las que no estén, al INDEX)
ROUTINE_TOUCHES = {"consolidate": ("INDEX", "SCHEMA"), "clean": ("SCHEMA",), "snapshot": ("INDEX",)}
RX_LINK = re.compile(r"\[\[([^\]|#]+)")
MAX_ROUNDS = 8


def frontmatter(text: str) -> tuple[dict, str]:
    """Frontmatter simple (clave: valor, escalares plegados) y el cuerpo. Nunca falla."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    key = None
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, _, v = line.partition(":")
            key, v = k.strip(), v.strip().strip("'\"")
            meta[key] = "" if v in (">", ">-", "|", "|-") else v
        elif key and line.startswith("  ") and not meta[key].startswith("["):
            meta[key] = (meta[key] + " " + line.strip()).strip()   # escalar plegado
    return meta, text[end + 4:]


def summary(body: str) -> str:
    """Primer párrafo de prosa, sin markdown pesado: es la «nota» de la tarjeta."""
    for paragraph in body.split("\n\n"):
        p = paragraph.strip()
        if p and not p.startswith(("#", "|", "```", "- [", "<", "---")):
            p = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", p)
            p = re.sub(r"[*`_]", "", " ".join(p.split()))
            return p[:220]
    return ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def read_vault(vault: Path) -> list[dict]:
    """Notas del grafo: las de la raíz (núcleo) y las de las carpetas del contrato.

    Nunca .git, _archive ni _templates."""
    notes = []
    if not vault.exists():
        return notes
    files = sorted(vault.glob("*.md"))
    for folder in FOLDERS:
        if (vault / folder).is_dir():
            files += sorted((vault / folder).rglob("*.md"))
    for f in files:
        rel = f.relative_to(vault).as_posix()
        if any(part.startswith((".", "_")) for part in Path(rel).parts):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        meta, body = frontmatter(text)
        parts = rel.split("/")
        if len(parts) == 1:
            layer, kind = "core", "core"
        elif parts[0] == "feedback":
            layer, kind = "rules", "rule"
        else:
            layer = "memory"
            kind = KIND_BY_FOLDER.get(parts[0]) or meta.get("type") \
                or (KIND_BY_ENTITY.get(parts[1]) if len(parts) > 2 else None) or "note"
        notes.append({"id": f.stem, "rel": rel, "text": text, "meta": meta, "body": body,
                      "layer": layer, "kind": kind})
    return notes


class Resolver:
    """Destino de un wikilink -> id de nodo: slug exacto, último tramo de ruta, título, slug."""

    def __init__(self, notes: list[dict]):
        self.ids = {n["id"] for n in notes}
        self.lower: dict[str, str] = {}
        for n in notes:
            for key in (n["id"], n["meta"].get("title") or "", _slug(n["id"]),
                        _slug(n["meta"].get("title") or "")):
                if key:
                    self.lower.setdefault(key.lower(), n["id"])

    def __call__(self, target: str) -> str | None:
        t = target.strip().removesuffix(".md")
        if t in self.ids:
            return t
        last = t.rstrip("/").split("/")[-1]
        if last in self.ids:
            return last
        for key in (t.lower(), last.lower(), _slug(last)):
            if key in self.lower:
                return self.lower[key]
        return None


def domain_list(areas) -> list[str]:
    """Los dominios presentes, ordenados, con `general` al final (y siempre presente)."""
    named = sorted({a for a in areas if a and a != GENERAL})
    return named + [GENERAL]


def resolve_areas(ids: set[str], fixed: dict[str, str],
                  neighbours: dict[str, set[str]]) -> dict[str, str]:
    """Dominio de cada nota: fijo si trae `area:`; si no, voto ponderado de sus vecinos.

    Cada voto pesa 1/grado del vecino. Empate: gana el orden de domain_list, estable entre
    corridas. Iterativo: una nota sin área puede heredarla de un vecino que la heredó."""
    order = domain_list(fixed.values())
    area = {n: fixed[n] for n in ids if fixed.get(n)}
    for _ in range(MAX_ROUNDS):
        changed = False
        for n in sorted(ids):
            if n in fixed and fixed[n]:
                continue
            votes: dict[str, float] = {}
            for v in neighbours.get(n, ()):
                a = area.get(v)
                if a and neighbours.get(v):
                    votes[a] = votes.get(a, 0.0) + 1 / len(neighbours[v])
            if votes:
                best = max(votes, key=lambda a: (votes[a], -order.index(a)))
                if best != area.get(n):
                    area[n], changed = best, True
        if not changed:
            break
    return {n: area.get(n) or GENERAL for n in ids}


def build(notes: list[dict], skills: list[dict], routines: list[dict], clients: list[dict],
          mcp_status: str | None, git_dates: dict[str, str], runs: list[dict]) -> tuple[dict, dict]:
    """Arma el grafo. Devuelve (graph, bodies) — bodies = {path: texto} para «open»."""
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()
    bodies: dict[str, str] = {}

    def link(a: str | None, b: str | None) -> None:
        if a and b and a in nodes and b in nodes and a != b:
            edges.add((a, b) if a < b else (b, a))

    # 1. Notas del vault
    resolve = Resolver(notes)
    for n in notes:
        if n["id"] in nodes:
            continue                      # mismo nombre en dos carpetas: gana la primera
        nodes[n["id"]] = {"id": n["id"], "kind": n["kind"], "layer": n["layer"], "area": "",
                          "label": (n["meta"].get("title") or n["id"])[:60], "path": n["rel"],
                          "summary": summary(n["body"]), "changed": git_dates.get(n["rel"], ""),
                          "bytes": len(n["text"].encode())}
        bodies[n["rel"]] = n["text"]
    for n in notes:
        for target in set(RX_LINK.findall(n["text"])):
            link(n["id"], resolve(target))

    # 2. Dominio de cada nota (el núcleo no vota: lo enlaza todo)
    voters = {i for i, v in nodes.items() if v["layer"] in ("rules", "memory")}
    neighbours: dict[str, set[str]] = {i: set() for i in voters}
    for a, b in edges:
        if a in voters and b in voters:
            neighbours[a].add(b)
            neighbours[b].add(a)
    fixed = {n["id"]: n["meta"].get("area", "").strip().lower() for n in notes
             if n["id"] in voters and n["meta"].get("area", "").strip()}
    for node_id, area in resolve_areas(voters, fixed, neighbours).items():
        nodes[node_id]["area"] = area
    for v in nodes.values():
        v["area"] = v["area"] or GENERAL

    # 3. Skills de los plugins del marketplace
    cores = [i for i, v in nodes.items() if v["layer"] == "core"]
    for sk in skills:
        sid = "skill:" + sk["name"]
        nodes[sid] = {"id": sid, "kind": "skill", "layer": "skills", "label": sk["name"],
                      "area": (sk.get("area") or GENERAL).lower(), "path": sk["path"],
                      "summary": (sk.get("description") or "")[:220], "changed": "",
                      "plugin": sk["plugin"]}
        text = sk.get("text") or ""
        bodies[sk["path"]] = text
        for target in set(RX_LINK.findall(text)):
            link(sid, resolve(target))
        for core in cores:
            if f"{core}.md" in text:
                link(sid, core)

    # 4. Rutinas del producto
    for r in routines:
        rid = "routine:" + r["name"]
        nodes[rid] = {"id": rid, "kind": "routine", "layer": "routines", "area": GENERAL,
                      "label": r["name"], "path": f"routine · {r.get('schedule') or 'no schedule'}",
                      "summary": r.get("summary") or "", "changed": "",
                      "enabled": bool(r.get("enabled")), "state": r.get("state"),
                      "uses_model": bool(r.get("needs_claude")), "bad": r.get("ok") is False}
        for target in ROUTINE_TOUCHES.get(r["name"], ("INDEX",)):
            link(rid, target)

    # 5. El MCP de memoria y los clientes que le hablan
    nodes["mcp:cortex"] = {"id": "mcp:cortex", "kind": "mcp", "layer": "mcps", "area": GENERAL,
                           "label": "cortex", "path": "mcp · /mcp", "changed": "",
                           "summary": "the memory MCP server (basic-memory over this vault)",
                           "status": mcp_status}
    link("mcp:cortex", "AGENTS" if "AGENTS" in nodes else (cores[0] if cores else None))
    for c in clients:
        nodes[c["id"]] = {"id": c["id"], "kind": "client", "layer": "clients", "area": GENERAL,
                          "label": c["label"], "path": f"client · {c.get('ip') or 'any unnamed IP'}",
                          "summary": c.get("summary") or "", "changed": c.get("last_seen") or "",
                          "ip": c.get("ip")}
        link(c["id"], "mcp:cortex")

    degree = {i: 0 for i in nodes}
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    for v in nodes.values():
        v["degree"] = degree[v["id"]]

    def count(field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in nodes.values():
            out[v[field]] = out.get(v[field], 0) + 1
        return out

    by_area = count("area")
    domains = domain_list(by_area)
    graph = {"nodes": list(nodes.values()), "edges": [list(e) for e in sorted(edges)],
             "domains": domains, "areas": {a: by_area.get(a, 0) for a in domains},
             "layers": {layer: count("layer").get(layer, 0) for layer in LAYERS},
             "kinds": count("kind"), "runs": runs}
    return graph, bodies
