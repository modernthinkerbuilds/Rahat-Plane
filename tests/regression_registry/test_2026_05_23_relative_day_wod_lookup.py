"""Regression: relative-day gym-WOD lookup (#41, 2026-05-23).

The inconsistency
-----------------
"What is the WOD for Tuesday" routed to Kobe's gym-programming lookup
(handle_gym_wod_on) and returned the actual synced WOD. But "what is the
WOD today" did NOT — the dispatcher's gym-WOD routes only matched explicit
weekday names, so relative days ("today"/"tomorrow"/"yesterday") fell
through to Fraser, which re-designed a session instead of surfacing the
gym's programming. Same question, two different behaviors.

The fix
-------
A new dispatcher route `gym_wod_relative` matches a WOD/gym anchor + a
relative-day token, resolves the offset to a weekday index, and calls the
same handle_gym_wod_on. These tests pin the routing precedence (so the
named-day, cadence, and design paths are NOT disturbed) and the offset
math.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import dispatcher


class TestRelativeDayRouting:
    @pytest.mark.parametrize("msg", [
        "What is the WOD today ?",
        "what's the wod tomorrow",
        "gym wod yesterday",
        "what's at the gym today",
        "what is the wod for today",
    ])
    def test_relative_day_routes_to_gym_lookup(self, msg):
        assert dispatcher.match_route(msg) == "gym_wod_relative", (
            f"{msg!r} should look up the gym's programming, not be "
            f"re-designed by Fraser")

    def test_named_weekday_still_uses_named_route(self):
        # Must not have broken the existing named-day behavior.
        assert dispatcher.match_route("what is the WOD for Tuesday") == \
            "gym_wod_on_day"

    def test_workout_today_is_still_cadence(self):
        # "workout today" (no WOD/gym anchor) stays the cadence answer.
        assert dispatcher.match_route("what's my workout today") == \
            "workout_today"

    def test_bare_wod_without_day_falls_through(self):
        # No day token → not a gym lookup; falls through (to Fraser).
        assert dispatcher.match_route("what is the WOD") != "gym_wod_relative"

    def test_design_request_not_intercepted(self):
        # A design request mentioning "wod ... tomorrow" must reach Fraser.
        assert dispatcher.match_route(
            "design me a PRVN cleans wod for tomorrow") != "gym_wod_relative"


class TestRelativeDayOffsetMath:
    def _capture_idx(self, monkeypatch, msg):
        captured = {}
        from agents.the_scientist import handler as kobe

        def _fake(idx):
            captured["idx"] = idx
            return "OK"

        monkeypatch.setattr(kobe, "handle_gym_wod_on", _fake)
        result = dispatcher.dispatch(msg)
        assert result == "OK"
        return captured["idx"]

    def test_today_resolves_to_current_weekday(self, monkeypatch):
        idx = self._capture_idx(monkeypatch, "what is the WOD today")
        assert idx == datetime.now().weekday()

    def test_tomorrow_resolves_to_next_weekday(self, monkeypatch):
        idx = self._capture_idx(monkeypatch, "what's the wod tomorrow")
        assert idx == (datetime.now() + timedelta(days=1)).weekday()

    def test_yesterday_resolves_to_prior_weekday(self, monkeypatch):
        idx = self._capture_idx(monkeypatch, "gym wod yesterday")
        assert idx == (datetime.now() - timedelta(days=1)).weekday()
