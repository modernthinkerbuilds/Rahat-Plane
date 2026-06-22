"""Regression: relative-day gym-WOD lookup (#41, 2026-05-23).

The inconsistency
-----------------
"What is the WOD for Tuesday" routed to Kobe's gym-programming lookup
(handle_gym_wod_on) and returned the actual synced WOD. But "what is the
WOD tomorrow"/"yesterday" did NOT — the dispatcher's gym-WOD routes only
matched explicit weekday names, so those relative days fell through to
Fraser and got re-designed instead of surfacing the gym's programming.

The fix
-------
A `gym_wod_relative` dispatcher route matches a WOD/gym anchor + a
relative-day token (tomorrow/yesterday), resolves the offset, and calls
the same handle_gym_wod_on. DELIBERATE EXCLUSION: "today"/"tonight" are
NOT gym lookups — "what's the WOD today" is Fraser's daily-driver design
intent (the composer folds in today's synced gym WOD), a long-standing
contract pinned by tests/test_fraser_delegation.py. So a NAMED weekday or
tomorrow/yesterday → Kobe lookup; today → Fraser.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import dispatcher


class TestRelativeDayRouting:
    @pytest.mark.parametrize("msg", [
        "what's the wod tomorrow",
        "gym wod yesterday",
        "what is the wod for tomorrow",
        "what's at the gym tomorrow",
    ])
    def test_relative_day_routes_to_gym_lookup(self, msg):
        assert dispatcher.match_route(msg) == "gym_wod_relative", (
            f"{msg!r} should look up the gym's programming, not be "
            f"re-designed by Fraser")

    @pytest.mark.parametrize("msg", [
        "What is the WOD today ?",
        "what's the wod today",
        "what's at the gym today",
    ])
    def test_today_is_not_a_gym_lookup(self, msg):
        # "today" is Fraser's daily-driver design intent (the composer
        # folds in today's gym WOD), NOT a gym schedule peek — it must
        # NOT be claimed by the gym lookup route.
        assert dispatcher.match_route(msg) != "gym_wod_relative"

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

    def test_tomorrow_resolves_to_next_weekday(self, monkeypatch):
        idx = self._capture_idx(monkeypatch, "what's the wod tomorrow")
        assert idx == (datetime.now() + timedelta(days=1)).weekday()

    def test_yesterday_resolves_to_prior_weekday(self, monkeypatch):
        idx = self._capture_idx(monkeypatch, "gym wod yesterday")
        assert idx == (datetime.now() - timedelta(days=1)).weekday()
