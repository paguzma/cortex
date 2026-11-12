import subprocess

from cortex.gitutil import git
from cortex.routines.snapshot import snapshot


def test_no_changes_no_remote(ctx):
    result = snapshot(ctx)
    assert result.status == "ok" and result.summary == "git: no changes · no remote"


def test_commits_changes(ctx):
    (ctx.vault / "nota.md").write_text("hola")
    result = snapshot(ctx, "chore(vault): prueba")
    assert result.status == "ok" and result.summary.startswith("git: 1 changes → ")
    assert git(ctx.vault, "log", "-1", "--format=%s") == "chore(vault): prueba"


def test_pushes_to_remote_and_follows_url_changes(ctx, tmp_path):
    first = tmp_path / "first.git"
    second = tmp_path / "second.git"
    for remote in (first, second):
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    ctx.cfg["git_remote"]["url"] = str(first)
    (ctx.vault / "a.md").write_text("a")
    assert snapshot(ctx).summary.endswith("· pushed")
    assert git(first, "rev-parse", "main") == git(ctx.vault, "rev-parse", "HEAD")
    ctx.cfg["git_remote"]["url"] = str(second)
    assert snapshot(ctx).status == "ok"
    assert git(second, "rev-parse", "main") == git(ctx.vault, "rev-parse", "HEAD")


def test_failed_push_keeps_local_commit(ctx, tmp_path):
    ctx.cfg["git_remote"]["url"] = str(tmp_path / "no-existe.git")
    (ctx.vault / "a.md").write_text("a")
    result = snapshot(ctx)
    assert result.status == "fail" and "push failed" in result.summary
    assert git(ctx.vault, "status", "--porcelain") == ""
