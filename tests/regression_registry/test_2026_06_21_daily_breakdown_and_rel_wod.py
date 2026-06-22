"""Regression (2026-06-21): two live regressions surfaced in Telegram.

1. "Give me calories by the day" had NO deterministic route, so it fell to
   the reasoner — which has only the weekly total and (correctly) refused to
   invent a per-day split ("I don't have your daily breakdown on file").
   Fix: handle_daily_burn_breakdown() + the `daily_breakdown` route, ordered
   BEFORE weekly_remaining (both mention burn/cal).

2. "What is tomorrow's WOD" (relative day, possessive, BEFORE the gym noun)
   matched no route and fell to the reasoner, which answered inconsistently
   ("no WOD synced" / "Monday is a rest day") while the explicit "Monday's
   WOD" worked. Fix: the `rel_day_workout` route → same handler as the
   relative-WOD lookup, which returns the gym WOD regardless of cadence.
"""
from __future__ import annotations

import pytest

from core import dispatcher


@pytest.mark.parametrize("msg", [
    "Give me calories by the day",
    "calories by the day",
    "burn each day",
    "daily breakdown",
    "daily burn breakdown",
    "day by day calories",
    "show me burn by the day",
])
def test_calories_by_the_day_routes_to_breakdown(msg):
    assert dispatcher.match_route(msg) == "daily_breakdown", (
        f"{msg!r} must hit the deterministic per-day breakdown, not the "
        "reasoner")


@pytest.mark.parametrize("msg", [
    "what is tomorrow's WOD",
    "tomorrow's wod",
    "tomorrows workout",
    "tomorrow's session",
    "whats tomorrows gym",
])
def test_tomorrow_wod_routes_to_rel_day(msg):
    assert dispatcher.match_route(msg) == "rel_day_workout", (
        f"{msg!r} must resolve deterministically to the gym WOD, not fall to "
        "the reasoner")


def test_breakdown_does_not_steal_weekly_remaining():
    # "per day" in the remaining sense ("≈ 206 kcal/day") must NOT be a
    # breakdown; the weekly-remaining read must still win / stay distinct.
    assert dispatcher.match_route(
        "how much do I have left this week") == "weekly_remaining"
    assert dispatcher.match_route("whats remaining this week") == "weekly_remaining"


def test_today_wod_still_falls_through_to_fraser():
    # "today"/"tonight" stay OUT of the relative route — that's Fraser's
    # daily-driver design intent, not a schedule peek.
    assert dispatcher.match_route("what's tonight's workout") != "rel_day_workout"


def test_breakdown_handler_renders_all_days():
    from agents.the_scientist import handler as k
    out = k.handle_daily_burn_breakdown()
    assert "by day" in out.lower()
    for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert name in out, f"breakdown missing {name}:\n{out}"
    assert "planned" in out  # has the totals footer


def test_relative_wod_is_date_aware(monkeypatch):
    """Bug 2026-06-21: 'tomorrow's WOD' returned 'Mon 15' (last Monday) when
    tomorrow was Mon the 22nd — handle_gym_wod_on matched only the weekday and
    the blob's FIRST Monday won. handle_gym_wod_on_date matches the header
    day-of-month, so it picks the right week."""
    from datetime import datetime
    from agents.the_scientist import handler as k
    from agents.the_scientist.protocols import GymDay
    blob = [
        GymDay(label="Mon 15", weekday="Mon", body="Clean & Jerk\nThe Wolf",
               strength="Clean & Jerk", blockers=[]),
        GymDay(label="Mon 22", weekday="Mon", body="Back Squat 5x5\nFran",
               strength="Back Squat", blockers=[]),
    ]
    monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: blob)
    # the 22nd must select 'Mon 22', NOT the first Monday in the blob
    out_22 = k.handle_gym_wod_on_date(datetime(2026, 6, 22))
    assert "Mon 22" in out_22 and "Mon 15" not in out_22
    # a date not in the blob must say so, never show a different day
    out_missing = k.handle_gym_wod_on_date(datetime(2026, 6, 29))
    assert "no WOD synced" in out_missing
