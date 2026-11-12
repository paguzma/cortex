"""Pruebas de notes.py y split.py."""
from cortex.routines.clean import notes, split

# Recorte de una nota real de infraestructura
PVE = """---
title: Proxmox VE (proxmox01)
type: infra
status: active
created: 2026-08-25
updated: '2026-09-30'
aliases:
- proxmox
- hypervisor
permalink: cortex/entities/infra/pve
---

Host Proxmox del homelab en 192.168.1.76, con 21 invitados.

## Observaciones
- ~~[pendiente] **memtest86+ completo AÚN NO corrido sobre el layout 4×16.**~~ ← superseded 2026-09-10: Ana booteó a producción 2026-09-10 ~17:00 con validación corta (tests 7+8)
- [config] Token API `cortex@pve` con rol CortexWatchdog, ver `/etc/pve/user.cfg`
- [estado] Grafo ~~127 nodos~~ ← superseded 2026-10-05 por la versión con skills. El panel corre en :8077
- ~~[riesgo] RAM marginal~~ ← superseded 2026-09-10:

## Relaciones
- parte_de [[homelab]]
- aloja [[brain-lxc]]
"""
HIST = "/data/archive/entities/infra/pve.md"


def test_split_note():
    fm, raw, body = notes.split_note(PVE)
    assert fm["type"] == "infra"
    assert raw.startswith("---\n") and raw.endswith("---\n")
    assert body.startswith("\nHost Proxmox")


def test_no_frontmatter():
    assert notes.split_note("hola") == ({}, "", "hola")


def test_section():
    _, _, body = notes.split_note(PVE)
    assert notes.section(body, "Relaciones") == "## Relaciones\n- parte_de [[homelab]]\n- aloja [[brain-lxc]]"
    assert notes.section(body, "No existe") is None


def test_concrete_facts():
    d = notes.facts("En 192.168.1.76:8006 corre `pve-manager` 8.2.4 desde 2026-09-10, ver "
                    "/etc/pve/user.cfg. CT 118 y [[brain-lxc]]. https://pve.example.lan/x.")
    for expected in ("ip:192.168.1.76", "port:8006", "code:pve-manager", "version:8.2.4",
                     "date:2026-09-10", "path:/etc/pve/user.cfg", "ctvm:CT 118",
                     "wikilink:brain-lxc", "url:https://pve.example.lan/x"):
        assert expected in d


def test_loose_numbers():
    d = notes.facts("RTX 3090 Ti, 21 invitados, 65,8 %")
    for expected in ("number:3090", "number:21", "number:65,8"):
        assert expected in d


def test_numbers_glued_to_letters_or_before_a_period():
    d = notes.facts("64GB de RAM, discos: 4.")
    assert "number:64" in d
    assert "number:4" in d


def test_numbers_before_a_comma():
    # «29,» antes no contaba como número: el validador no veía si se perdía.
    d = notes.facts("Formateado el 2026-09-29, con Windows; 3, 4 y 1,5 discos")
    for expected in ("number:29", "number:3", "number:4", "number:1,5"):
        assert expected in d


def test_linking_to():
    texts = {"entities/infra/pve.md": "x", "entities/infra/ups.md": "protege [[pve]] y [[pve|el host]]",
             "entities/tools/a.md": "ver [[pve#gotchas]]", "entities/tools/b.md": "[[pve-backup]] no",
             "episodes/2026/e.md": "[[pve]]", "feedback/f.md": "[[pve]]"}
    assert notes.linking_to(texts, "entities/infra/pve.md") == ["entities/infra/ups.md", "entities/tools/a.md"]


def test_moves_struck_and_keeps_current():
    base, moved = split.separate(PVE, HIST)
    assert len(moved) == 3
    assert "~~" not in base
    assert "- Ana booteó a producción 2026-09-10 ~17:00 con validación corta (tests 7+8)" in base
    assert "- [estado] Grafo la versión con skills. El panel corre en :8077" in base
    assert "RAM marginal" not in base          # tachado sin nada después: la línea se va entera
    assert base.startswith(notes.split_note(PVE)[1])   # frontmatter intacto
    assert base.rstrip().endswith(f"Historial: {HIST}")


def test_no_struck_changes_nothing():
    text = "---\ntitle: x\n---\n\nnada\n"
    assert split.separate(text, "/h/x.md") == (text, [])


def test_does_not_duplicate_history_line():
    base, _ = split.separate(PVE, "/h/pve.md")
    base2, _ = split.separate(base + "- ~~viejo~~ ← superseded 2026-10-07: nuevo\n", "/h/pve.md")
    assert base2.count("Historial: /h/pve.md") == 1


def test_does_not_eat_current_text_between_strikes():
    line = "- ~~a~~ sigue vigente ~~b~~ ← superseded 2026-01-01: nuevo"
    note = "---\ntitle: x\n---\n\n## Observaciones\n" + line + "\n"
    base, moved = split.separate(note, "/h/x.md")
    assert "- ~~a~~ sigue vigente nuevo" in base
    assert moved == [line]


def test_adjacent_strikes_leave_together():
    line = "- [x] ~~3 strikes~~ ~~36~~ ← superseded 2026-10-01: 40"
    note = "---\ntitle: x\n---\n\n## Observaciones\n" + line + "\n"
    base, _ = split.separate(note, "/h/x.md")
    assert "- [x] 40" in base
    assert "~~" not in base


def test_history_only_appends():
    h1 = split.append_history(None, "entities/infra/pve.md", "2026-10-07", ["- ~~a~~ ← superseded 2026-09-10: b"])
    assert h1.startswith("# Historial de entities/infra/pve.md\n")
    assert "## 2026-10-07 — cortex clean\n\n- ~~a~~ ← superseded 2026-09-10: b\n" in h1
    h2 = split.append_history(h1, "entities/infra/pve.md", "2026-10-08", ["- ~~c~~ ← superseded 2026-10-01: d"])
    assert h2.startswith(h1.rstrip("\n"))
    assert h2.endswith("## 2026-10-08 — cortex clean\n\n- ~~c~~ ← superseded 2026-10-01: d\n")
