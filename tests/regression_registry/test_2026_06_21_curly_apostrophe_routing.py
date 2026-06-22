"""Regression (2026-06-21): iOS/Telegram curly apostrophe defeated routing.

"tomorrow's WOD" typed with a curly ’ (U+2019) matched NO dispatcher route
and fell to the reasoner (which still had the weekday bug → 'Mon 15'). The
dispatcher now ASCII-folds curly quotes before matching.
"""
from __future__ import annotations
import pytest
from core import dispatcher


@pytest.mark.parametrize("msg,expected", [
    ("what is tomorrow’s WOD", "rel_day_workout"),   # curly '
    ("what is tomorrow's WOD", "rel_day_workout"),         # straight '
    ("what’s my plan", "show_plan_this_week"),
    ("what’s my weight", "current_weight"),
])
def test_curly_apostrophe_still_routes(msg, expected):
    assert dispatcher.match_route(msg) == expected, (
        f"{msg!r} must route to {expected} despite the curly apostrophe")
