"""Regression (2026-10-14): a window of events keeps a spread across the feeds.

With the feeds read in code, the inventory holds whole calendars: a
county library system alone lists 300+ family programs a month. The
digest, `/whatson`, the day shortcut and the concierge's context all took
`query_window(..., limit=N)`, which is ordered by time and cut at N, so a
weekend window was one library's Saturday-morning storytimes and nothing
from a museum, a garden or a stage.

Fix (bridges/events/store.query_window): `per_source_cap` keeps at most
that many rows per feed per day before the limit; the callers pass 3
(the digest, `/whatson`), 4 (the day shortcut) and 2 (the concierge).
"""
from __future__ import annotations

from datetime import datetime

import pytest

NOW = datetime(2026, 10, 14, 9, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    path = str(tmp_path / "ev.db")
    monkeypatch.setenv("RAHAT_EVENTS_DB", path)
    from bridges.events import store
    lib = [{"title": f"Storytime {i}", "start_ts": f"2026-10-17 09:{i * 4:02d}:00", "venue": f"Branch {i}",
            "city": "Alpha Town", "url": "", "categories": ["library", "kids"]} for i in range(12)]
    store.upsert_events(lib, "alpha-lib", now=NOW, path=path, source_kind="bibliocommons")
    store.upsert_events([{"title": "Animal Day", "start_ts": "2026-10-17 10:00:00", "venue": "Alpha Garden", "city": "Alpha Town",
                          "url": "https://g.test", "categories": ["garden", "family"]}], "alpha-garden", now=NOW, path=path,
                        source_kind="tribe")
    store.upsert_events([{"title": "Family Concert", "start_ts": "2026-10-17 11:30:00", "venue": "Alpha Hall", "city": "Alpha Town",
                          "url": "", "categories": ["music", "family"]}], "alpha-hall", now=NOW, path=path, source_kind="page")
    return path


def test_the_cap_keeps_every_feed_in_a_small_window(db):
    from bridges.events.store import query_window
    cut = query_window("2026-10-17", "2026-10-17", limit=6, path=db)
    assert {r["source_id"] for r in cut} == {"alpha-lib"}                       # the old cut: one branch's morning
    spread = query_window("2026-10-17", "2026-10-17", limit=6, per_source_cap=3, path=db)
    assert [r["source_id"] for r in spread] == ["alpha-lib", "alpha-lib", "alpha-lib", "alpha-garden", "alpha-hall", "alpha-lib"]
    assert spread[-1]["title"] == "Storytime 3"                                  # the rest follow, in time order
    whole = query_window("2026-10-17", "2026-10-17", limit=60, per_source_cap=3, path=db)
    assert len(whole) == 14 and [r["title"] for r in whole[-9:]] == [f"Storytime {i}" for i in range(3, 12)]
    one_feed = query_window("2026-10-17", "2026-10-17", limit=60, per_source_cap=3, categories=["library"], path=db)
    assert [r["title"] for r in one_feed] == [f"Storytime {i}" for i in range(12)]   # one feed reads as before


def test_the_digest_and_whatson_use_the_cap(db, monkeypatch):
    from bridges.events import digest
    out = digest.build_digest(NOW, path=db)
    assert "Animal Day" in out and "Family Concert" in out and out.count("Storytime") == 4    # six lines a day
    seen: dict = {}
    from bridges.events import store
    real = store.query_window

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)
    monkeypatch.setattr(store, "query_window", spy)
    from agents.genie import handler
    handler.handle_whats_on(now=NOW)
    assert seen.get("per_source_cap") == 3
