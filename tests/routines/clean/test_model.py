"""Pruebas de model.py. Nunca llaman al CLI real: ctx.claude es un falso."""
import pytest

from cortex.routines.clean import model, validate

from .conftest import make_ctx


class Recorder:
    def __init__(self, rc=0, output="<nota>\nhola\n</nota>"):
        self.rc, self.output, self.calls = rc, output, []

    def __call__(self, prompt, **kw):
        self.calls.append({"prompt": prompt, **kw})
        return self.rc, self.output


def ctx_with(tmp_path, claude):
    return make_ctx(tmp_path / "vault", tmp_path / "archive", tmp_path / "state", claude, model="claude-x")


def test_call_goes_through_ctx_claude_without_tools_or_mcp(tmp_path):
    rec = Recorder()
    ctx = ctx_with(tmp_path, rec)
    out = model.call(ctx, "el prompt")
    assert model.extract(out, "nota") == "hola"
    [c] = rec.calls
    assert c["prompt"] == "el prompt"
    assert c["model"] == "claude-x"
    assert c["env"] == ctx.env
    assert c["cwd"] == tmp_path / "state" / "cwd"
    assert c["cwd"].is_dir()                     # se crea
    assert c["allowed_tools"] == ()
    assert c["mcp"] is False
    assert c["timeout"] == model.TIMEOUT


def test_nonzero_exit_is_a_failure(tmp_path):
    with pytest.raises(RuntimeError, match="exited with 3"):
        model.call(ctx_with(tmp_path, Recorder(rc=3, output="boom")), "p")


def test_extract():
    assert model.extract("bla <json>\n[1]\n</json> bla", "json") == "[1]"
    assert model.extract("sin etiquetas", "json") is None


def test_code_block_inside_tags():
    assert model.extract("<json>\n```json\n[1]\n```\n</json>", "json") == "[1]"
    assert model.extract("<nota>\n```\na\nb\n```\n</nota>", "nota") == "a\nb"


def test_mentions_the_tag_before():
    assert model.extract("Pondré la lista entre <json> y </json>:\n<json>\n[2]\n</json>", "json") == "[2]"
    assert model.extract("uso <json> aquí\n<json>\n[3]\n</json>", "json") == "[3]"


def test_tags_case_insensitive():
    assert model.extract("<JSON>\n[4]\n</Json>", "json") == "[4]"


NEIGHBOR = """---
title: UPS desktop
---

UPS para el PC.

## Relaciones
- protegerá [[gamedesk]]
- sobre [[pve]]
"""


def test_strikes_and_passes_the_validator():
    items = [{"linea": "- protegerá [[gamedesk]]", "motivo": "gconsole es solo PC de juegos"},
             {"linea": "- no existe esta línea", "motivo": "x"}]
    new, applied, ignored = model.strike(NEIGHBOR, items, "2026-10-07", "gamedesk")
    assert ("- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: gconsole es solo PC de juegos "
            "(ver [[gamedesk]])") in new
    assert applied == ["- protegerá [[gamedesk]]"]
    assert ignored == ["- no existe esta línea"]
    assert validate.validate_neighbor(NEIGHBOR, new, "gamedesk").ok


def test_does_not_strike_frontmatter_or_already_struck():
    t = NEIGHBOR.replace("- sobre [[pve]]", "- ~~sobre [[pve]]~~ ← superseded 2026-10-01: x")
    items = [{"linea": "title: UPS desktop", "motivo": "x"},
             {"linea": "- ~~sobre [[pve]]~~ ← superseded 2026-10-01: x", "motivo": "y"}]
    new, applied, ignored = model.strike(t, items, "2026-10-07", "gamedesk")
    assert new == t
    assert applied == []
    assert len(ignored) == 2


def test_does_not_strike_headings():
    new, applied, ignored = model.strike(NEIGHBOR, [{"linea": "## Relaciones", "motivo": "x"}],
                                         "2026-10-07", "gamedesk")
    assert new == NEIGHBOR
    assert applied == []
    assert ignored == ["## Relaciones"]


def test_item_that_is_not_a_dict():
    new, applied, ignored = model.strike(NEIGHBOR, ["- protegerá [[gamedesk]]", None, 3],
                                         "2026-10-07", "gamedesk")
    assert new == NEIGHBOR
    assert applied == []
    assert ignored == ["- protegerá [[gamedesk]]", "None", "3"]


def test_reason_with_strike_is_ignored():
    items = [{"linea": "- protegerá [[gamedesk]]", "motivo": "ya no ~~es~~ así"}]
    new, _, ignored = model.strike(NEIGHBOR, items, "2026-10-07", "gamedesk")
    assert new == NEIGHBOR
    assert ignored == ["- protegerá [[gamedesk]]"]


def test_matches_ignoring_trailing_spaces():
    t = NEIGHBOR.replace("- protegerá [[gamedesk]]", "- protegerá [[gamedesk]]  ")
    items = [{"linea": "- protegerá [[gamedesk]]", "motivo": "ya no"}]
    new, applied, ignored = model.strike(t, items, "2026-10-07", "gamedesk")
    assert "- ~~protegerá [[gamedesk]]  ~~ ← superseded 2026-10-07: ya no (ver [[gamedesk]])" in new
    assert len(applied) == 1
    assert ignored == []
    # y el item con espacios finales también empareja una línea sin ellos
    new, _, _ = model.strike(NEIGHBOR, [{"linea": "- sobre [[pve]]   ", "motivo": "ya no"}],
                             "2026-10-07", "gamedesk")
    assert "- ~~sobre [[pve]]~~ ← superseded" in new


def test_prompts_carry_what_is_needed():
    p = model.prompt_tidy("NOTA-X", "2026-10-07")
    assert "NOTA-X" in p
    assert "<lo_que_salio_al_historial>" not in p
    assert "<lo_que_salio_al_historial>" not in model.prompt_tidy("NOTA-X", "2026-10-07", [])
    h = model.prompt_tidy("NOTA-X", "2026-10-07", ["- ~~viejo A~~ ← superseded 2026-10-01: nuevo"])
    assert ("<lo_que_salio_al_historial>\n- ~~viejo A~~ ← superseded 2026-10-01: nuevo\n"
            "</lo_que_salio_al_historial>") in h
    assert "Esas líneas ya salieron de la nota: son historia." in h
    assert "No vuelvas a poner en la nota ningún dato que solo aparezca en lo que salió al historial." in h
    assert h.index("</lo_que_salio_al_historial>") < h.index("<nota_actual>")
    assert "<nota>" in p
    assert "2026-10-07" in p
    q = model.prompt_neighbors("gamedesk", "OBJ", "entities/infra/ups.md", "VEC")
    for s in ("gamedesk", "OBJ", "entities/infra/ups.md", "VEC", "<json>", "sin datos concretos",
              "Marca una línea SOLO si es falsa completa hoy", "solo menciona «gamedesk» de paso",
              "Ante la duda, no la marques"):
        assert s in q
