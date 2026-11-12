"""Prueba de integración de la rutina con un vault, un historial y un ctx.claude falsos."""
import shutil
import signal

import pytest

from cortex.routines import structure
from cortex.routines.clean import clean, validate

from .conftest import NAS, NEIGHBOR, TARGET, TODAY, commit_old, git_raw

GD = "entities/infra/gamedesk.md"
UPS = "entities/infra/ups-desktop.md"


def hook(repo, body="exit 1"):
    h = repo / ".git/hooks/pre-commit"
    h.write_text(f"#!/bin/sh\n{body}\n")
    h.chmod(0o755)


@pytest.fixture
def fake_check(world, monkeypatch):
    """structure.check falso: un problema más en cuanto la nota cambia de título."""
    def install(break_revert=False, foreign_commit=False):
        def check(vault):
            n = vault / GD
            new = "gconsole (PC de juegos)" in n.read_text()
            if new and break_revert:   # ensucia: el revert no puede
                with open(n, "a") as f:
                    f.write("- [estado] editada a mano\n")
            if new and foreign_commit:  # otro commit encima del de la rutina: el revert va al suyo, no a HEAD
                (vault / "entities/infra/ajena.md").write_text("ajena\n")
                git_raw(vault, "add", "entities/infra/ajena.md")
                git_raw(vault, "commit", "-qm", "ajeno")
            return [{"kind": "title"}] if new else []
        monkeypatch.setattr(structure, "check", check)
    return install


def test_propose_writes_nothing(world):
    res = world.run()
    assert res.status == "ok"
    assert res.summary.startswith("proposed"), res.summary
    assert "removed alias `worker cuda`" in res.detail
    assert "ups-desktop" in res.detail
    assert world.read(GD) == TARGET
    assert world.read(UPS) == NEIGHBOR
    assert not (world.archive / GD).exists()


def test_apply_writes_commits_and_keeps_history(world):
    world.apply = True
    res = world.run()
    assert res.status == "ok", res
    assert res.summary.startswith("applied"), res.summary
    note = world.read(GD)
    assert "title: gconsole (PC de juegos)" in note
    assert "~~" not in note
    assert f"Historial: {world.archive}/{GD}" in note
    assert "~~protegerá [[gamedesk]]~~ ← superseded 2026-10-07" in world.read(UPS)
    assert "worker CUDA del homelab" in (world.archive / GD).read_text()
    assert "chore(clean):" in world.log(world.vault)
    assert world.status(world.vault) == ""
    assert world.status(world.archive) == ""
    assert (world.state / "registry.json").exists()


def test_model_runs_in_its_own_cwd_without_tools(world):
    world.run()
    assert world.claude.calls
    for c in world.claude.calls:
        assert c["cwd"] == world.state / "cwd"
        assert c["allowed_tools"] == () and c["mcp"] is False
        assert c["model"] == "test-model"
        assert c["env"] == {"SOME_SECRET": "x"}
    assert (world.state / "cwd").is_dir()


def test_own_commits_do_not_count_as_state_change(world):
    # El tachado que la propia corrida deja en ups-desktop («← superseded») no puede
    # volver a elegirla a la noche siguiente como «cambió de estado».
    world.apply = True
    assert world.run().status == "ok"
    res = world.run()
    assert res.status == "ok", res
    assert "changed state" not in res.detail
    assert f"=== {GD}" not in res.detail


def test_validator_rejection_is_not_a_failure_and_writes_nothing(world):
    world.apply = True
    world.claude.mode = "INVENT"
    res = world.run()
    assert res.status == "ok"
    assert "invented data" in res.detail
    assert "ip:10.9.9.9" in res.detail
    assert world.read(GD) == TARGET


def test_failure_while_applying_reports_and_restores(world):
    world.apply = True
    hook(world.vault, "echo broken-hook >&2\nexit 1")
    res = world.run()
    assert res.status == "fail", res
    assert "broken-hook" in res.detail
    assert world.read(GD) == TARGET
    assert world.read(UPS) == NEIGHBOR
    assert not (world.archive / GD).exists()
    assert world.status(world.vault) == ""
    assert world.status(world.archive) == ""
    assert world.log(world.archive) == ""   # el commit del historial de esta corrida se deshizo


def test_model_failure_is_a_fail_and_writes_nothing(world):
    world.apply = True
    world.claude.mode = "FAIL"
    res = world.run()
    assert res.status == "fail", res
    assert res.summary.startswith("the model failed: claude exited with 3")
    assert world.read(GD) == TARGET
    assert not (world.archive / GD).exists()


def test_valve_drops_to_one_target(world):
    # 2 objetivos con su vecina = 4 notas > 3: queda solo el primero (2 notas) y el
    # segundo se pospone, sin llamar de nuevo al modelo.
    world.add_second_target()
    res = world.run(max_notes=3)
    assert res.status == "ok", res
    assert res.summary.startswith("proposed 1 notes"), res.summary
    assert f"=== {GD}" in res.detail
    assert "ups-desktop" in res.detail
    assert "=== entities/infra/nas-viejo.md" not in res.detail
    assert "ups-nas" not in res.detail
    assert "postponed by the valve: entities/infra/nas-viejo.md" in res.detail
    rejections = world.registry(proposed=True)["rejections"]
    assert "valve" in rejections["entities/infra/nas-viejo.md"]["reason"]


def test_valve_drops_to_one_target_and_applies(world):
    world.add_second_target()
    world.apply = True
    res = world.run(max_notes=3)
    assert res.status == "ok", res
    assert res.summary.startswith("applied 1 notes"), res.summary
    assert "title: gconsole" in world.read(GD)
    assert "~~" not in world.read("entities/infra/ups-nas.md")
    assert "~~" in world.read("entities/infra/nas-viejo.md")
    assert "entities/infra/nas-viejo.md" not in world.registry()


def test_valve_in_propose_mode_is_ok(world):
    res = world.run(max_notes=1)
    assert res.status == "ok", res
    assert res.summary.startswith("proposed 0 notes · valve: would have stopped (2 notes)"), res.summary
    assert f"=== {GD}" in res.detail
    reg = world.registry(proposed=True)
    assert GD not in reg
    assert "valve" in reg["rejections"][GD]["reason"]


def test_valve_in_apply_mode_is_a_failure(world):
    world.apply = True
    res = world.run(max_notes=1)
    assert res.status == "fail", res
    assert "valve" in res.summary
    assert world.read(GD) == TARGET
    rejection = world.registry()["rejections"][GD]
    assert rejection["bytes"] == len(TARGET.encode())
    assert "valve" in rejection["reason"]


def test_target_linking_another_target_is_not_its_neighbor(world):
    world.add_second_target(NAS.replace("- vive_en [[pve]]", "- vive_en [[pve]]\n- protegerá [[gamedesk]]"),
                            own_neighbor=False)
    res = world.run()
    assert res.status == "ok", res
    assert "=== entities/infra/nas-viejo.md" in res.detail
    assert "--- neighbor entities/infra/nas-viejo.md" not in res.detail


def test_neighbor_of_both_targets_gets_both_strikes(world):
    world.add_second_target(own_neighbor=False)
    (world.vault / UPS).write_text(NEIGHBOR + "- protegerá [[nas-viejo]]\n")
    commit_old(world.vault, "ups")
    world.apply = True
    res = world.run()
    assert res.status == "ok", res
    ups = world.read(UPS)
    assert "~~protegerá [[gamedesk]]~~ ← superseded" in ups
    assert "~~protegerá [[nas-viejo]]~~ ← superseded" in ups
    assert "1 neighbors struck" in res.summary   # notas únicas, no tachados


def test_note_changed_during_the_run_is_skipped(world):
    world.apply = True
    world.claude.mode = "TOUCH"
    res = world.run()
    assert res.status == "ok", res
    assert res.summary.startswith(f"skipped: {GD} changed during the run"), res.summary
    assert world.read(GD) == TARGET + "- [estado] tocado a mano\n"
    assert world.read(UPS) == NEIGHBOR
    assert not (world.archive / GD).exists()
    assert "chore(clean)" not in world.log(world.vault)
    assert GD not in world.registry()


def test_uncommitted_change_in_target_is_skipped(world):
    dirty = TARGET.replace("el PC de juegos", "el PC  de juegos")
    (world.vault / GD).write_text(dirty)
    world.apply = True
    res = world.run()
    assert res.status == "ok", res
    assert res.summary.startswith("nothing to clean"), res.summary
    assert f"{GD} has uncommitted changes" in res.detail
    assert world.claude.calls == []   # se detecta antes de gastar modelo
    assert world.read(GD) == dirty
    assert world.read(UPS) == NEIGHBOR
    assert "chore(clean)" not in world.log(world.vault)


def test_dirty_target_yields_its_place_to_the_next(world):
    world.add_second_target()
    (world.vault / GD).write_text(TARGET.replace("el PC de juegos", "el PC  de juegos"))
    res = world.run()
    assert res.status == "ok", res
    assert f"=== {GD}" not in res.detail
    assert "=== entities/infra/nas-viejo.md" in res.detail
    assert f"{GD} has uncommitted changes" in res.detail


def test_dirty_history_is_a_failure_without_calling_the_model(world):
    (world.archive / "entities/infra").mkdir(parents=True)
    (world.archive / GD).write_text("a medio escribir\n")
    res = world.run()
    assert res.status == "fail", res
    assert res.summary == f"the history has uncommitted changes: {GD}"
    assert world.claude.calls == []


def test_dirty_check_parses_the_first_porcelain_line(world):
    # gitutil.git recorta la salida: « M ruta» no puede perder la primera letra de la ruta.
    (world.vault / GD).write_text(TARGET + "x\n")
    assert clean.dirty(world.vault) == [GD]
    (world.vault / "entities/infra/nueva.md").write_text("n\n")
    assert sorted(clean.dirty(world.vault)) == ["entities/infra/gamedesk.md", "entities/infra/nueva.md"]


def test_rejections_saved_even_if_applying_fails(world):
    # objetivo rechazado + vecina aprobada: se intenta aplicar la vecina y el commit falla
    world.claude.mode = "INVENT"
    hook(world.vault)
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert "failed to apply" in res.summary
    assert GD in world.registry()["rejections"]


def test_rejections_saved_even_if_skipped(world):
    world.claude.mode = "INVENT"
    (world.vault / UPS).write_text(NEIGHBOR.replace("UPS.", "UPS. "))   # vecina sucia
    world.apply = True
    res = world.run()
    assert res.status == "ok", res
    assert res.summary.startswith(f"skipped: {UPS}"), res.summary
    assert GD in world.registry()["rejections"]


def test_revert_targets_its_own_commit_even_with_another_on_top(world, fake_check):
    fake_check(foreign_commit=True)
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert "reverted" in res.summary
    assert world.read(GD) == TARGET
    assert world.read(UPS) == NEIGHBOR
    assert world.read("entities/infra/ajena.md") == "ajena\n"


def test_rejected_waits_before_coming_back(world):
    world.apply = True
    world.claude.mode = "INVENT"
    assert world.run().status == "ok"
    rejection = world.registry()["rejections"][GD]
    assert rejection["bytes"] == len(TARGET.encode())
    assert "invented data" in rejection["reason"]
    assert f"=== {GD}" not in world.run().detail


def test_missing_history_repo_is_created_on_first_use(world):
    shutil.rmtree(world.archive)
    world.apply = True
    res = world.run()
    assert res.status == "ok", res
    assert (world.archive / ".git").is_dir()
    assert "worker CUDA del homelab" in (world.archive / GD).read_text()
    assert world.status(world.archive) == ""


def test_history_dir_that_is_not_a_repo_is_initialized(world):
    shutil.rmtree(world.archive / ".git")
    res = world.run()
    assert res.status == "ok", res
    assert (world.archive / ".git").is_dir()
    assert world.read(GD) == TARGET   # en modo proponer, igual no se escribe nada


def test_failure_while_applying_undoes_the_previous_history_commit(world):
    (world.archive / "LEEME.md").write_text("historial\n")
    git_raw(world.archive, "add", "-A")
    git_raw(world.archive, "commit", "-qm", "inicio")
    hook(world.vault)
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert len(world.log(world.archive).splitlines()) == 1
    assert world.status(world.archive) == ""
    assert not (world.archive / GD).exists()
    assert world.read(GD) == TARGET


def test_history_goes_first_if_it_fails_the_vault_is_untouched(world):
    hook(world.archive, "echo history-broken >&2\nexit 1")
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert "history-broken" in res.detail
    assert world.read(GD) == TARGET
    assert world.read(UPS) == NEIGHBOR
    assert "chore(clean)" not in world.log(world.vault)
    assert world.status(world.vault) == ""
    assert not (world.archive / GD).exists()


def test_regression_reverts_both_repos(world, fake_check):
    fake_check()
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert "reverted" in res.summary
    assert world.read(GD) == TARGET
    assert world.read(UPS) == NEIGHBOR
    assert not (world.archive / GD).exists()
    assert "Revert" in world.log(world.archive)
    assert world.status(world.archive) == ""
    registry = world.registry()   # solo el backoff: la parte de notas limpiadas no se toca
    assert GD not in registry
    rejection = registry["rejections"][GD]
    assert rejection["reason"] == "reverted: the vault got worse"
    assert rejection["bytes"] == len(TARGET.encode())
    assert f"=== {GD}" not in world.run().detail   # a la noche siguiente no vuelve a intentarla


def test_failed_revert_warns_it_stayed_committed(world, fake_check):
    fake_check(break_revert=True)
    world.apply = True
    res = world.run()
    assert res.status == "fail", res
    assert "the night stayed committed: review by hand" in res.summary + res.detail


def test_unexpected_error_is_a_failure(world):
    (world.vault / "entities/infra/roto.md").write_bytes(b"\xff\xfe no es utf-8")
    res = world.run()
    assert res.status == "fail", res
    assert "UnicodeDecodeError" in res.summary


def test_sigterm_is_a_failure_and_the_handler_is_restored(world):
    before = signal.getsignal(signal.SIGTERM)
    world.claude.mode = "SIGTERM"
    res = world.run()
    assert res.status == "fail", res
    assert "SIGTERM" in res.summary
    assert signal.getsignal(signal.SIGTERM) is before
    assert world.read(GD) == TARGET


def test_sigterm_is_ignored_while_restoring():
    with clean.sigterm_raises():
        with clean.no_sigterm():
            signal.raise_signal(signal.SIGTERM)   # no levanta Terminated: se ignora
        with pytest.raises(clean.Terminated):
            signal.raise_signal(signal.SIGTERM)


def test_model_sees_what_went_to_the_history(world):
    world.run()
    assert any("<lo_que_salio_al_historial>" in c["prompt"] for c in world.claude.calls)


def test_proposing_twice_does_not_repeat_the_target(world):
    res = world.run()
    assert f"=== {GD}" in res.detail
    reg = world.registry(proposed=True)
    assert reg[GD]["bytes"] == len(TARGET.encode())   # la nota real
    assert reg[GD]["struck"] == 1
    assert f"=== {GD}" not in world.run().detail


def test_apply_registers_the_note_as_it_ends_up(world):
    world.apply = True
    world.run()
    reg = world.registry()[GD]
    assert (reg["bytes"], reg["struck"]) == (len(world.read(GD).encode()), 0)


def test_full_proposals_on_disk(world):
    res = world.run()
    d = world.state / "proposals" / TODAY
    assert "title: gconsole (PC de juegos)" in (d / "entities__infra__gamedesk.md").read_text()
    assert "+title: gconsole (PC de juegos)" in (d / "entities__infra__gamedesk.diff").read_text()
    assert "~~protegerá [[gamedesk]]~~" in (d / "entities__infra__ups-desktop.md").read_text()
    assert "+- ~~protegerá [[gamedesk]]~~" in (d / "entities__infra__ups-desktop.diff").read_text()
    assert f"Full proposals: {d}" in res.detail
    assert world.read(GD) == TARGET   # el vault no se toca


def test_proposals_keep_14_days(world):
    base = world.state / "proposals"
    for i in range(1, 21):
        (base / f"2026-09-{i:02d}").mkdir(parents=True)
        (base / f"2026-09-{i:02d}/x.md").write_text("x")
    world.run()
    days = sorted(x.name for x in base.iterdir())
    assert len(days) == 14
    assert days[-1] == TODAY
    assert days[0] == "2026-09-08"


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_an_explicit_true_applies(world, value):
    ctx = world.ctx()
    ctx.cfg["routines"]["clean"]["apply"] = value
    res = clean.run(ctx, today=TODAY)
    assert res.summary.startswith("proposed"), res.summary
    assert world.read(GD) == TARGET
    assert not clean.apply_enabled({})


def test_huge_diff_is_truncated_and_nothing_else_is_lost(world):
    def target(name):
        original = "\n".join(f"- [x] {name} línea vieja {i}" for i in range(3000)) + "\n"
        candidate = "\n".join(f"- [x] {name} línea nueva {i}" for i in range(3000)) + "\n"
        vv = validate.Verdict(changes=[f"struck: - algo de {name} — reason: ya no (ver [[{name}]])"])
        return {"path": f"entities/infra/{name}.md", "reason": "disorder queue", "original": original,
                "candidate": candidate, "verdict": validate.Verdict(changes=[f"removed alias `{name}-x`"]),
                "moved": [], "neighbors": [{"path": f"entities/infra/neighbor-{name}.md", "verdict": vv,
                                            "ignored": []}]}

    cleaner = clean.Cleaner(world.ctx(), today=TODAY)
    d = cleaner.detail_of([target("uno"), target("dos")])
    assert len(d) < 20000
    for name in ("uno", "dos"):
        assert f"=== entities/infra/{name}.md" in d
        assert f"removed alias `{name}-x`" in d
        assert f"--- neighbor entities/infra/neighbor-{name}.md: OK" in d
        assert f"struck: - algo de {name} — reason: ya no" in d
    assert d.count("[diff truncated: ") == 2
    assert "more characters; full diff in " in d
    assert str(world.state / "proposals" / TODAY / "entities__infra__uno.diff") + "]" in d


def test_model_failure_puts_the_target_in_backoff(world):
    # Si el modelo se cae (timeout), la misma nota no puede volver primera cada noche.
    world.apply = True
    world.claude.mode = "FAIL"
    assert world.run().status == "fail"
    rejection = world.registry()["rejections"][GD]
    assert rejection["bytes"] == len(TARGET.encode())
    assert "model failed" in rejection["reason"]
    world.claude.mode = "normal"
    world.claude.calls.clear()
    res = world.run()
    assert f"=== {GD}" not in res.detail and not world.claude.calls


def test_model_failure_on_the_second_target_keeps_the_first(world):
    # El trabajo ya aprobado del primer objetivo no se tira porque falle el segundo.
    world.add_second_target()
    world.apply = True
    normal = world.claude

    def flaky(prompt, **kw):
        if "nas-viejo" in prompt:
            normal.calls.append({"prompt": prompt})
            return 124, "Claude did not finish within 900 s"
        return normal(prompt, **kw)
    world.claude = flaky
    res = clean.run(world.ctx(), today="2026-10-07")
    world.claude = normal
    assert res.status == "ok", res
    assert "model failed on 1" in res.summary
    assert "title: gconsole" in world.read(GD)
    assert "~~" in world.read("entities/infra/nas-viejo.md")
    assert "model failed" in world.registry()["rejections"]["entities/infra/nas-viejo.md"]["reason"]


def test_neighbor_calls_are_capped(world):
    # Un hub con muchas vecinas no puede costar una llamada por vecina sin tope.
    for i in range(clean.MAX_NEIGHBORS + 5):
        (world.vault / f"entities/infra/vecina-{i:02d}.md").write_text(
            NEIGHBOR.replace("UPS desktop", f"vecina {i}").replace("ups-desktop", f"vecina-{i:02d}"))
    from .conftest import commit_old
    commit_old(world.vault, "vecinas")
    res = world.run(max_notes=100)
    neighbor_calls = [c for c in world.claude.calls if "<nota_actual>" not in c["prompt"]]
    assert len(neighbor_calls) == clean.MAX_NEIGHBORS
    assert "neighbors not checked" in res.detail


def test_failed_target_leaves_no_strikes_on_a_shared_neighbor(world):
    # A tacha la vecina compartida y después el modelo se cae: lo de A no puede colarse
    # al vault por B, que tacha sobre el texto que dejó A.
    import subprocess
    world.add_second_target(own_neighbor=False)
    (world.vault / UPS).write_text(NEIGHBOR + "- protegerá [[nas-viejo]]\n")
    (world.vault / "entities/infra/zz-late.md").write_text(
        NEIGHBOR.replace("UPS desktop", "zz late").replace("ups-desktop", "zz-late"))
    commit_old(world.vault, "vecinas")
    world.apply = True
    normal = world.claude

    def flaky(prompt, **kw):
        if 'ruta="entities/infra/zz-late.md"' in prompt:
            raise subprocess.TimeoutExpired("claude", 900)
        return normal(prompt, **kw)
    world.claude = flaky
    res = world.run(max_notes=100)
    world.claude = normal
    assert res.status == "ok", res
    assert "model failed on 1" in res.summary
    ups = world.read(UPS)
    assert "~~protegerá [[nas-viejo]]~~ ← superseded" in ups
    assert "~~protegerá [[gamedesk]]~~" not in ups
    assert "model failed" in world.registry()["rejections"][GD]["reason"]
