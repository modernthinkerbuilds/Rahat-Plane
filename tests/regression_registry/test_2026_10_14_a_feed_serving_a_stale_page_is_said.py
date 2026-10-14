"""Regression (2026-10-14): the daily line names a feed whose newest event is past.

LIVE: the City of Mountain View page fetched fine for a browser all
autumn and showed March. A reader that filters to "today onward" finds
nothing, the refresh counts as unproductive, and the dead-feed check
(3+ days of nothing) is the only thing that says so: a feed that keeps
fetching an old calendar with a few rows never trips it.

Fix (core/health.genie_feeds_dated_past): a source still being tried
whose newest stored event is before today is named, with the date.
"""
from __future__ import annotations

from datetime import datetime

import pytest

NOW = datetime(2026, 10, 14, 8, 15)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    path = str(tmp_path / "ev.db")
    monkeypatch.setenv("RAHAT_EVENTS_DB", path)
    from bridges.events import store
    store.upsert_events([{"title": "Spring Fair", "start_ts": "2026-03-07 10:00:00", "city": "Alpha Town", "categories": []}],
                        "alpha-city", now=datetime(2026, 10, 13, 7), path=path, source_kind="page")
    store.upsert_events([{"title": "Baby Bounce", "start_ts": "2026-10-17 10:30:00", "city": "Alpha Town", "categories": []}],
                        "alpha-lib", now=datetime(2026, 10, 13, 7), path=path, source_kind="bibliocommons")
    store.upsert_events([{"title": "Old Show", "start_ts": "2026-05-01 19:00:00", "city": "Alpha Town", "categories": []}],
                        "retired-page", now=datetime(2026, 9, 1, 7), path=path, source_kind="page")
    return path


def test_a_stale_page_is_named_and_a_live_feed_is_not(db, monkeypatch):
    from core import health
    monkeypatch.setattr(health, "_feed_name", lambda sid: {"alpha-city": "Alpha city"}.get(sid, sid))
    assert health.genie_feeds_dated_past(NOW, path=db) == [
        "Genie: the Alpha city feed's newest event is dated 2026-03-07; it is serving a stale page."]


def test_a_source_no_longer_tried_is_not_a_finding(db):
    from core import health
    lines = health.genie_feeds_dated_past(NOW, path=db)
    assert not any("retired-page" in ln for ln in lines)


def test_the_check_is_in_the_daily_findings(db, monkeypatch):
    from core import health
    monkeypatch.setattr(health, "_feed_name", lambda sid: sid)
    for check in ("model_refusals", "lost_messages", "genie_dead_feeds", "genie_feeds_stale", "watch_silent",
                  "gym_plan_stale", "benji_feeds_in_error"):
        monkeypatch.setattr(health, check, lambda now, **kw: [])
    assert health.findings(NOW) == ["Genie: the alpha-city feed's newest event is dated 2026-03-07; it is serving a stale page."]
