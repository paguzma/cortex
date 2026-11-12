"""Pruebas de pick.py."""
from datetime import datetime, timedelta, timezone

from cortex.routines.clean import pick

NOTES = {
    "entities/infra/pve.md": {"bytes": 38354, "struck": 16, "observations": 57},
    "entities/infra/home-assistant.md": {"bytes": 47475, "struck": 6, "observations": 99},
    "entities/infra/chica.md": {"bytes": 1200, "struck": 0, "observations": 3},
    "entities/infra/gamedesk.md": {"bytes": 9944, "struck": 2, "observations": 20},
    "feedback/regla.md": {"bytes": 90000, "struck": 40, "observations": 9},
}
PVE, GD = "entities/infra/pve.md", "entities/infra/gamedesk.md"


def paths(out):
    return [p for p, _ in out]


def test_summarize_counts():
    t = "---\ntitle: x\n---\n## Observaciones\n- [a] uno\n- ~~[b] dos~~ ← superseded 2026-10-01: tres\n- [c] ~~x~~ ← superseded 2026-10-02: y\n"
    r = pick.summarize(t)
    assert (r["struck"], r["observations"], r["bytes"]) == (2, 2, len(t.encode()))


def test_status_superseded_and_prose_are_state_changes():
    assert pick.state_changed("+status: archived\n")
    assert pick.state_changed("+- ~~[x] a~~ ← superseded 2026-10-07: b\n")
    assert pick.state_changed("+**`gconsole`: el PC de juegos, y nada más**\n")


def test_not_a_state_change():
    assert not pick.state_changed("+- [gotcha] algo nuevo\n+updated: '2026-10-07'\n+++ b/x.md\n+\n+## Observaciones\n+title: otro\n")


def test_disorder_queue_by_struck_and_respects_scope():
    assert paths(pick.pick(NOTES, set(), {})) == [PVE, "entities/infra/home-assistant.md"]


def test_changed_goes_first():
    out = pick.pick(NOTES, {GD}, {})
    assert out[0][0] == GD
    assert "changed state" in out[0][1]
    assert out[1][0] == PVE


def test_changed_out_of_scope_is_ignored():
    assert "feedback/regla.md" not in paths(pick.pick(NOTES, {"feedback/regla.md"}, {}))


def test_just_cleaned_does_not_return_until_it_grows():
    reg = {PVE: {"ts": "2026-10-06", "bytes": 30000, "struck": 16}}
    assert PVE not in paths(pick.pick(NOTES, set(), reg))
    reg = {PVE: {"ts": "2026-10-06", "bytes": 29000, "struck": 16}}  # 38354 > 1.3 × 29000
    assert PVE in paths(pick.pick(NOTES, set(), reg))


def test_just_cleaned_returns_if_it_gains_struck_lines():
    reg = {PVE: {"ts": "2026-10-06", "bytes": 38000, "struck": 10}}
    assert PVE in paths(pick.pick(NOTES, set(), reg))


def test_rejected_waits_7_days_with_same_bytes():
    now = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)
    ago = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
    rej = {PVE: {"ts": ago(1), "bytes": 38354, "reason": "invented data"},
           GD: {"ts": ago(1), "bytes": 9944, "reason": "invented data"}}
    picked = paths(pick.pick(NOTES, {GD}, {}, rejections=rej, now=now))
    assert PVE not in picked   # cola de desorden
    assert GD not in picked    # y también lo que cambió de estado
    assert "entities/infra/home-assistant.md" in picked


def test_rejected_returns_after_7_days():
    now = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)
    rej = {PVE: {"ts": (now - timedelta(days=7, minutes=1)).isoformat(), "bytes": 38354, "reason": "x"}}
    assert PVE in paths(pick.pick(NOTES, set(), {}, rejections=rej, now=now))


def test_rejected_returns_if_bytes_changed():
    now = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)
    rej = {PVE: {"ts": (now - timedelta(days=1)).isoformat(), "bytes": 38000, "reason": "x"}}
    assert PVE in paths(pick.pick(NOTES, set(), {}, rejections=rej, now=now))


def test_default_rejections_and_local_time():
    a_while_ago = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")   # sin huso
    rej = {PVE: {"ts": a_while_ago, "bytes": 38354, "reason": "x"}}
    assert PVE not in paths(pick.pick(NOTES, set(), {}, rejections=rej))
    assert PVE in paths(pick.pick(NOTES, set(), {}))


def test_small_clean_note_does_not_enter():
    assert "entities/infra/chica.md" not in paths(pick.pick(NOTES, set(), {}, limit=10))
