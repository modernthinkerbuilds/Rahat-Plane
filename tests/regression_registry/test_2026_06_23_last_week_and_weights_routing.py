"""Regression (2026-06-23): two live Telegram mis-routes the user reported as
"these were working last week".

BUG 1 — "What weights should I use for tomorrow's WOD?" answered "I don't have
tomorrow's WOD synced yet" while "What is tomorrow's WOD?" returned it. Root
cause was the typo "tommorws" matching no route so BOTH fell to the (flaky)
reasoner; the weights phrasing must resolve to the deterministic date-aware
gym-WOD lookup that DOES find the synced day.

BUG 2 — "How many calories did I burn last week" returned THIS week's
"Week so far / Remaining" block (0 kcal) instead of last week's total. The
weekly_remaining regex matched "how many … burn … week" and, ordered before
last_week, stole the query. "last week" came AFTER the burn keyword, which the
old _LAST_WEEK_RE (which required "last week <keyword>") didn't match. Fix:
bidirectional _LAST_WEEK_RE + last_week ordered BEFORE weekly_remaining.
"""
from __future__ import annotations

import pytest

from core import dispatcher


# ── BUG 1: weights-for-tomorrow resolves to the deterministic WOD lookup ──
@pytest.mark.parametrize("msg", [
    "What weights should I use for tommorws WOD?",   # the exact live phrasing
    "what weights should I use for tomorrow's WOD",
    "what weights for tomorrows wod",
])
def test_weights_for_tomorrow_routes_to_wod_lookup(msg):
    assert dispatcher.match_route(msg) == "rel_day_workout", (
        f"{msg!r} must hit the date-aware gym-WOD lookup, not the reasoner "
        "(which answered 'not synced')")


# ── BUG 2: "last week" burn queries hit last_week, not weekly_remaining ──
@pytest.mark.parametrize("msg", [
    "How many calories did I burn last week",
    "how many calories did I burn last week",
    "calories burned last week",
    "how much did I burn last week",
    "what was my burn last week",
    "how many calories last week",
])
def test_last_week_burn_routes_to_last_week(msg):
    assert dispatcher.match_route(msg) == "last_week", (
        f"{msg!r} asks about the COMPLETED week — must route to last_week, "
        "not weekly_remaining (this week's 'Remaining' block)")


@pytest.mark.parametrize("msg", [
    "how much do I have left this week",
    "whats remaining this week",
    "how many calories can I still burn this week",
    "how much burn remaining this week",
])
def test_current_week_stays_weekly_remaining(msg):
    # The fix must NOT steal current-week queries — these have no "last week".
    assert dispatcher.match_route(msg) == "weekly_remaining", (
        f"{msg!r} is a CURRENT-week query and must stay weekly_remaining")


def test_daily_breakdown_unaffected():
    assert dispatcher.match_route("calories by the day") == "daily_breakdown"
