from datetime import date

from cortex.agentos import pending

TEXTS = {
    "entities/projects/site.md": ("# Site\n\n- [pendiente] renew the domain 2026-09-01\n- ~~[pendiente] old thing~~\n"
                                  "- [fact] nothing to do\n", "2026-10-01"),
    "references/map.md": ("- [pending] update the map\n", "2026-10-02"),
    "decisions/2026-01-01--x.md": ("- [Pendiente] review this decision\n", None),
    "feedback/rule.md": ("- [pendiente] out of scope\n", "2026-10-03"),
    "episodes/2026/2026-10-01--e.md": ("- [pendiente] out of scope too\n", None),
}


def test_vault_pending_scope_struck_and_dates():
    items = pending.vault_pending(TEXTS)
    by_text = {i["text"]: i for i in items}
    assert set(by_text) == {"renew the domain 2026-09-01", "update the map", "review this decision"}
    assert by_text["renew the domain 2026-09-01"]["date"] == "2026-09-01"   # fecha del hecho
    assert by_text["update the map"]["date"] == "2026-10-02"                # si no, updated
    assert by_text["review this decision"]["date"] is None
    assert by_text["update the map"]["path"] == "references/map.md"


def test_failed_routines():
    reports = {"clean": {"status": "fail", "summary": "boom", "ts": "2026-10-07T05:00:00+00:00"},
               "snapshot": {"status": "ok", "summary": "fine", "ts": "2026-10-07T05:00:00+00:00"},
               "consolidate": None}
    items = pending.failed_routines(reports)
    assert items == [{"kind": "routine", "text": "clean failed: boom", "routine": "clean", "date": "2026-10-07"}]


def test_token_expiry_window():
    today = date(2026, 10, 8)
    cfg = lambda loaded: {"claude": {"token_loaded_at": loaded}}  # noqa: E731
    assert pending.token_expiry(cfg(None), today) == []
    assert pending.token_expiry(cfg("2026-01-01"), today) == []             # vence 2027-01-01: lejos
    soon = pending.token_expiry(cfg("2025-10-20"), today)                   # vence 2026-10-20
    assert len(soon) == 1 and "12 days" in soon[0]["text"] and soon[0]["kind"] == "token"
    assert "expired" in pending.token_expiry(cfg("2025-01-01T10:00:00"), today)[0]["text"]
    assert pending.token_expiry(cfg("garbage"), today) == []


def test_collect_sorts_counts_and_keeps_the_domain_hook_empty():
    out = pending.collect(TEXTS, {"clean": {"status": "fail", "summary": "x", "ts": "2026-08-01T00:00:00+00:00"}},
                          {"claude": {"token_loaded_at": None}}, date(2026, 10, 8))
    assert [i["kind"] for i in out["items"]] == ["routine", "vault", "vault", "vault"]
    assert out["items"][-1]["date"] is None                                 # sin fecha al final
    assert out["counts"] == {"vault": 3, "routine": 1, "token": 0, "review": 0}
    assert out["failures"] == [] and out["domain_proposals"] == []


def test_a_broken_source_is_named_not_zeroed():
    out = pending.collect(TEXTS, {"x": "not a dict"}, {}, date(2026, 10, 8))
    assert out["failures"] == ["routine"] and out["counts"]["routine"] is None
    assert out["counts"]["vault"] == 3


def test_domain_proposals_pass_through():
    from datetime import date
    from cortex.agentos import pending
    out = pending.collect({}, {}, {"claude": {"token_loaded_at": None}}, date(2026, 10, 8),
                          [{"id": "new:casa", "kind": "new", "text": "New domain «casa»", "ts": "x"}])
    assert out["domain_proposals"] == [{"id": "new:casa", "kind": "new", "text": "New domain «casa»"}]


# --- vigencia: hechos que pueden haber quedado viejos ---

REVIEW = {
    "entities/infra/nas.md": ("- [config] pool en RAIDZ1 (as of 2026-03)\n- [config] scrub (as of 2026-09)\n"
                              "- [config] ~~backup 02:00 (as of 2025-01)~~ ← superseded 2026-10-01: 04:00\n",
                              "2026-10-01", "infra"),
    "entities/tools/restic.md": ("- [gotcha] falla sin avisar\n", "2026-03-01", "tool"),
    "entities/projects/web.md": ("- [estado] fase 2 (as of 2026-09)\n", "2026-07-01", "project"),
    "feedback/estilo.md": ("- [preferencia] respuestas cortas (as of 2026-01-15)\n", "2026-10-01", "feedback"),
    "entities/people/ana.md": ("- [rol] CTO (as of 2026-09)\n", "2024-01-01", "person"),
    "episodes/2026/2026-01-01--e.md": ("- pasó algo (as of 2025-12)\n", "2026-01-01", "episode"),
}


def test_review_flags_old_as_of_facts_and_ignores_struck_ones():
    items = {i["path"]: i for i in pending.needs_review(REVIEW, date(2026, 10, 22))}
    assert items["entities/infra/nas.md"]["date"] == "2026-03"              # el más viejo vigente
    assert "1 fact" in items["entities/infra/nas.md"]["text"]               # el tachado no cuenta
    assert items["feedback/estilo.md"]["kind"] == "review"                  # feedback entra


def test_review_flags_notes_past_their_type_interval():
    items = {i["path"]: i for i in pending.needs_review(REVIEW, date(2026, 10, 22))}
    assert "not updated since 2026-03-01" in items["entities/tools/restic.md"]["text"]   # tool: 180 días
    assert "entities/projects/web.md" in items                              # project: 90 días
    assert "entities/people/ana.md" not in items                            # person: sin plazo
    assert not any(p.startswith("episodes/") for p in items)                # episodios: append-only


def test_review_is_quiet_for_a_fresh_vault():
    fresh = {"entities/infra/nas.md": ("- [config] pool (as of 2026-09)\n", "2026-10-01", "infra")}
    assert pending.needs_review(fresh, date(2026, 10, 22)) == []


def test_collect_counts_review_items_and_isolates_its_failure():
    out = pending.collect({}, {}, {"claude": {"token_loaded_at": None}}, date(2026, 10, 22), review=REVIEW)
    assert out["counts"]["review"] == 4
    broken = pending.collect({}, {}, {}, date(2026, 10, 22), review={"x.md": "not a tuple"})
    assert broken["failures"] == ["review"] and broken["counts"]["review"] is None


def test_review_items_go_after_what_really_waits_on_the_owner():
    # Los «por revisar» suelen ser viejos: ordenados por fecha taparían los pendientes reales.
    out = pending.collect(TEXTS, {}, {"claude": {"token_loaded_at": None}}, date(2026, 10, 22), review=REVIEW)
    kinds = [i["kind"] for i in out["items"]]
    assert kinds.index("review") > max(i for i, k in enumerate(kinds) if k != "review")
