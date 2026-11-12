from cortex.agentos import graph


def test_domain_list_sorted_general_last_and_always_present():
    assert graph.domain_list([]) == ["general"]
    assert graph.domain_list(["work", "general", "home", "work", ""]) == ["home", "work", "general"]


def test_frontmatter_area_is_fixed():
    out = graph.resolve_areas({"a", "b"}, {"a": "home"}, {"a": {"b"}, "b": {"a"}})
    assert out == {"a": "home", "b": "home"}


def test_neighbour_vote_weighted_by_inverse_degree():
    # x enlaza a un hub de "work" (que enlaza a todo) y a una nota chica de "home"
    ids = {"x", "hub", "h1", "w1", "w2", "w3"}
    nb = {"x": {"hub", "h1"}, "hub": {"x", "w1", "w2", "w3"}, "h1": {"x"},
          "w1": {"hub"}, "w2": {"hub"}, "w3": {"hub"}}
    out = graph.resolve_areas(ids, {"hub": "work", "h1": "home"}, nb)
    assert out["x"] == "home"            # 1/1 de h1 pesa más que 1/4 del hub
    assert out["w1"] == "work"           # hereda del hub


def test_vote_propagates_and_falls_back_to_general():
    nb = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}, "lonely": set()}
    out = graph.resolve_areas(set(nb), {"a": "lab"}, nb)
    assert out == {"a": "lab", "b": "lab", "c": "lab", "lonely": "general"}


def test_tie_breaks_by_domain_order():
    nb = {"x": {"a", "b"}, "a": {"x"}, "b": {"x"}}
    assert graph.resolve_areas(set(nb), {"a": "zeta", "b": "alpha"}, nb)["x"] == "alpha"


def _note(path, text):
    meta, body = graph.frontmatter(text)
    parts = path.split("/")
    layer = "core" if len(parts) == 1 else "rules" if parts[0] == "feedback" else "memory"
    return {"id": parts[-1][:-3], "rel": path, "text": text, "meta": meta, "body": body,
            "layer": layer, "kind": "core" if layer == "core" else "note"}


def test_build_graph_edges_areas_and_bodies():
    notes = [_note("AGENTS.md", "# Agents\n\nRead [[SCHEMA]]."),
             _note("SCHEMA.md", "# Schema"),
             _note("entities/projects/site.md", "---\ntitle: Web Site\narea: work\n---\n\nThe site. [[host]]"),
             _note("entities/infra/host.md", "---\ntitle: Host\n---\n\nRuns [[Web Site]] and [[nowhere]]."),
             _note("feedback/be-kind.md", "Be kind. [[entities/infra/host]]")]
    skills = [{"name": "mem-save", "plugin": "cortex", "description": "Save", "area": "general",
               "text": "Read SCHEMA.md first, then [[site]].", "path": "plugins/cortex/skills/mem-save/SKILL.md"}]
    routines = [{"name": "consolidate", "enabled": True, "schedule": "30 4 * * *", "needs_claude": True, "ok": False}]
    clients = [{"id": "client:10.0.0.2", "ip": "10.0.0.2", "label": "laptop"},
               {"id": "client:unknown", "ip": None, "label": "client"}]
    g, bodies = graph.build(notes, skills, routines, clients, "up", {"SCHEMA.md": "2026-10-01T00:00:00+00:00"}, [])
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = {tuple(e) for e in g["edges"]}
    assert ("host", "site") in edges and ("AGENTS", "SCHEMA") in edges
    assert ("be-kind", "host") in edges                       # wikilink con ruta
    assert ("SCHEMA", "skill:mem-save") in edges and ("site", "skill:mem-save") in edges
    assert ("INDEX", "routine:consolidate") not in edges     # INDEX no existe: no se inventa
    assert ("SCHEMA", "routine:consolidate") in edges
    assert ("client:10.0.0.2", "mcp:cortex") in edges and ("AGENTS", "mcp:cortex") in edges
    assert nodes["site"]["area"] == "work" and nodes["host"]["area"] == "work"
    assert nodes["be-kind"]["area"] == "work" and nodes["be-kind"]["layer"] == "rules"
    assert nodes["AGENTS"]["area"] == "general" and nodes["AGENTS"]["layer"] == "core"
    assert nodes["site"]["label"] == "Web Site" and nodes["site"]["summary"] == "The site. host"
    assert nodes["SCHEMA"]["changed"].startswith("2026-10-01")
    assert nodes["routine:consolidate"]["bad"] is True and nodes["mcp:cortex"]["status"] == "up"
    assert g["domains"] == ["work", "general"]
    assert g["areas"] == {"work": 3, "general": sum(1 for n in nodes.values() if n["area"] == "general")}
    assert g["layers"]["clients"] == 2 and g["layers"]["memory"] == 2
    assert bodies["entities/projects/site.md"].startswith("---") and "plugins/cortex/skills/mem-save/SKILL.md" in bodies
    assert all(n["degree"] >= 0 for n in nodes.values())


def test_read_vault_skips_archive_templates_and_git(tmp_path):
    for rel in ("AGENTS.md", "entities/tools/a.md", "episodes/2026/2026-01-01--e.md", "decisions/d.md",
                "_archive/old.md", "_templates/t.md", ".git/x.md", "other/ignored.md", "entities/_drafts/z.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntype: tool\n---\nx")
    notes = {n["rel"]: n for n in graph.read_vault(tmp_path)}
    assert set(notes) == {"AGENTS.md", "entities/tools/a.md", "episodes/2026/2026-01-01--e.md", "decisions/d.md"}
    assert notes["episodes/2026/2026-01-01--e.md"]["kind"] == "episode"
    assert notes["entities/tools/a.md"]["kind"] == "tool"
    assert graph.read_vault(tmp_path / "missing") == []
