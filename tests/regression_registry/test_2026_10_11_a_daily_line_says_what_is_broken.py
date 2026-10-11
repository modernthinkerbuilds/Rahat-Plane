"""Regression (2026-10-11, D6): a daily line says what is broken.

LIVE: silent failures were the month's pattern, each found by hand weeks
late. Genie's city calendar fetched nothing on every refresh from late
August; Benji's Microsoft feed never fetched a role from launch, and two
job boards went dark in early October; the Watch fed nothing from Sep 28
to Oct 3; the spending cap refused every feed refresh from Sep 17 to 20.
Each bot logged it. Nobody was told.

Fix (core/health.py; one tick entry in new_plane/miya_runner/__main__.py):
read-only checks over what the bots already record, and one Miya message
at 08:15, once a day, only when something is wrong (owner decision D6):
  * a Genie feed still tried but fetching nothing for 3+ days;
  * Genie's feeds as a whole not fed for over a day;
  * the Watch silent for over a day;
  * a Benji job feed in error for 3+ days;
  * the model refusing calls in the last day.
Under test mode the line is off unless a test asks (RAHAT_HEALTH_LINE=1),
so the tick tests that count messages are never disturbed.
"""
from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

NOW = datetime(2026, 10, 11, 8, 15)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("RAHAT_JOBSEARCH_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("RAHAT_HEALTH_LINE", "1")
    from core import health
    importlib.reload(health)
    import agents.the_scientist.state as kstate
    monkeypatch.setattr(kstate, "watch_silence", lambda now=None, hours=24: None)
    return {"dir": tmp_path, "health": health, "events": str(tmp_path / "events.db"),
            "jobs": str(tmp_path / "jobs.db")}


def _feed(path, source, at, fetched):
    from bridges.events import store
    store.mark_refresh(source, now=at, fetched=fetched, path=path)


def test_a_feed_still_tried_but_fetching_nothing_is_named(sandbox):
    h, db = sandbox["health"], sandbox["events"]
    _feed(db, "feed-a", datetime(2026, 10, 1, 7, 0), 12)
    for day in range(2, 12):                                  # ten empty tries
        _feed(db, "feed-a", datetime(2026, 10, day, 7, 0), 0)
    _feed(db, "feed-b", datetime(2026, 10, 11, 7, 0), 20)    # healthy
    _feed(db, "feed-c", datetime(2026, 9, 1, 7, 0), 0)       # no longer tried: retired, not broken
    assert h.genie_dead_feeds(NOW) == ["Genie: the feed-a feed has fetched nothing since Oct 1 (10 tries)."]


def test_feeds_not_fed_for_a_day_are_said(sandbox):
    h, db = sandbox["health"], sandbox["events"]
    _feed(db, "feed-b", datetime(2026, 10, 10, 6, 0), 20)
    assert h.genie_feeds_stale(NOW) == ["Genie: the event feeds haven't brought anything in since Sat Oct 10, 6 am."]
    _feed(db, "feed-b", datetime(2026, 10, 11, 7, 0), 20)
    assert h.genie_feeds_stale(NOW) == []


def test_a_silent_watch_is_said(sandbox, monkeypatch):
    import agents.the_scientist.state as kstate
    monkeypatch.setattr(kstate, "watch_silence", lambda now=None, hours=24: "No Watch data since Sat Oct 3 08:15")
    assert sandbox["health"].watch_silent(NOW) == ["Kobe: No Watch data since Sat Oct 3 08:15."]


def test_a_job_feed_in_error_for_three_days_is_said(sandbox):
    from bridges.jobsearch import store
    store.record_source_run("Board A", 0, now=datetime(2026, 10, 5, 6, 0), state="error", note="404")
    store.record_source_run("Board B", 0, now=datetime(2026, 10, 10, 6, 0), state="error", note="404")  # 1 day
    store.record_source_run("Board C", 40, now=datetime(2026, 10, 11, 6, 0))
    assert sandbox["health"].benji_feeds_in_error(NOW) == ["Benji: 1 job feed in error since Oct 5: Board A."]


def test_refused_model_calls_are_counted_from_the_logs(sandbox):
    log = Path(sandbox["dir"]) / "events_ingest.log"
    log.write_text("2026-10-09 18:00:01,000 WARNING 429 RESOURCE_EXHAUSTED old\n"
                   "2026-10-10 18:00:01,000 WARNING 429 RESOURCE_EXHAUSTED: monthly spending cap\n"
                   "2026-10-11 07:00:02,000 WARNING spending cap exceeded\n"
                   "2026-10-11 07:00:03,000 INFO refreshed 0\n")
    assert sandbox["health"].model_refusals(NOW) == [
        "Model: 2 calls refused since Sat 18:00 (quota or spending cap); the bots' AI answers fail until it clears."]


def test_the_line_comes_once_at_8_15_and_only_when_something_is_wrong(sandbox):
    h, db = sandbox["health"], sandbox["events"]
    assert h.maybe_health_line(NOW) is None                   # nothing recorded: nothing to say
    assert h.maybe_health_line(datetime(2026, 10, 12, 8, 0)) is None   # before 08:15
    _feed(db, "feed-b", datetime(2026, 10, 10, 6, 0), 20)
    out = h.maybe_health_line(datetime(2026, 10, 12, 8, 15))
    assert out == ("🩺 Health check, Mon Oct 12: something needs a look.\n"
                   "• Genie: the event feeds haven't brought anything in since Sat Oct 10, 6 am."), out
    assert h.maybe_health_line(datetime(2026, 10, 12, 9, 0)) is None   # once a day
    from tests.telegram_checks import reply_problems
    assert reply_problems(out) == []


def test_under_test_mode_the_line_waits_to_be_asked(sandbox, monkeypatch):
    _feed(sandbox["events"], "feed-b", datetime(2026, 10, 10, 6, 0), 20)
    monkeypatch.delenv("RAHAT_HEALTH_LINE")
    assert sandbox["health"].maybe_health_line(NOW) is None
    monkeypatch.setenv("RAHAT_HEALTH_LINE", "1")
    assert sandbox["health"].maybe_health_line(NOW) is not None


def test_miyas_tick_sends_it(sandbox, monkeypatch):
    import agents.the_scientist.handler as kh
    for name in ("maybe_morning_briefing", "maybe_weekly_reset", "maybe_recovery_nudge", "maybe_walk_nudge"):
        monkeypatch.setattr(kh, name, lambda: None)
    import agents.huberman.handler as hub
    monkeypatch.setattr(hub, "maybe_autocool", lambda *a, **k: None)
    monkeypatch.setattr(sandbox["health"], "maybe_health_line", lambda *a, **k: "🩺 Health check: x")
    from new_plane.miya_runner.__main__ import _fire_nudges
    tg = MagicMock()
    tg.send_message = MagicMock(return_value={"ok": True, "result": {"message_id": 1}})
    _fire_nudges(tg, "1")
    tg.send_message.assert_called_once_with("1", "🩺 Health check: x")
