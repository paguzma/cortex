import subprocess

import httpx

from cortex import checks, config
from cortex.secrets import LocalBackend
from cortex import paths
from cortex.seed import seed_vault


class FakeRun:
    def __init__(self, returncode=0, stderr="", exc=None):
        self.returncode, self.stderr, self.exc, self.calls = returncode, stderr, exc, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.returncode, "ok", self.stderr)


def test_claude_without_credential():
    ok, detail = checks.check_claude({}, "opus", run=FakeRun())
    assert not ok and "credential" in detail


def test_claude_ok_passes_model_and_env():
    run = FakeRun()
    ok, _ = checks.check_claude({"CLAUDE_CODE_OAUTH_TOKEN": "t"}, "opus", run=run)
    args, kwargs = run.calls[0]
    assert ok and args[-2:] == ["--model", "opus"]
    assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "t"


def test_claude_failure_and_missing_cli():
    ok, detail = checks.check_claude({"ANTHROPIC_API_KEY": "k"}, "opus",
                                     run=FakeRun(returncode=1, stderr="invalid key"))
    assert not ok and "invalid key" in detail
    ok, detail = checks.check_claude({"ANTHROPIC_API_KEY": "k"}, "opus",
                                     run=FakeRun(exc=FileNotFoundError()))
    assert not ok and "not installed" in detail


def test_infisical_check():
    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"accessToken": "t"})
        return httpx.Response(200, json={"secrets": [{"secretKey": "A", "secretValue": "1"}]})
    ok, detail = checks.check_infisical("http://i", "id", "s", "p", "prod",
                                        http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ok and "1 secrets" in detail
    deny = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    ok, detail = checks.check_infisical("http://i", "id", "s", "p", "prod", http=deny)
    assert not ok and "401" in detail


def test_deploy_key(tmp_path):
    key = tmp_path / "deploy_key"
    assert checks.deploy_public_key(key) is None
    pub = checks.ensure_deploy_key(key)
    assert pub.startswith("ssh-ed25519 ") and checks.deploy_public_key(key) == pub
    assert checks.ensure_deploy_key(key) == pub


def test_git_remote_check(tmp_path):
    ok, _ = checks.check_git_remote("git@x:y.git", tmp_path / "k", tmp_path / "kh", run=FakeRun())
    assert ok
    ok, detail = checks.check_git_remote("git@x:y.git", tmp_path / "k", tmp_path / "kh",
                                         run=FakeRun(returncode=128, stderr="denied"))
    assert not ok and "denied" in detail


def test_health(data):
    seed_vault(paths.vault_dir(), "2026-10-08")
    LocalBackend(paths.secrets_file()).set("CLAUDE_CODE_OAUTH_TOKEN", "t")

    def down(request):
        raise httpx.ConnectError("down")
    rows = {r["name"]: r for r in checks.health(
        config.load(), http=httpx.Client(transport=httpx.MockTransport(down)))}
    assert rows["MCP"]["status"] == "fail"
    assert rows["Vault"]["status"] == "ok"
    assert rows["Marketplace"]["status"] == "fail"
    assert rows["Claude"]["status"] == "ok"
    assert rows["Remote backup"]["status"] == "off"
    assert "t" not in rows["Claude"]["detail"].split()


def test_health_flags_an_invalid_saved_schedule(data):
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    rows = {r["name"]: r for r in checks.health(
        cfg, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))}
    assert rows["Routines"]["status"] == "fail"
    assert "invalid schedule, not running: snapshot" in rows["Routines"]["detail"]


def test_health_shows_a_failed_run_and_an_invalid_schedule_together(data):
    from cortex.routines import report
    from cortex.routines.base import Result
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    report.write_report("consolidate", Result("fail", "boom"), cfg)
    rows = {r["name"]: r for r in checks.health(
        cfg, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))}
    assert rows["Routines"]["status"] == "fail"
    assert "last run failed: consolidate" in rows["Routines"]["detail"]
    assert "invalid schedule, not running: snapshot" in rows["Routines"]["detail"]


def test_health_ignores_an_invalid_schedule_of_a_routine_off_for_lack_of_credential(data):
    from cortex.routines.schedule import write_crontab
    cfg = config.load()
    cfg["routines"]["consolidate"]["schedule"] = "*/60 * * * *"
    write_crontab(cfg)
    rows = {r["name"]: r for r in checks.health(
        cfg, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))}
    assert rows["Claude"]["status"] == "off"
    assert rows["Routines"]["status"] == "ok"
