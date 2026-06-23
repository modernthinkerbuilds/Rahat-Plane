"""Regression (2026-06-22, Test-Lead round 3) — F5: handle_gym_wod_on_date
matches the synced GymDay purely on day-of-MONTH (``int(parts[1]) ==
target.day``) and ignores the weekday and the month entirely. Within one
month that's unique, so today's fix is correct for the common case. But a
stale multi-week blob that straddles a month boundary collides: a target of
e.g. Jul 1 matches a leftover 'Mon 1' header that meant Jun 1.

Low severity (needs a stale cross-month blob) but it's a silent
wrong-day answer — the precise failure class the date-aware fix set out to
kill. Fix: also require the weekday name to match (the header carries it),
or carry the full date through the sync. xfail until then.
"""
from __future__ import annotations

from datetime import datetime

import pytest


def _blob():
    from agents.the_scientist.protocols import GymDay
    # A stale blob: last month's 'Mon 1' (e.g. Jun 1) lingers; this month's
    # first day is a different weekday.
    return [
        GymDay(label="Mon 1", weekday="Mon", body="Old June 1 WOD\nLingering",
               strength="Back Squat", blockers=[]),
    ]


def test_cross_month_day_collision(monkeypatch):
    from agents.the_scientist import handler as k
    monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _blob())
    # Query July 1 (a Wednesday in 2026); the only blob entry is 'Mon 1'
    # (June 1). A correct lookup must NOT serve the stale June WOD.
    out = k.handle_gym_wod_on_date(datetime(2026, 7, 1))
    assert "Old June 1 WOD" not in out, (
        "served a different month's WOD on a day-of-month collision")
