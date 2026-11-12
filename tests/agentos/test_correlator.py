from datetime import datetime, timedelta, timezone

import pytest

from cortex.agentos import pulses

TZ = timezone(timedelta(hours=-3))
T0 = datetime(2026, 10, 6, 0, 10, 0, tzinfo=TZ)


def ev(sec, type_="read", ref="entities/infra/server"):
    return {"ts": T0 + timedelta(seconds=sec), "type": type_, "ref": ref, "detail": "read_note"}


def post(sec, origin="client:a"):
    return {"ts": T0 + timedelta(seconds=sec), "origin": origin}


@pytest.fixture
def c():
    return pulses.Correlator()


def test_event_waits_and_takes_the_post_that_arrives_later(c):
    c.feed_cortex(ev(0), received=100.0)
    assert c.tick(101.0) == []                       # aún retenido
    c.feed_post(post(1), received=101.2)             # el POST llega tarde
    out = c.tick(102.6)
    assert [(e["type"], e["origin"]) for e in out] == [("read", "client:a")]
    assert c.tick(110.0) == []                       # el POST quedó usado: no hay consulta extra


def test_post_that_arrives_first_also_works(c):
    c.feed_post(post(0, "client:b"), received=100.0)
    c.feed_cortex(ev(1, "write"), received=100.5)
    assert [(e["type"], e["origin"]) for e in c.tick(103.1)] == [("write", "client:b")]


def test_no_close_post_is_unknown(c):
    c.feed_post(post(10), received=100.0)            # a 10 s: fuera de tolerancia
    c.feed_cortex(ev(0), received=100.0)
    assert [(e["type"], e["origin"]) for e in c.tick(102.6)] == [("read", "unknown")]


def test_leftover_post_is_a_query_and_a_burst_counts_once(c):
    for s in (0, 0.5, 1.0, 2.0):
        c.feed_post(post(s), received=100.0 + s)
    out = c.tick(106.0)
    assert [(e["type"], e["origin"], e["ref"]) for e in out] == [("query", "client:a", "cortex")]
    c.feed_post(post(10), received=110.0)            # 8 s después: otra sesión, otra consulta
    assert len(c.tick(114.0)) == 1


def test_burst_split_between_ticks_is_still_one(c):
    c.feed_post(post(0), received=100.0)
    assert len(c.tick(103.6)) == 1
    c.feed_post(post(1.5), received=101.5)           # misma ráfaga, sale en el tick siguiente
    assert c.tick(105.1) == []


def test_queries_from_different_origins_do_not_merge(c):
    c.feed_post(post(0, "client:a"), received=100.0)
    c.feed_post(post(0.2, "client:b"), received=100.2)
    assert sorted(e["origin"] for e in c.tick(104.0)) == ["client:a", "client:b"]
