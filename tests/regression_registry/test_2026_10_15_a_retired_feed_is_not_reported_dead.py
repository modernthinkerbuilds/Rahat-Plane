"""Regression pin — 2026-10-15: a feed retired from the registry is not
reported as dead for two more days.

Health line, Oct 15: "the City of Mountain View events feed has fetched
nothing since Aug 23 (119 tries)". That source left the registry on
Oct 14 (8b88530); its last try was the pass before. The dead-feed check
took "tried in the last two days" as "still being tried", so a retired
feed was named for two mornings after its retirement, and the owner
asked for it to be fixed.

Fix (core/health.genie_dead_feeds): a source that is no longer in the
registry (defaults plus the vault overlay, minus the disabled) is
retired, not broken, whatever its last try's date.
"""
from __future__ import annotations

from datetime import datetime

import pytest

NOW = datetime(2026, 10, 15, 8, 15)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    path = str(tmp_path / "ev.db")
    monkeypatch.setenv("RAHAT_EVENTS_DB", path)
    from bridges.events import store
    # A retired city page: tried three times a day until yesterday evening, never fetched anything.
    for day, hour in ((13, 7), (13, 12), (13, 18), (14, 7)):
        store.mark_refresh("old-city", now=datetime(2026, 10, day, hour), fetched=0, path=path)
    # A live feed that is broken: tried this morning, nothing for a week.
    for day in (12, 13, 14, 15):
        store.mark_refresh("new-cal", now=datetime(2026, 10, day, 7), fetched=0, path=path)
    return path


def test_a_feed_no_longer_in_the_registry_is_not_named(db, monkeypatch):
    from core import health
    monkeypatch.setattr(health, "_registered_feed_ids", lambda: {"new-cal"})
    monkeypatch.setattr(health, "_feed_name", lambda sid: {"new-cal": "New calendar"}.get(sid, sid))
    lines = health.genie_dead_feeds(NOW, path=db)
    assert lines == ["Genie: the New calendar feed has fetched nothing ever (4 tries)."]


def test_when_the_registry_cannot_be_read_every_tried_feed_counts(db, monkeypatch):
    from core import health
    monkeypatch.setattr(health, "_registered_feed_ids", lambda: None)
    monkeypatch.setattr(health, "_feed_name", lambda sid: sid)
    lines = health.genie_dead_feeds(NOW, path=db)
    assert any("old-city" in ln for ln in lines) and any("new-cal" in ln for ln in lines)


def test_the_registered_ids_come_from_the_registry_with_its_overlay(monkeypatch):
    from core import health
    from bridges.events import registry
    monkeypatch.setattr(registry, "load_sources", lambda: [{"id": "a"}, {"id": "b"}])
    assert health._registered_feed_ids() == {"a", "b"}
