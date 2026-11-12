from cortex import paths
from cortex.domains import assign, store


def ring(prefix: str, size: int, area: str | None = None) -> list[str]:
    vault = paths.vault_dir()
    names = [f"{prefix}-{i}" for i in range(size)]
    for i, name in enumerate(names):
        area_line = f"area: {area}\n" if area else ""
        (vault / "entities/projects" / f"{name}.md").write_text(
            f"---\ntitle: {name}\ntype: project\nstatus: active\n{area_line}---\n\nNota.\n\n"
            f"## Relaciones\n- a [[{names[(i + 1) % size]}]]\n- a [[{names[(i + 2) % size]}]]\n")
    return names


def area_of(name: str) -> str | None:
    return assign.current_area((paths.vault_dir() / "entities/projects" / f"{name}.md").read_text())


def test_empty_vault(admin):
    page = admin.get("/setup/domains").text
    assert "No domains yet" in page and "None right now" in page


def test_name_a_raw_group(admin):
    ring("casa", 6)
    page = admin.get("/setup/domains").text
    assert "Group 1" in page and "casa-0" in page
    import re
    gid = re.search(r'name="id" value="(g-[0-9a-f]+)"', page).group(1)
    r = admin.post("/setup/domains", data={"action": "name-group", "id": gid, "name": "Hogar"})
    assert "6 notes are now in «hogar»" in r.text
    assert area_of("casa-0") == "hogar"
    assert admin.post("/setup/domains", data={"action": "name-group", "id": gid, "name": "general"}).status_code == 400


def test_rename_merge_and_proposals(admin):
    ring("casa", 3, area="hogar")
    ring("plata", 2, area="finanzas")
    store.add([{"id": "new:hogar", "kind": "new", "domain": "hogar", "text": "New domain «hogar»"},
               {"id": "merge:finanzas:hogar", "kind": "merge", "source": "finanzas",
                "target": "hogar", "text": "consider merging"}])
    page = admin.get("/setup/domains").text
    assert "Waiting on you" in page and "consider merging" in page
    r = admin.post("/setup/domains", data={"action": "rename", "source": "hogar", "target": "casa"})
    assert "3 notes updated" in r.text and area_of("casa-0") == "casa"
    # las propuestas que nombraban «hogar» ya no apuntan a nada
    assert store.load() == []
    admin.post("/setup/domains", data={"action": "merge", "source": "finanzas", "target": "casa"})
    assert area_of("plata-0") == "casa"
    assert admin.post("/setup/domains", data={"action": "rename", "source": "casa", "target": "Mal Nombre"}).status_code == 400


def test_dismiss(admin):
    store.add([{"id": "new:x", "kind": "new", "domain": "x1", "text": "t"}])
    admin.post("/setup/domains", data={"action": "dismiss", "id": "new:x"})
    assert store.load() == []
