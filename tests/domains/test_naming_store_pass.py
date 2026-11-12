import json

from cortex import config, paths
from cortex.domains import assign, cluster, consolidate_pass, naming, store
from cortex.routines.base import Context

from .conftest import cluster_of, write_note


def make_ctx(vault, claude):
    state = paths.state_dir() / "routines" / "consolidate"
    state.mkdir(parents=True, exist_ok=True)
    return Context(cfg=config.load(), env={}, vault=vault, archive=paths.data_dir() / "archive",
                   state=state, model="opus", claude=claude)


def namer(mapping):
    """Un claude falso que nombra cada grupo según la primera nota que reconoce."""
    calls = []

    def claude(prompt, **kwargs):
        calls.append((prompt, kwargs))
        answer, current = [], None
        for line in prompt.splitlines():
            if line.startswith("## Grupo "):
                gid = line.split()[2]
                current = gid
            for key, domain in mapping.items():
                if line.startswith(f"- {key}") and current:
                    answer.append({"group": current, "domain": domain})
                    current = None
        return 0, "Listo:\n" + json.dumps(answer)
    return claude, calls


def test_parse_keeps_only_valid_entries():
    out = 'texto [{"group":"g-1","domain":"Casa"},{"group":"g-x","domain":"z"},{"group":"g-2","domain":"general"},{"group":"g-3","domain":"con espacio"}]'
    assert naming.parse(out, {"g-1", "g-2", "g-3"}) == {"g-1": "casa"}
    assert naming.parse("no json", {"g-1"}) == {}
    assert naming.parse("[{roto", {"g-1"}) == {}


def test_prompt_lists_groups_and_existing(vault):
    cluster_of(vault, "casa", 6)
    cluster_of(vault, "otro", 2, area="trabajo")
    groups = cluster.groups(vault)
    _, notes = cluster.note_graph(vault)
    text = naming.prompt(groups, notes, {"trabajo": 2})
    assert "- trabajo (2)" in text and f"## Grupo {groups[0].id}" in text and "- casa-0:" in text


def test_store_add_dedup_and_dismiss(data):
    assert store.add([{"id": "new:casa", "kind": "new", "text": "x"}]) == 1
    assert store.add([{"id": "new:casa", "kind": "new", "text": "x"}]) == 0
    assert [p["id"] for p in store.load()] == ["new:casa"]
    store.dismiss("new:casa")
    assert store.load() == []


def test_pass_names_groups_and_proposes(data, vault):
    a = cluster_of(vault, "casa", 6)
    b = cluster_of(vault, "plata", 6)
    claude, calls = namer({"casa-": "hogar", "plata-": "finanzas"})
    summary, detail = consolidate_pass.run(make_ctx(vault, claude))
    assert summary.startswith("domains: 12 notes named · new: finanzas, hogar · 2 proposals")
    assert calls[0][1]["cwd"].name == "cwd" and "allowed_tools" not in calls[0][1]
    assert assign.current_area((vault / "entities/projects/casa-0.md").read_text()) == "hogar"
    assert {p["id"] for p in store.load()} == {"new:hogar", "new:finanzas"}
    # segunda noche: nada nuevo que nombrar, el modelo ni se llama
    claude2, calls2 = namer({})
    assert consolidate_pass.run(make_ctx(vault, claude2))[0] == "domains: nothing new"
    assert calls2 == []


def test_pass_never_overwrites_existing_area(data, vault):
    cluster_of(vault, "casa", 3, area="mio")
    names = [f"casa-x{i}" for i in range(5)]
    for i, n in enumerate(names):
        write_note(vault, "entities/projects", n, [names[(i + 1) % 5], "casa-0", "casa-1"])
    claude, _ = namer({"casa-x": "otro"})
    consolidate_pass.run(make_ctx(vault, claude))
    assert assign.current_area((vault / "entities/projects/casa-0.md").read_text()) == "mio"


def test_pass_with_bad_model_output_writes_nothing(data, vault):
    cluster_of(vault, "casa", 6)
    before = (vault / "entities/projects/casa-0.md").read_text()
    summary, _ = consolidate_pass.run(make_ctx(vault, lambda p, **k: (0, "no sé")))
    assert summary == "domains: the model named no group"
    summary, _ = consolidate_pass.run(make_ctx(vault, lambda p, **k: (1, "boom")))
    assert summary == "domains: the model failed (exit 1)"
    assert (vault / "entities/projects/casa-0.md").read_text() == before


def test_too_many_domains_proposes_a_merge():
    before = {f"d{i}": 10 + i for i in range(7)}
    after = dict(before, nuevo=2)
    ids = [p["id"] for p in consolidate_pass.proposals_for(before, after)]
    assert ids == ["new:nuevo", "merge:nuevo:d0"]


def test_notes_without_frontmatter_never_reach_the_model(data, vault):
    # assign se las salta (no hay dónde escribir area:): si formaran grupo, se pagaría
    # una llamada al modelo cada noche para nada.
    names = cluster_of(vault, "suelta", 6)
    for n in names:
        p = vault / "entities/projects" / f"{n}.md"
        p.write_text(p.read_text().split("---\n", 2)[2])
    assert cluster.groups(vault) == []
    claude, calls = namer({"suelta-": "hogar"})
    assert consolidate_pass.run(make_ctx(vault, claude))[0] == "domains: nothing new"
    assert calls == []
