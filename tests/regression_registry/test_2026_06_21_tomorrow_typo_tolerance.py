"""Regression (2026-06-21): mangled 'tomorrow' spellings fell to the reasoner.

Live failure (Telegram):
  "What weights should I use for tommorws WOD?"  → reasoner: "I don't have
                                                    tomorrow's WOD synced yet"
  "What is tommorws WOD?"                         → reasoner: <correct WOD>

Same input class, two different answers — because the typo-tolerant tomorrow
regex was `tom+or+ow`, which REQUIRES a trailing 'ow'. The user's actual typo
"tommorws" (t-o-m-m-o-r-w-s, dropped letters) matched neither gym-WOD route, so
BOTH phrasings fell through to the non-deterministic reasoner, which answered
inconsistently.

Fix: widen the tomorrow fragment to `tom+o?r+o?w?` (the o's and trailing w are
optional), so "tommorws" / "tomoro" / "tomorow" resolve deterministically to the
date-aware gym-WOD lookup — while "tomato"/"tom" alone still do NOT match.
"""
from __future__ import annotations

import pytest

from core import dispatcher


@pytest.mark.parametrize("msg", [
    "What weights should I use for tommorws WOD?",  # the exact live failure
    "What is tommorws WOD?",
    "tommorws wod",
    "tommorow wod",
    "tomorow workout",
    "tomoro session",
    "tomorrows gym",
])
def test_mangled_tomorrow_routes_deterministically(msg):
    assert dispatcher.match_route(msg) == "rel_day_workout", (
        f"{msg!r} must resolve to the deterministic date-aware gym-WOD "
        "lookup, not fall to the reasoner")


@pytest.mark.parametrize("msg", [
    "tomato wod",     # 'tom' + no r-after-o cluster → not tomorrow
    "tom wod",        # bare name
    "what is the WOD",  # no day token at all
])
def test_no_false_positive_on_lookalikes(msg):
    assert dispatcher.match_route(msg) != "rel_day_workout", (
        f"{msg!r} is NOT a tomorrow lookup and must not be intercepted")


def test_today_still_excluded():
    # 'today'/'tonight' remain Fraser's daily-driver design intent.
    assert dispatcher.match_route("what is the WOD today") != "rel_day_workout"
    assert dispatcher.match_route("whats my workout today") != "rel_day_workout"
