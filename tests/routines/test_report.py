import httpx
import pytest

from cortex import config
from cortex.routines import report
from cortex.routines.base import Result


def recorder(status=200):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    return calls, httpx.Client(transport=httpx.MockTransport(handler))


def test_result_rejects_unknown_status():
    with pytest.raises(ValueError):
        Result("falla", "x")


def test_write_and_read(data):
    cfg = config.load()
    report.write_report("snapshot", Result("ok", "3 changes", "detalle"), cfg)
    current = report.read_report("snapshot")
    assert current["status"] == "ok" and current["summary"] == "3 changes"
    assert report.read_report("nunca") is None


def test_detail_is_cut(data):
    report.write_report("clean", Result("ok", "x", "a" * 30_000), config.load())
    detail = report.read_report("clean")["detail"]
    assert len(detail) < 20_100 and detail.endswith("[… cut at 20 KB]")


def test_history_keeps_last_30_newest_first(data):
    cfg = config.load()
    for i in range(35):
        report.write_report("snapshot", Result("ok", f"run {i}"), cfg)
    history = report.read_history("snapshot")
    assert len(history) == 30
    assert history[0]["summary"] == "run 34" and history[-1]["summary"] == "run 5"


def test_alert_only_on_failure(data):
    cfg = config.load()
    cfg["alert_url"] = "https://hooks.example/cortex"
    calls, http = recorder()
    report.write_report("clean", Result("ok", "bien"), cfg, http=http)
    assert calls == []
    report.write_report("clean", Result("fail", "mal", "secreto en el detalle"), cfg, http=http)
    assert len(calls) == 1
    body = calls[0].read().decode()
    assert '"routine":"clean"' in body.replace(" ", "") and "secreto" not in body


def test_failed_alert_keeps_report(data, capsys):
    cfg = config.load()
    cfg["alert_url"] = "https://hooks.example/cortex"
    _, http = recorder(500)
    report.write_report("clean", Result("fail", "mal"), cfg, http=http)
    assert report.read_report("clean")["status"] == "fail"
    assert "alert failed" in capsys.readouterr().out
