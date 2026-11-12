from cortex.routines import structure
from cortex.seed import seed_vault


def note(stem: str, kind: str, body: str = "", fm_extra: str = "") -> str:
    return (f"---\ntitle: {stem}\ntype: {kind}\nstatus: active\ncreated: 2026-10-01\n"
            f"updated: 2026-10-01\n{fm_extra}---\n\n{body}\n")


def write(vault, rel, text):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def index_with(vault, *rels):
    lines = "\n".join(f"- [{r}]({r}) — x" for r in rels)
    (vault / "INDEX.md").write_text(f"# INDEX\n\n{lines}\n")


def test_seeded_vault_is_clean(tmp_path):
    seed_vault(tmp_path / "v", "2026-10-08")
    assert structure.check(tmp_path / "v") == []
    assert structure.summary_line(tmp_path / "v", []).startswith("STRUCTURE OK — 0 notes")


def test_healthy_pair(tmp_path):
    write(tmp_path, "entities/people/ana.md", note("ana", "person", "## Relaciones\n- trabaja_en [[web]]"))
    write(tmp_path, "entities/projects/web.md", note("web", "project", "## Relaciones\n- de [[ana]]"))
    index_with(tmp_path, "entities/people/ana.md", "entities/projects/web.md")
    assert structure.check(tmp_path) == []


def test_detects_each_kind(tmp_path):
    write(tmp_path, "entities/people/ana.md", "---\ntitle: ana\ntype: project\n---\nsin nada [[fantasma]]\n")
    write(tmp_path, "episodes/2026/sin-fecha.md", note("sin-fecha", "episode", "[[ana]]"))
    long = "- [hecho] " + "x" * 100
    write(tmp_path, "feedback/a.md", note("a", "feedback", f"{long}\n## Relaciones\n- [[b]]"))
    write(tmp_path, "feedback/b.md", note("b", "feedback", f"{long}\n## Relaciones\n- [[a]]"))
    (tmp_path / "INDEX.md").write_text("- [x](no/existe.md)\n" + "l\n" * 201)
    kinds = {p["kind"] for p in structure.check(tmp_path)}
    assert {"frontmatter", "type", "relations", "broken-link", "name", "duplicate",
            "index", "orphan"} <= kinds


def test_archived_note_is_not_a_broken_link(tmp_path):
    write(tmp_path, "entities/people/ana.md", note("ana", "person", "## Relaciones\n- [[viejo]]"))
    write(tmp_path, "_archive/2026/viejo.md", "archivado")
    index_with(tmp_path, "entities/people/ana.md")
    kinds = [p["kind"] for p in structure.check(tmp_path)]
    assert "broken-link" not in kinds


def test_missing_index(tmp_path):
    assert structure.check(tmp_path) == [{"kind": "index", "note": "INDEX.md", "detail": "does not exist"}]


def _full_index_vault(vault, index_lines):
    write(vault, "entities/people/ana.md", note("ana", "person", "## Relaciones\n- [[web]]"))
    write(vault, "entities/projects/web.md", note("web", "project", "## Relaciones\n- [[ana]]"))
    filler = "".join(f"- l{i}\n" for i in range(index_lines - 1))
    (vault / "INDEX.md").write_text("- [ana](entities/people/ana.md) — x\n" + filler)


def test_full_index_turns_missing_lines_into_a_warning(tmp_path):
    # Con el INDEX en su tope, exigir una línea por nota haría fallar cada noche.
    _full_index_vault(tmp_path, structure.INDEX_MAX)
    assert structure.check(tmp_path) == []
    assert structure.warnings(tmp_path) == [
        "INDEX.md is full: 1 notes without a line (entities/projects/web.md)"]
    assert "INDEX.md is full" in structure.render(tmp_path, [])


def test_index_with_room_still_requires_a_line(tmp_path):
    _full_index_vault(tmp_path, structure.INDEX_MAX - 1)
    assert structure.check(tmp_path) == [
        {"kind": "index", "note": "entities/projects/web.md", "detail": "has no line in INDEX.md"}]
    assert structure.warnings(tmp_path) == []
