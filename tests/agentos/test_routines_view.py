import json
from datetime import date, datetime, timedelta, timezone

from cortex.agentos import routines_view as rv
from cortex.routines import report
from cortex.routines.base import Result

TZ = timezone(timedelta(hours=-3))
CFG = {"instance": {"name": "t"}, "alert_url": ""}


def test_entries_report_wins_and_never_ran_is_empty():
    reports = {"clean": {"routine": "clean", "ts": "2026-10-06T05:00:00-03:00", "status": "fail",
                         "summary": "x" * 400, "detail": "trace"}, "snapshot": None}
    hist = {"clean": [{"ts": "2026-10-06T05:00:00-03:00", "status": "fail", "summary": "s", "detail": "d",
                       "routine": "clean"}], "snapshot": []}
    items, full = rv.entries(["clean", "snapshot"], reports, hist)
    assert items[0]["ok"] is False and items[0]["source"] == "report"
    assert len(items[0]["summary"]) == rv.MAX_SUMMARY
    assert full["clean"]["detail"] == "trace"
    assert full["clean"]["history"] == [{"ts": "2026-10-06T05:00:00-03:00", "ok": False, "summary": "s", "detail": "d"}]
    assert items[1] == {"routine": "snapshot", "ts": None, "ok": None, "summary": None, "source": None}
    assert full["snapshot"] == {"entry": items[1], "detail": "", "history": []}


def test_healthy_means_last_ok_or_never_ran():
    assert rv.healthy(None) and rv.healthy({"status": "ok"}) and not rv.healthy({"status": "fail"})


def test_runs_per_day_counts_ok_and_fail_in_window():
    hist = {"snapshot": [{"ts": "2026-10-08T01:00:00-03:00", "status": "ok"},
                         {"ts": "2026-10-08T02:00:00-03:00", "status": "ok"},
                         {"ts": "2026-10-07T02:00:00-03:00", "status": "fail"},
                         {"ts": "2026-08-01T02:00:00-03:00", "status": "ok"},   # fuera de la ventana
                         {"ts": "garbage", "status": "ok"}, "not a dict"],
            "clean": []}
    assert rv.runs_per_day(hist, date(2026, 10, 8)) == [
        {"day": "2026-10-07", "routine": "snapshot", "ok": 0, "fail": 1},
        {"day": "2026-10-08", "routine": "snapshot", "ok": 2, "fail": 0},
    ]


def test_finished_in_window():
    since = datetime(2026, 10, 8, 10, 0, 0, tzinfo=TZ)
    reports = {"a": {"ts": "2026-10-08T10:01:00-03:00", "status": "ok"},
               "b": {"ts": "2026-10-08T09:00:00-03:00", "status": "ok"}, "c": None}
    out = rv.finished_in(reports, since, since + timedelta(minutes=5))
    assert [(r["routine"], r["ok"]) for r in out] == [("a", True)]


def test_reads_what_the_runner_writes(data):
    """El panel lee exactamente lo que routines/report.py escribe: vigente e historial."""
    for i, status in enumerate(("ok", "fail", "ok")):
        report.write_report("snapshot", Result(status, f"run {i}", f"detail {i}"), CFG,
                            now=datetime(2026, 10, 8, 10, i, tzinfo=TZ))
    rep = report.read_report("snapshot")
    items, full = rv.entries(["snapshot"], {"snapshot": rep}, {"snapshot": report.read_history("snapshot")})
    assert items[0]["summary"] == "run 2" and items[0]["ok"] is True
    assert [h["summary"] for h in full["snapshot"]["history"]] == ["run 2", "run 1", "run 0"]
    assert full["snapshot"]["history"][1]["ok"] is False
    json.dumps(full)   # serializable tal cual va a logs.json


def test_long_detail_is_cut_by_the_report(data):
    report.write_report("clean", Result("ok", "big", "x" * 30_000), CFG)
    _, full = rv.entries(["clean"], {"clean": report.read_report("clean")}, {})
    assert full["clean"]["detail"].endswith("[… cut at 20 KB]")
    assert len(full["clean"]["detail"]) < 21_000
