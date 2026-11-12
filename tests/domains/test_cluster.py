from cortex.domains import cluster
from .conftest import cluster_of, write_note


def test_empty_vault_has_no_groups(vault):
    assert cluster.groups(vault) == []


def test_two_clusters_give_two_groups(vault):
    a = cluster_of(vault, "casa", 6)
    b = cluster_of(vault, "trabajo", 6)
    write_note(vault, "entities/projects", "puente", ["casa-0", "trabajo-0"])
    groups = cluster.groups(vault)
    found = {frozenset(g.notes) for g in groups}
    assert len(groups) == 2 and all(any(set(x) <= f for f in found) for x in (a, b))
    assert all(g.fits is None and g.cohesion >= 0.5 for g in groups)


def test_small_groups_are_ignored(vault):
    cluster_of(vault, "chico", 4)
    assert cluster.groups(vault) == []


def test_hub_does_not_swallow_clusters(vault):
    a = cluster_of(vault, "casa", 8, extra=("hub",))
    b = cluster_of(vault, "trabajo", 8, extra=("hub",))
    c = cluster_of(vault, "plata", 8, extra=("hub",))
    write_note(vault, "entities/infra", "hub", a + b + c)
    graph, _ = cluster.note_graph(vault)
    assert cluster.hubs(graph) == {"hub"}
    groups = cluster.groups(vault)
    assert len(groups) == 3
    assert all("hub" not in g.notes for g in groups)
    assert {frozenset(g.notes) for g in groups} == {frozenset(a), frozenset(b), frozenset(c)}


def test_assigned_notes_are_not_candidates_but_give_fits(vault):
    assigned = cluster_of(vault, "casa", 4, area="hogar")
    names = [f"casa-nueva-{i}" for i in range(5)]
    for i, n in enumerate(names):
        write_note(vault, "entities/projects", n, [names[(i + 1) % 5], assigned[i % 4], assigned[(i + 1) % 4]])
    groups = cluster.groups(vault)
    assert len(groups) == 1
    assert set(groups[0].notes) == set(names) and groups[0].fits == "hogar"


def test_ids_are_stable_and_episodes_ignored(vault):
    cluster_of(vault, "casa", 6)
    write_note(vault, "episodes/2026", "2026-10-08--todo", [f"casa-{i}" for i in range(6)], kind="episode")
    first = cluster.groups(vault)
    assert first and first == cluster.groups(vault)
    assert all("2026-10-08--todo" not in g.notes for g in first)


def test_domain_counts(vault):
    cluster_of(vault, "casa", 3, area="hogar")
    write_note(vault, "feedback", "regla", ["casa-0"], area="trabajo", kind="feedback")
    assert cluster.domain_counts(vault) == {"hogar": 3, "trabajo": 1}
