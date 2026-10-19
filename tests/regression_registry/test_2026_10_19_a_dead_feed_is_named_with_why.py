"""Regression (2026-10-19): the health line names a dead feed with why.

LIVE: the 8:15 health line listed fifteen Genie feeds, one per line,
each "has fetched nothing ever (5 tries)", plus four stale pages and two
food feeds. The why was only in the ingest log: six sites answered the
reader 403 (Akamai, Cloudflare), one address returned 404, two host
names did not resolve, one feed looped on redirects, and four calendars
answered with nothing upcoming. Nothing the owner could act on, and
nothing that told the architect where to look.

Fix: every refresh row records its outcome when it yields nothing
(`events_refresh_log.outcome`: "HTTP 403", "no such host", "redirect
loop", "model error", "nothing upcoming; newest 2025-06-14", "nothing
listed; 3 skipped"); the health line groups the dead feeds by it, one
line per group, and keeps the old one-feed line (with the why) when a
group has one feed. A feed the registry marks `quiet` is named only when
its site fails. The food line says the nightly job's error the same way
and stops naming a retired feed. `python -m bridges.events --check`
reads sources once with the production client and writes nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

NOW = datetime(2026, 10, 19, 8, 15)


class HTTPError(Exception):                     # the name is what failure_reason reads, as with requests'
    def __init__(self, code: int):
        super().__init__(f"{code} Client Error: Forbidden for url: https://example.org/events")
        self.response = type("Resp", (), {"status_code": code})()


class ConnectionError(Exception):  # noqa: A001 — shadowing on purpose: requests' class has this name
    pass


class TooManyRedirects(Exception):
    pass


def _ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def _vevent(uid: str, title: str, start: str) -> str:
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\nDTSTART;TZID=America/Los_Angeles:{start}\r\nEND:VEVENT\r\n"


def _raiser(exc: Exception):
    def http(url: str) -> str:
        raise exc
    return http


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    return str(tmp_path / "events.db")


SOURCES = {
    "alpha-refuses": ({"id": "alpha-refuses", "kind": "ical", "url": "https://alpha.example/cal.ics"},
                      _raiser(HTTPError(403))),
    "beta-refuses": ({"id": "beta-refuses", "kind": "libcal", "url": "https://beta.example/ajax/calendar/list?c=1"},
                     _raiser(HTTPError(403))),
    "gamma-gone": ({"id": "gamma-gone", "kind": "ical", "url": "https://gamma.example/cal.ics"},
                   _raiser(HTTPError(404))),
    "delta-gone": ({"id": "delta-gone", "kind": "ical", "url": "https://delta.example/cal.ics"},
                   _raiser(ConnectionError("HTTPSConnectionPool(host='delta.example', port=443): Max retries exceeded "
                                           "(Caused by NameResolutionError(\"Failed to resolve 'delta.example'\"))"))),
    "eps-loops": ({"id": "eps-loops", "kind": "ical", "url": "https://eps.example/feed"},
                  _raiser(TooManyRedirects("Exceeded 30 redirects."))),
    "zeta-old": ({"id": "zeta-old", "kind": "ical", "url": "https://zeta.example/cal.ics"},
                 lambda url: _ics(_vevent("a@z", "Spring talk", "20250614T190000"),
                                  _vevent("b@z", "Winter talk", "20241201T190000"))),
    "eta-quiet": ({"id": "eta-quiet", "kind": "ical", "url": "https://eta.example/cal.ics", "quiet": True},
                  lambda url: _ics(_vevent("c@e", "Old lecture", "20260422T190000"))),
}


def _refresh_days(db, ids, days=4):
    from bridges.events import ingest
    for d in range(days):
        for sid in ids:
            src, http = SOURCES[sid]
            ingest.refresh_source(dict(src), today=NOW - timedelta(days=days - d), http=http, db_path=db)


@pytest.fixture
def health(monkeypatch):
    from core import health as h
    monkeypatch.setattr(h, "_registered_feed_ids", lambda: set(SOURCES) | {"legacy-feed"})
    monkeypatch.setattr(h, "_feed_name", lambda sid: sid.replace("-", " ").title())
    monkeypatch.setattr(h, "_quiet_feed_ids", lambda: {"eta-quiet"})
    return h


# ── the outcome each refresh row records ──────────────────────────────────
def test_each_refresh_records_why_it_yielded_nothing(db):
    from bridges.events import ingest, store
    want = {"alpha-refuses": "HTTP 403", "gamma-gone": "HTTP 404", "delta-gone": "no such host",
            "eps-loops": "redirect loop", "zeta-old": "nothing upcoming; newest 2025-06-14"}
    for sid, outcome in want.items():
        src, http = SOURCES[sid]
        r = ingest.refresh_source(dict(src), today=NOW, http=http, db_path=db)
        assert r["fetched"] == 0 and r["outcome"] == outcome, sid
        assert store.last_outcome(sid, path=db) == outcome, sid


def test_a_productive_refresh_records_no_outcome(db):
    from bridges.events import ingest, store
    src = {"id": "live-cal", "kind": "ical", "url": "https://live.example/cal.ics"}
    r = ingest.refresh_source(src, today=NOW, http=lambda url: _ics(_vevent("x@l", "Baby Bounce", "20261021T103000")),
                              db_path=db)
    assert r["fetched"] == 1 and r["outcome"] is None and store.last_outcome("live-cal", path=db) is None


def test_a_page_whose_model_call_fails_says_so(db):
    from bridges.events import ingest, readers

    def llm(prompt: str) -> str:
        raise ingest.ModelError("RESOURCE_EXHAUSTED: spending cap")
    src = {"id": "page-src", "name": "A page", "kind": "page", "url": "https://page.example/events"}
    r = ingest.refresh_source(src, today=NOW, llm=llm, http=lambda url: "<p>Fall Fair, Oct 24, 10 am</p>", db_path=db)
    assert r["outcome"] == "model error"
    assert readers.failure_reason(ingest.ModelError("timeout")) == "model error"


# ── the health line ─────────────────────────────────────────────────────
def test_dead_feeds_are_grouped_by_why(db, health):
    _refresh_days(db, list(SOURCES))
    lines = health.genie_dead_feeds(NOW, path=db)
    assert lines == [
        "Genie: 2 sites refuse the feed reader (HTTP 403): Alpha Refuses and Beta Refuses.",
        "Genie: 3 feed addresses are gone: Delta Gone (no such host), Eps Loops (redirect loop) and Gamma Gone (HTTP 404).",
        "Genie: the Zeta Old feed has fetched nothing ever (4 tries): it answers, but its newest event is dated 2025-06-14.",
    ], lines
    assert not any("Eta Quiet" in ln for ln in lines), "a quiet calendar with nothing upcoming is not a finding"


def test_one_feed_in_a_group_keeps_its_own_line_with_why(db, health):
    _refresh_days(db, ["alpha-refuses", "gamma-gone"])
    assert health.genie_dead_feeds(NOW, path=db) == [
        "Genie: the Alpha Refuses feed has fetched nothing ever (4 tries): the site refuses the reader (HTTP 403).",
        "Genie: the Gamma Gone feed has fetched nothing ever (4 tries): the address is gone (HTTP 404)."]


def test_a_quiet_calendar_is_named_when_its_site_fails(db, health):
    from bridges.events import ingest
    src = SOURCES["eta-quiet"][0]
    for d in range(4):
        ingest.refresh_source(dict(src), today=NOW - timedelta(days=4 - d), http=_raiser(HTTPError(403)), db_path=db)
    lines = health.genie_dead_feeds(NOW, path=db)
    assert lines == ["Genie: the Eta Quiet feed has fetched nothing ever (4 tries): the site refuses the reader (HTTP 403)."]


def test_a_row_from_before_outcomes_keeps_the_old_line(db, health):
    from bridges.events import store
    for d in range(4):
        store.mark_refresh("legacy-feed", now=NOW - timedelta(days=4 - d), fetched=0, path=db)
    assert health.genie_dead_feeds(NOW, path=db) == ["Genie: the Legacy Feed feed has fetched nothing ever (4 tries)."]


def test_stale_pages_share_one_line_and_a_quiet_calendar_is_left_alone(db, health):
    from bridges.events import store
    for sid, day in (("page-a", "2026-10-16"), ("page-b", "2026-10-17"), ("eta-quiet", "2026-04-22")):
        store.upsert_events([{"title": f"Event {sid}", "start_ts": f"{day} 10:00:00", "city": "Alpha Town"}], sid,
                            now=NOW - timedelta(hours=3), path=db, source_kind="page")
    assert health.genie_feeds_dated_past(NOW, path=db) == [
        "Genie: 2 feeds list only past events (a stale page, or a season over): Page A (newest 2026-10-16) and "
        "Page B (newest 2026-10-17)."]


def test_the_food_line_says_why_and_skips_a_retired_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from core import health as h
    from core.kb import schema as kbs
    from bridges.food import nightly
    path = str(tmp_path / "kb.db")
    con = kbs.connect(path)
    nightly._meta_set(con, "food_nightly:last_run", {"at": "2026-10-19 05:30"})
    nightly._meta_set(con, "food_feed:eater-sf", {"at": "2026-10-19 05:30", "ok": False, "fails": 3,
                                                  "error": "HTTPError: 403 Client Error: Forbidden for url: https://x"})
    nightly._meta_set(con, "food_feed:sfist-food", {"at": "2026-10-19 05:30", "ok": False, "fails": 3,
                                                    "error": "TooManyRedirects: Exceeded 30 redirects."})
    nightly._meta_set(con, "food_feed:mercury-things", {"at": "2026-10-19 05:30", "ok": False, "fails": 3,
                                                        "error": "HTTPError: 403 Client Error: Forbidden"})
    con.commit()
    con.close()
    assert h.food_feeds_silent(NOW, path=path) == [
        "Food: the Eater San Francisco feed has fetched nothing ever (3 nights): the site refuses the reader (HTTP 403).",
        "Food: the SFist Food feed has fetched nothing ever (3 nights): the address loops on redirects."]


# ── the words, and the check ────────────────────────────────────────────
@pytest.mark.parametrize("logged, reason", [
    ("HTTPError: 403 Client Error: Forbidden for url: https://www.example.gov/Events", "HTTP 403"),
    ("HTTPError: 404 Client Error: Not Found for url: https://www.example.org/RSSFeed.aspx", "HTTP 404"),
    ("ConnectionError: HTTPSConnectionPool(host='www.example.org', port=443): Max retries exceeded with url: / "
     "(Caused by NameResolutionError(\"<urllib3.connection.HTTPSConnection>: Failed to resolve\"))", "no such host"),
    ("ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))", "connection failed"),
    ("TooManyRedirects: Exceeded 30 redirects.", "redirect loop"),
    ("ReadTimeout: HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=30)", "timeout"),
])
def test_failure_reason_reads_the_logged_errors(logged, reason):
    from bridges.events.readers import failure_reason
    assert failure_reason(logged) == reason


def test_the_check_says_what_each_source_answered_and_writes_nothing(db, monkeypatch):
    from bridges.events import ingest
    sources = [SOURCES["alpha-refuses"][0], {"id": "live-cal", "kind": "ical", "url": "https://live.example/cal.ics"},
               {"id": "a-page", "kind": "page", "url": "https://page.example/events"},
               {"id": "a-search", "kind": "search", "url": "https://search.example/"}]
    monkeypatch.setattr(ingest, "load_sources", lambda: sources)

    def http(url: str) -> str:
        if "alpha" in url:
            raise HTTPError(403)
        if "live" in url:
            return _ics(_vevent("x@l", "Baby Bounce", "20261021T103000"), _vevent("y@l", "Toddler Time", "20261028T103000"))
        return "<h2>Fall Fair</h2><p>Oct 24, 10 am</p><p>Nov 7th: Lantern walk</p>"
    lines: list[str] = []
    rc = ingest.check(None, today=NOW, http=http, out=lines.append)
    assert rc == 1
    assert lines[0].startswith("BAD") and "alpha-refuses" in lines[0] and lines[0].endswith("HTTP 403")
    assert lines[1].startswith("ok") and "2 events, 2026-10-21 to 2026-10-28" in lines[1]
    assert lines[2].startswith("ok") and "2 dates" in lines[2]
    assert lines[3].startswith("ok") and "paid search" in lines[3]
    assert lines[-1] == "3 of 4 sources answered"
    from bridges.events import store
    assert store.last_refresh_at("live-cal", path=db) is None
