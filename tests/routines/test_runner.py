import threading
import time

from cortex import config, paths
from cortex.gitutil import git
from cortex.routines import report, runner
from cortex.routines.base import Result
from cortex.secrets import LocalBackend


def test_main_usage(ready):
    assert runner.main([]) == 2
    assert runner.main(["nope"]) == 2


def test_snapshot_runs_and_reports(ready):
    (paths.vault_dir() / "nota.md").write_text("hola")
    assert runner.main(["snapshot"]) == 0
    assert report.read_report("snapshot")["summary"].startswith("git: 1 changes")
    assert runner.current_run() is None


def test_claude_routine_without_credential_is_skipped(ready):
    assert runner.run_routine("consolidate", claude=None) is None
    assert report.read_report("consolidate") is None
    assert runner.routine_state("consolidate", config.load(), {}) == "no-credential"


def test_disabled_routine_is_skipped(ready):
    cfg = config.load()
    cfg["routines"]["snapshot"]["enabled"] = False
    config.save(cfg)
    assert runner.run_routine("snapshot") is None


def test_routine_runs_then_snapshots(ready, monkeypatch):
    LocalBackend(paths.secrets_file()).set("CLAUDE_CODE_OAUTH_TOKEN", "t")
    seen = {}

    def fake_run(ctx):
        seen.update(model=ctx.model, env=ctx.env)
        (ctx.vault / "cambio.md").write_text("x")
        return Result("ok", "consolidated")
    monkeypatch.setitem(runner.ROUTINES, "consolidate", runner.Spec(fake_run, True))
    cfg = config.load()
    cfg["routines"]["consolidate"]["model"] = "claude-opus-5-5"
    config.save(cfg)
    written = runner.run_routine("consolidate")
    assert written["status"] == "ok"
    assert written["summary"].startswith("consolidated · git: 1 changes")
    assert seen["model"] == "claude-opus-5-5" and seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    assert git(paths.vault_dir(), "log", "-1", "--format=%s").startswith("chore(vault): snapshot after consolidate")


def test_unexpected_exception_is_fail(ready, monkeypatch):
    def boom(ctx):
        raise ValueError("se rompió")
    monkeypatch.setitem(runner.ROUTINES, "snapshot", runner.Spec(boom, False))
    assert runner.main(["snapshot"]) == 1
    current = report.read_report("snapshot")
    assert "ValueError" in current["summary"] and "Traceback" in current["detail"]


def test_secrets_failure_fails_claude_routines(ready, monkeypatch):
    def broken(cfg):
        raise ConnectionError("infisical caído")
    monkeypatch.setattr(runner, "resolve_env", broken)
    written = runner.run_routine("consolidate")
    assert written["status"] == "fail" and "ConnectionError" in written["summary"]
    assert runner.run_routine("snapshot")["status"] == "ok"


def test_routines_never_overlap(ready, monkeypatch):
    order = []

    def slow(ctx):
        order.append("slow-start")
        time.sleep(0.5)
        order.append("slow-end")
        return Result("ok", "slow")

    def fast(ctx):
        order.append("fast")
        return Result("ok", "fast")
    monkeypatch.setitem(runner.ROUTINES, "snapshot", runner.Spec(slow, False))
    LocalBackend(paths.secrets_file()).set("CLAUDE_CODE_OAUTH_TOKEN", "t")
    monkeypatch.setitem(runner.ROUTINES, "clean", runner.Spec(fast, True))
    t = threading.Thread(target=runner.run_routine, args=("snapshot",))
    t.start()
    time.sleep(0.1)
    runner.run_routine("clean")
    t.join()
    assert order == ["slow-start", "slow-end", "fast"]


def test_init_creates_archive_and_crontab(ready):
    assert (paths.data_dir() / "archive" / ".git").exists()
    assert "cortex-routine consolidate" in (paths.state_dir() / "routines" / "crontab").read_text()


def test_scheduled_state_flags_an_invalid_schedule_only_when_it_would_run(ready):
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    cfg["routines"]["consolidate"]["schedule"] = "*/60 * * * *"
    assert runner.scheduled_state("snapshot", cfg, {}) == "invalid-schedule"
    assert runner.scheduled_state("consolidate", cfg, {}) == "no-credential"
    cfg["routines"]["snapshot"]["enabled"] = False
    assert runner.scheduled_state("snapshot", cfg, {}) == "disabled"
