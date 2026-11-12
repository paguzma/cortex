import pytest

from cortex.domains import assign

NOTE = "---\ntitle: casa\ntype: project\nstatus: active\n---\n\nCuerpo con area: falsa en el texto.\n"


def test_set_area_inserts_and_replaces():
    once = assign.set_area(NOTE, "hogar")
    assert "status: active\narea: hogar\n---" in once
    assert assign.current_area(once) == "hogar"
    twice = assign.set_area(once, "casa")
    assert twice.count("area: ") == 2  # la del frontmatter + la del cuerpo, que no se toca
    assert assign.current_area(twice) == "casa"
    assert twice.endswith("Cuerpo con area: falsa en el texto.\n")


@pytest.mark.parametrize("name", ["general", "Casa", "a", "con espacio", "x" * 30, "1abc"])
def test_invalid_names(name):
    with pytest.raises(assign.AreaError):
        assign.set_area(NOTE, name)


def test_no_frontmatter():
    with pytest.raises(assign.AreaError):
        assign.set_area("sin frontmatter", "hogar")


def test_only_area_changed():
    assert assign.only_area_changed(NOTE, assign.set_area(NOTE, "hogar"))
    assert not assign.only_area_changed(NOTE, NOTE.replace("Cuerpo", "Otro"))
    assert not assign.only_area_changed(NOTE, assign.set_area(NOTE, "hogar").replace("title: casa", "title: x"))


def test_apply_skips_assigned_and_restores_on_failure(tmp_path):
    (tmp_path / "a.md").write_text(NOTE)
    (tmp_path / "b.md").write_text(assign.set_area(NOTE, "trabajo"))
    (tmp_path / "c.md").write_text(NOTE)
    with pytest.raises(assign.AreaError):
        assign.apply(tmp_path, {"a.md": "hogar", "b.md": "hogar", "c.md": "No Vale"})
    assert (tmp_path / "a.md").read_text() == NOTE
    assert assign.apply(tmp_path, {"a.md": "hogar", "b.md": "hogar"}) == ["a.md"]
    assert assign.current_area((tmp_path / "b.md").read_text()) == "trabajo"


def test_rename(tmp_path):
    for name, area in (("a", "casa"), ("b", "casa"), ("c", "trabajo")):
        (tmp_path / f"{name}.md").write_text(assign.set_area(NOTE, area))
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "d.md").write_text(assign.set_area(NOTE, "casa"))
    assert assign.rename(tmp_path, "casa", "hogar") == 2
    assert assign.current_area((tmp_path / "a.md").read_text()) == "hogar"
    assert assign.current_area((tmp_path / "_archive" / "d.md").read_text()) == "casa"


def test_note_without_frontmatter_is_skipped_not_fatal(tmp_path):
    # Una nota suelta sin frontmatter no puede tumbar el lote entero cada noche.
    (tmp_path / "a.md").write_text(NOTE)
    (tmp_path / "suelta.md").write_text("sin frontmatter")
    skipped: list[str] = []
    assert assign.apply(tmp_path, {"a.md": "hogar", "suelta.md": "hogar"}, skipped=skipped) == ["a.md"]
    assert skipped == ["suelta.md"]
    assert (tmp_path / "suelta.md").read_text() == "sin frontmatter"
    assert assign.apply(tmp_path, {"suelta.md": "hogar"}) == []
