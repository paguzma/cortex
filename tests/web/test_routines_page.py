from cortex import config, paths
from cortex.routines import report
from cortex.routines.base import Result
from cortex.secrets import LocalBackend


def test_list_shows_states(admin):
    report.write_report("snapshot", Result("fail", "push failed"), config.load())
    page = admin.get("/setup/routines").text
    assert "off (no credential)" in page and "push failed" in page
    LocalBackend(paths.secrets_file()).set("CLAUDE_CODE_OAUTH_TOKEN", "t")
    assert "off (no credential)" not in admin.get("/setup/routines").text


def test_alert_url(admin):
    assert admin.post("/setup/routines", data={"alert_url": "ftp://x"}).status_code == 400
    admin.post("/setup/routines", data={"alert_url": "https://ntfy.sh/t"})
    assert config.load()["alert_url"] == "https://ntfy.sh/t"


def test_edit_routine_rewrites_crontab(admin):
    r = admin.post("/setup/routines/clean", data={"enabled": "on", "schedule": "15 3 * * *",
                                                   "model": "claude-opus-5-5", "apply": "on"})
    assert r.status_code == 200 and "Saved" in r.text
    settings = config.load()["routines"]["clean"]
    assert settings == {"enabled": True, "schedule": "15 3 * * *", "model": "claude-opus-5-5",
                        "apply": True}
    crontab = (paths.state_dir() / "routines" / "crontab").read_text()
    assert "15 3 * * * /usr/local/bin/cortex-routine clean" in crontab
    admin.post("/setup/routines/clean", data={"schedule": "15 3 * * *"})
    assert config.load()["routines"]["clean"]["enabled"] is False
    assert "cortex-routine clean" not in (paths.state_dir() / "routines" / "crontab").read_text()


def test_bad_schedule_rejected(admin):
    assert admin.post("/setup/routines/snapshot", data={"schedule": "cada hora"}).status_code == 400


def test_history_shown(admin):
    for i in range(3):
        report.write_report("snapshot", Result("ok", f"run {i}", f"detalle {i}"), config.load())
    page = admin.get("/setup/routines/snapshot").text
    assert "Last 3 runs" in page and "detalle 2" in page


def test_run_now_launches_runner(admin, monkeypatch):
    from cortex.web import app
    launched = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda args, **kw: launched.append(args))
    assert admin.post("/setup/routines/snapshot/run").headers["location"] == "/setup/routines/snapshot"
    assert launched == [["cortex-routine", "snapshot"]]
    admin.post("/setup/routines/nope/run")
    assert len(launched) == 1


def test_timezone_change_restarts_scheduler(admin, monkeypatch):
    from cortex.web import app
    restarted = []
    monkeypatch.setattr(app.services, "restart", lambda name: restarted.append(name))
    admin.post("/setup/wizard/1", data={"timezone": "UTC", "base_url": "http://x"})
    assert restarted == []
    admin.post("/setup/wizard/1", data={"timezone": "America/Santiago", "base_url": "http://x"})
    assert restarted == ["supercronic"]


def test_invalid_saved_schedule_is_flagged(admin):
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    config.save(cfg)
    assert "not running (invalid schedule)" in admin.get("/setup/routines").text
    page = admin.get("/setup/routines/snapshot").text
    assert "is not valid, so it was left out of the crontab" in page


def test_routine_with_invalid_saved_schedule_can_be_turned_off(admin):
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    config.save(cfg)
    r = admin.post("/setup/routines/snapshot", data={"schedule": "*/60 * * * *"})
    assert r.status_code == 200 and "Saved" in r.text
    assert config.load()["routines"]["snapshot"]["enabled"] is False
    # Apagarla no abre la puerta a guardar otro horario inválido ni a encenderla así.
    assert admin.post("/setup/routines/snapshot", data={"schedule": "cada hora"}).status_code == 400
    assert admin.post("/setup/routines/snapshot",
                      data={"enabled": "on", "schedule": "*/60 * * * *"}).status_code == 400
