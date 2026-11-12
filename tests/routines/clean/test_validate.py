"""Pruebas de validate.py."""
import pytest

from cortex.routines.clean import validate

BASE = """---
title: gamedesk (workstation Linux)
type: infra
status: active
created: 2026-08-25
updated: '2026-10-05'
aliases:
- gconsole
- worker cuda
- workstation linux
permalink: cortex/entities/infra/gamedesk
---

`gconsole`: el PC de juegos de [[ana]], y nada más (as of 2026-10-05).

## Observaciones
- [estado] Formateado 2026-09-29; corre Windows para juegos
- [config] RTX 3090 Ti, sin servicios del homelab
- [estado] Es solo PC de juegos desde 2026-10-05

## Relaciones
- pertenece_a [[ana]]
"""

GOOD = BASE.replace("title: gamedesk (workstation Linux)", "title: gconsole (PC de juegos)") \
    .replace("- worker cuda\n- workstation linux\n", "").replace("updated: '2026-10-05'", "updated: '2026-10-07'") \
    .replace("- [estado] Formateado 2026-09-29; corre Windows para juegos\n- [config] RTX 3090 Ti, sin servicios del homelab\n- [estado] Es solo PC de juegos desde 2026-10-05\n",
             "- [estado] Formateado 2026-09-29: Windows, RTX 3090 Ti, solo juegos desde 2026-10-05, sin servicios del homelab\n")


def target(base, cand):
    return validate.validate_target(base, cand)


# --- objetivo ---

def test_correct_cleanup_passes_and_names_changes():
    v = target(BASE, GOOD)
    assert v.ok, v.errors
    assert "title: «gamedesk (workstation Linux)» → «gconsole (PC de juegos)»" in v.changes
    assert "removed alias `worker cuda`" in v.changes
    assert "removed alias `workstation linux`" in v.changes


def test_lost_fact():
    v = target(BASE, GOOD.replace("Formateado 2026-09-29: ", "Formateado: "))
    assert not v.ok
    assert any("lost" in e and "date:2026-09-29" in e for e in v.errors), v.errors


def test_invented_fact():
    v = target(BASE, GOOD.replace("sin servicios del homelab", "sin servicios, IP 192.168.1.140"))
    assert not v.ok
    assert any("invented" in e and "ip:192.168.1.140" in e for e in v.errors), v.errors


def test_invented_path():
    assert not target(BASE, GOOD.replace("solo juegos", "solo juegos (/etc/juegos)")).ok


def test_relations_changed():
    v = target(BASE, GOOD.replace("- pertenece_a [[ana]]", "- usado_por [[ana]]"))
    assert not v.ok
    assert any("Relaciones" in e for e in v.errors)


def test_structural_field_changed():
    assert not target(BASE, GOOD.replace("type: infra", "type: tool")).ok


def test_note_that_grows():
    v = target(BASE, GOOD.replace("## Observaciones", "## Observaciones\n- [estado] " + "relleno " * 80))
    assert not v.ok
    assert any("grew" in e for e in v.errors)


def test_no_frontmatter():
    assert not target(BASE, "solo texto").ok


# --- objetivo: falla cerrado ---

def test_new_frontmatter_key_rejected():
    v = target(BASE, GOOD.replace("permalink:", "superseded_by: ana\npermalink:"))
    assert not v.ok
    assert "changed frontmatter key `superseded_by`" in v.errors


def test_removing_frontmatter_key_rejected():
    v = target(BASE.replace("permalink:", "tags:\n- homelab\npermalink:"), GOOD)
    assert not v.ok
    assert "changed frontmatter key `tags`" in v.errors


def test_removing_updated_rejected():
    v = target(BASE, GOOD.replace("updated: '2026-10-07'\n", ""))
    assert not v.ok
    assert any("updated" in e for e in v.errors), v.errors


def test_title_with_invented_fact_rejected():
    v = target(BASE, GOOD.replace("title: gconsole (PC de juegos)", "title: gconsole 192.168.1.140"))
    assert not v.ok
    assert any("invented" in e and "ip:192.168.1.140" in e for e in v.errors), v.errors


def test_alias_with_invented_path_rejected():
    v = target(BASE, GOOD.replace("- gconsole\n", "- gconsole\n- /etc/juegos\n"))
    assert not v.ok
    assert any("invented" in e and "path:/etc/juegos" in e for e in v.errors), v.errors


def test_base_with_unreadable_yaml_rejected():
    broken = "type: [infra"
    v = target(BASE.replace("type: infra", broken), GOOD.replace("type: infra", broken))
    assert not v.ok
    assert "the original note has no readable frontmatter (type/created/permalink)" in v.errors


def test_base_without_permalink_rejected():
    line = "permalink: cortex/entities/infra/gamedesk\n"
    v = target(BASE.replace(line, ""), GOOD.replace(line, ""))
    assert not v.ok
    assert "the original note has no readable frontmatter (type/created/permalink)" in v.errors


def test_second_relations_section_rejected():
    v = target(BASE, GOOD + "\n## Relaciones\n- usado_por [[ana]]\n")
    assert not v.ok
    assert any("Relaciones" in e for e in v.errors), v.errors


def test_relation_slipped_into_observations_rejected():
    v = target(BASE, GOOD.replace("## Observaciones\n", "## Observaciones\n- usado_por [[ana]]\n"))
    assert not v.ok
    assert any("relation" in e and "usado_por [[ana]]" in e for e in v.errors), v.errors


def test_changed_number_rejected():
    v = target(BASE, GOOD.replace("RTX 3090 Ti", "RTX 4090 Ti"))
    assert not v.ok
    assert any("invented" in e and "number:4090" in e for e in v.errors), v.errors
    assert any("lost" in e and "number:3090" in e for e in v.errors), v.errors


@pytest.mark.parametrize("line", ['- "depende de" [[ana]]', "* usa [[ana]]", "1. usa [[ana]]",
                                  "- usa  [[ana]]", "-\tusa [[ana]]", "+ usa [[ana]] (desde siempre)",
                                  "2) usa [[ana]]", "- 'depende de' [[ana]]",
                                  "- usa [[ana]] (desde (siempre))", "> - usa [[ana]]"])
def test_other_relation_shapes_outside_relations_rejected(line):
    v = target(BASE, GOOD.replace("## Observaciones\n", "## Observaciones\n" + line + "\n"))
    assert not v.ok
    assert any("relation" in e for e in v.errors), v.errors


def test_observation_with_link_in_the_middle_still_passes():
    line = "- [estado] lo usa [[ana]] a diario\n"
    v = target(BASE.replace("## Observaciones\n", "## Observaciones\n" + line),
               GOOD.replace("## Observaciones\n", "## Observaciones\n" + line))
    assert v.ok, v.errors


def test_new_observation_with_link_in_the_middle_passes():
    v = target(BASE, GOOD.replace("## Observaciones\n", "## Observaciones\n- [estado] lo usa [[ana]] a diario\n"))
    assert v.ok, v.errors


def test_updated_that_is_not_a_date_rejected():
    v = target(BASE, GOOD.replace("updated: '2026-10-07'", "updated: 10.1.1.1"))
    assert not v.ok
    assert any("updated" in e for e in v.errors), v.errors


def test_updated_unquoted_date_passes():
    v = target(BASE, GOOD.replace("updated: '2026-10-07'", "updated: 2026-10-07"))
    assert v.ok, v.errors


# --- vecina ---

NEIGHBOR = """---
title: UPS desktop
type: infra
---

UPS APC para el PC.

## Relaciones
- protegerá [[gamedesk]]
- sobre [[pve]]
"""
STRUCK = NEIGHBOR.replace("- protegerá [[gamedesk]]",
                          "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: gconsole es solo PC de juegos, ver [[gamedesk]]")
LINE = "- protegerá [[gamedesk]]"


def neighbor(original, new, objective=frozenset()):
    return validate.validate_neighbor(original, new, "gamedesk", objective)


def test_valid_strike():
    v = neighbor(NEIGHBOR, STRUCK)
    assert v.ok, v.errors


def test_line_rewritten_instead_of_struck():
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace("protegerá [[gamedesk]]", "protege la consola [[gamedesk]]")).ok


def test_reason_without_link_to_target():
    assert not neighbor(NEIGHBOR, STRUCK.replace(", ver [[gamedesk]]", "")).ok


def test_added_line():
    assert not neighbor(NEIGHBOR, NEIGHBOR + "- nueva\n").ok


def test_frontmatter_touched():
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace(
        "title: UPS desktop", "~~title: UPS desktop~~ ← superseded 2026-10-07: x [[gamedesk]]")).ok


def test_list_dash_inside_strike_rejected():
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace(
        LINE, "~~- protegerá [[gamedesk]]~~ ← superseded 2026-10-07: solo juegos, ver [[gamedesk]]")).ok


@pytest.mark.parametrize("struck", [
    "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: ahora en 192.168.1.140, ver [[gamedesk]]",  # ip inventada
    "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: ver [[gamedesk]] y [[otra-nota]]",        # enlace extra
    "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: ~~algo~~ ver [[gamedesk]]",              # tachado en el motivo
    "- ~~protegerá [[gamedesk]]~~ ← superseded 9999-99-99: ver [[gamedesk]]",                       # fecha imposible
])
def test_reason_with_invented_data_or_fake_date_rejected(struck):
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace(LINE, struck)).ok


def test_striking_a_heading_rejected():
    new = NEIGHBOR.replace("## Relaciones", "~~## Relaciones~~ ← superseded 2026-10-07: ver [[gamedesk]]")
    assert not neighbor(NEIGHBOR, new).ok


def test_striking_a_blank_line_rejected():
    original = NEIGHBOR.replace("UPS APC para el PC.\n", "UPS APC para el PC.\n  \n")
    new = NEIGHBOR.replace("UPS APC para el PC.\n", "UPS APC para el PC.\n ~~ ~~ ← superseded 2026-10-07: ver [[gamedesk]]\n")
    assert not neighbor(original, new).ok


def test_changes_include_the_reason():
    v = neighbor(NEIGHBOR, STRUCK)
    assert v.ok, v.errors
    assert v.changes == ["struck: - protegerá [[gamedesk]] — reason: gconsole es solo PC de juegos, ver [[gamedesk]]"]


REASON = "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: solo juegos desde 2026-10-05, ver [[gamedesk]]"


def test_reason_with_target_fact_without_target_facts_rejected():
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace(LINE, REASON)).ok


def test_reason_with_target_fact_with_target_facts_passes():
    objective = frozenset({"date:2026-10-05", "number:2026", "number:10", "number:05", "wikilink:ana"})
    v = neighbor(NEIGHBOR, NEIGHBOR.replace(LINE, REASON), objective)
    assert v.ok, v.errors


def test_reason_with_fact_foreign_to_target_rejected():
    objective = frozenset({"date:2026-10-05", "number:2026", "number:10", "number:05"})
    assert not neighbor(NEIGHBOR, NEIGHBOR.replace(LINE, REASON.replace("2026-10-05", "2026-10-06")), objective).ok


def test_original_with_carriage_return_rejected():
    original = NEIGHBOR.replace("UPS APC para el PC.", "UPS APC para el PC.\r")
    new = STRUCK.replace("UPS APC para el PC.", "UPS APC para el PC.\r")
    v = neighbor(original, new)
    assert not v.ok
    assert "unrecognizable frontmatter" in v.errors


def test_original_with_empty_frontmatter_rejected():
    original = "---\n---\n\nUPS.\n- protegerá [[gamedesk]]\n"
    new = original.replace(LINE, "- ~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07: ver [[gamedesk]]")
    v = neighbor(original, new)
    assert not v.ok
    assert "unrecognizable frontmatter" in v.errors


# --- historial ---

def test_history_only_grows():
    assert validate.validate_history(None, "# H\n").ok
    assert validate.validate_history("# H\n", "# H\n\n## x\n").ok
    assert not validate.validate_history("# H\nviejo\n", "# H\n").ok


def test_extending_the_last_old_line_rejected():
    assert not validate.validate_history("# H\nviejo\n", "# H\nviejoMODIFICADO\n").ok
    assert validate.validate_history("# H\nviejo\n\n", "# H\nviejo\n\n## 2026-10-07\n").ok
