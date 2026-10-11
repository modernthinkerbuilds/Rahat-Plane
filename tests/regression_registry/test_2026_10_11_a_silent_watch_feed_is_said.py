"""Regression (2026-10-11): a silent Watch feed is said, not computed on.

LIVE (QA-2026-10-04-21; vault/vitals.log): nothing came from the Watch
between Sep 28 00:52 and Oct 3 09:01 (5.3 days) and nothing said so:
Kobe's burn answers and 9 pm checks ran on missing data, and Huberman's
autorun saw no workout.

Fix: agents.the_scientist.state.watch_silence() reads the newest reading
of a metric only the Watch export writes (heart rate, steps, HRV, resting
HR; Kobe's /fix and the legacy Shortcut write active calories and
weight). Over 24 h old, Kobe's /week, /today, /pace, day-burn answers and
morning briefing lead with "⚠️ No Watch data since <time>, so burns since
then are missing until it syncs."; the 9 pm check says it can't see
today's burn instead of calling the day short; Huberman's autorun opens
with the same line. A database with no Watch data at all (no Watch set
up) stays quiet.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.quality_gate.test_telegram_reply_validity import _freeze_scientist, kobe  # noqa: F401
from tests.telegram_checks import reply_problems

MONDAY_9PM = datetime(2026, 10, 5, 21, 0)          # a CrossFit day in the default plan
NOTE = "No Watch data since Sat Oct 3 08:15"


def _rows(*rows):
    from agents.the_scientist import state
    con = state._db()
    con.executemany("INSERT INTO raw_vitals (metric_type, value, timestamp) VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()


def _silent():
    _rows(("heart_rate", 64.0, "2026-10-03 08:15:00"), ("steps", 120.0, "2026-10-02 22:00:00"))


def _fresh():
    _rows(("heart_rate", 71.0, "2026-10-05 20:30:00"))


def test_the_silence_is_measured_from_watch_only_metrics(kobe, monkeypatch):
    from agents.the_scientist import state
    _freeze_scientist(monkeypatch, MONDAY_9PM)
    assert state.watch_silence() is None                       # never any Watch data: quiet
    _rows(("active_calories", 850.0, "2026-10-05 20:55:00"))   # Kobe's own /fix row
    assert state.watch_silence() is None
    _silent()
    assert state.watch_silence() == NOTE                       # the /fix row doesn't count
    _fresh()
    assert state.watch_silence() is None


@pytest.mark.parametrize("ask", ["/week", "/today", "/pace", "yesterday"])
def test_kobes_burn_answers_lead_with_it_while_silent(kobe, monkeypatch, ask):
    _freeze_scientist(monkeypatch, MONDAY_9PM.replace(hour=12))
    _silent()
    out = kobe.route(ask)
    assert out.startswith(f"⚠️ {NOTE}, so burns since then are missing until it syncs."), out
    assert reply_problems(out) == [], out


@pytest.mark.parametrize("ask", ["/week", "/today", "/pace", "yesterday"])
def test_a_fresh_feed_adds_nothing(kobe, monkeypatch, ask):
    _freeze_scientist(monkeypatch, MONDAY_9PM.replace(hour=12))
    _rows(("heart_rate", 71.0, "2026-10-05 11:30:00"))
    assert "No Watch data" not in kobe.route(ask)


def test_the_9pm_check_will_not_call_a_silent_day_short(kobe, monkeypatch):
    _freeze_scientist(monkeypatch, MONDAY_9PM)
    _silent()
    out = kobe.maybe_recovery_nudge()
    assert out and NOTE in out and "can't see today's burn" in out, out
    assert "short)" not in out and reply_problems(out) == [], out


def test_the_9pm_check_with_a_fresh_feed_is_unchanged(kobe, monkeypatch):
    _freeze_scientist(monkeypatch, MONDAY_9PM)
    _fresh()
    out = kobe.maybe_recovery_nudge()
    assert out and "short)" in out and "No Watch data" not in out, out


def test_the_morning_briefing_says_it(kobe, monkeypatch):
    _freeze_scientist(monkeypatch, MONDAY_9PM.replace(hour=8, minute=0))
    _silent()
    out = kobe.maybe_morning_briefing()
    assert out and out.startswith(f"⚠️ {NOTE}"), out


def test_huberman_says_why_nothing_synced(kobe, tmp_path, monkeypatch):
    monkeypatch.delenv("HUBERMAN_AUTOCOOL", raising=False)
    from agents.huberman import context, handler
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: None)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "cf")
    _freeze_scientist(monkeypatch, MONDAY_9PM)
    _silent()
    out = handler.maybe_autocool(now=MONDAY_9PM.replace(minute=35))
    assert out.startswith(f"_{NOTE}._"), out
    assert "Trained? Reply *did the WOD*" in out
