"""Grupos candidatos a dominio, sacados del grafo de wikilinks.

Deducir dominios solo del grafo ya falló una vez: los hubs (notas que enlazan media
memoria) se llevaban todo a su comunidad. Por eso los hubs se sacan antes de agrupar y
el grupo nunca se aplica solo: es una propuesta que el modelo nombra.
"""
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from ..agentos.graph import RX_LINK, Resolver, read_vault
from .assign import FRONTMATTER

MIN_GROUP = 5
MIN_COHESION = 0.5
MIN_NOTES_FOR_HUBS = 20
HUB_QUANTILE = 0.95
SEED = 42
RESOLUTION = 0.7   # con 1.0 Louvain parte en dos un racimo chico y denso (6 notas → 3 + 3)


@dataclass(frozen=True)
class Group:
    id: str                 # estable: depende solo de sus notas
    notes: tuple[str, ...]  # stems de las notas con frontmatter y SIN area: (las que recibirían el dominio)
    fits: str | None        # dominio existente al que ya pertenece la mayoría con area:
    cohesion: float         # aristas internas / (internas + hacia afuera)


def note_graph(vault: Path) -> tuple[nx.Graph, dict[str, dict]]:
    """Grafo no dirigido de las notas de memoria y reglas (sin episodios ni núcleo)."""
    notes = [n for n in read_vault(vault)
             if n["layer"] in ("memory", "rules") and n["kind"] != "episode"]
    by_id = {n["id"]: n for n in notes}
    resolve = Resolver(notes)
    graph = nx.Graph()
    graph.add_nodes_from(by_id)
    for n in notes:
        for raw in RX_LINK.findall(n["text"]):
            target = resolve(raw)
            if target and target != n["id"] and target in by_id:
                graph.add_edge(n["id"], target)
    return graph, by_id


def hubs(graph: nx.Graph) -> set[str]:
    if graph.number_of_nodes() < MIN_NOTES_FOR_HUBS:
        return set()
    degrees = sorted(d for _, d in graph.degree())
    threshold = degrees[min(len(degrees) - 1, int(len(degrees) * HUB_QUANTILE))]
    return {n for n, d in graph.degree() if d > threshold and d >= 3}


def _group_id(members: list[str]) -> str:
    return "g-" + hashlib.sha1("\n".join(sorted(members)).encode()).hexdigest()[:10]


def groups(vault: Path) -> list[Group]:
    graph, notes = note_graph(vault)
    core = graph.copy()
    core.remove_nodes_from(hubs(graph))
    core.remove_nodes_from([n for n in list(core) if core.degree(n) == 0])
    if core.number_of_edges() == 0:
        return []
    out = []
    for community in nx.community.louvain_communities(core, seed=SEED, resolution=RESOLUTION):
        members = sorted(community)
        # sin frontmatter assign no puede escribir area:: no son candidatas, o el grupo volvería
        # cada noche con su llamada al modelo
        unassigned = [n for n in members
                      if not notes[n]["meta"].get("area") and FRONTMATTER.match(notes[n]["text"])]
        if len(unassigned) < MIN_GROUP:
            continue
        internal = core.subgraph(members).number_of_edges()
        boundary = sum(1 for _ in nx.edge_boundary(core, community))
        cohesion = internal / (internal + boundary) if internal + boundary else 0.0
        if cohesion < MIN_COHESION:
            continue
        assigned = Counter(notes[n]["meta"]["area"] for n in members if notes[n]["meta"].get("area"))
        fits = None
        if assigned:
            domain, votes = assigned.most_common(1)[0]
            fits = domain if votes * 2 > sum(assigned.values()) else None
        out.append(Group(_group_id(unassigned), tuple(unassigned), fits, round(cohesion, 2)))
    return sorted(out, key=lambda g: (-len(g.notes), g.id))


def domain_counts(vault: Path) -> Counter:
    """Notas con area: explícito, por dominio."""
    _, notes = note_graph(vault)
    return Counter(n["meta"]["area"] for n in notes.values() if n["meta"].get("area"))
