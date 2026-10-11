"""Regression (2026-10-11): the weekend digest says when its feeds are stale.

LIVE (QA-2026-10-04-06; vault/events_ingest.log): from Sep 17 to Sep 20
the spend cap stopped every feed refresh (258 failed calls), and the 8 am
digest went out each morning without a word about it, closing with "From
your verified event feeds (refreshed through the day)".

Fix: bridges.events.store.last_productive_refresh() is the newest refresh
that fetched anything (zero-yield refreshes and the on-demand marker
don't count). When that is over 24 h old, the digest's second line reads
"⚠️ Feeds last refreshed Wed Sep 16, 6 pm — new events may be missing."
and the footer says "last refreshed …" instead of "refreshed through the
day". Fresh feeds, or a store that never refreshed, change nothing.

Invented towns and venues throughout.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.telegram_checks import reply_problems

FRIDAY_8AM = datetime(2026, 9, 18, 8, 0)
EVENTS = [{"title": "Plaza Concert", "start_ts": "2026-09-19 18:00:00", "venue": "Plaza",
           "city": "Beta City", "url": "https://events.example/concert"},
          {"title": "Kids Craft Morning", "start_ts": "2026-09-20 10:00:00", "venue": "Beta Library",
           "city": "Beta City", "url": "https://events.example/craft"}]


@pytest.fixture
def feeds(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    db = str(tmp_path / "events.db")
    monkeypatch.setenv("RAHAT_EVENTS_DB", db)
    from bridges.events import digest, store

    def fed(at: datetime, events=EVENTS, source="alpha-library"):
        store.upsert_events(list(events), source, now=at, path=db, source_kind="page")

    def build(now=FRIDAY_8AM):
        return digest.build_digest(now, path=db)

    return {"fed": fed, "build": build, "store": store, "db": db}


def test_a_digest_on_feeds_two_days_old_says_so(feeds):
    feeds["fed"](datetime(2026, 9, 16, 18, 0))
    out = feeds["build"]()
    lines = out.split("\n")
    assert lines[1] == "⚠️ _Feeds last refreshed Wed Sep 16, 6 pm — new events may be missing._", out
    assert "(last refreshed Wed Sep 16, 6 pm)" in out and "refreshed through the day" not in out
    assert "Plaza Concert" in out and reply_problems(out) == [], out


def test_refreshes_that_fetched_nothing_do_not_count(feeds):
    feeds["fed"](datetime(2026, 9, 16, 18, 0))
    for h in (6, 12, 18, 24, 30):                              # the spend-cap shape: every pass empty
        at = datetime(2026, 9, 16, 18, 0) + timedelta(hours=h)
        feeds["fed"](at, events=[])
        feeds["store"].mark_refresh("_on_demand", now=at, fetched=0, path=feeds["db"])
    assert "Feeds last refreshed Wed Sep 16, 6 pm" in feeds["build"]()


def test_fresh_feeds_change_nothing(feeds):
    feeds["fed"](datetime(2026, 9, 18, 6, 30))
    out = feeds["build"]()
    assert "Feeds last refreshed" not in out and "refreshed through the day" in out


def test_a_store_that_never_refreshed_says_nothing_about_it(feeds):
    from bridges.events import digest
    assert digest.feeds_stale(FRIDAY_8AM, path=feeds["db"]) is None


def test_the_time_reads_the_household_way():
    from bridges.events.digest import _when
    assert _when(datetime(2026, 9, 16, 18, 0)) == "Wed Sep 16, 6 pm"
    assert _when(datetime(2026, 9, 16, 0, 30)) == "Wed Sep 16, 12:30 am"
