"""Unit tests for core.dispatcher (ADR-009, Option C).

What this file pins
-------------------
1. The feature flag — RAHAT_USE_DISPATCHER=0 disables the dispatcher.
2. Empty/None message handling — dispatch returns None, never crashes.
3. Each Route's regex matches its intended phrasings (and ONLY those).
4. Route order — gym-WOD-day fires before generic plan; slash always
   wins.
5. Handler exception safety — a crashing handler returns None, never
   propagates.

Notes
-----
- These tests DO NOT invoke real handlers — they use monkeypatched
  stubs so the dispatcher can be tested in isolation. End-to-end tests
  in tests/regression_registry/test_2026_05_19_single_dispatcher_routes.py
  drive the full Kobe.route() → dispatcher → handler path with real
  handler functions.
- The route order is asserted by name in test_route_order_specific_before_generic.
  If a refactor reorders the ROUTES list, that test will fire.
"""
from __future__ import annotations

import os
import re

import pytest


# ─── 1. Feature flag ───────────────────────────────────────────────
def test_dispatcher_enabled_by_default(monkeypatch):
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)
    from core import dispatcher
    assert dispatcher.enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off"])
def test_dispatcher_disabled_via_env(monkeypatch, val):
    monkeypatch.setenv("RAHAT_USE_DISPATCHER", val)
    from core import dispatcher
    assert dispatcher.enabled() is False


def test_dispatch_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("RAHAT_USE_DISPATCHER", "0")
    from core import dispatcher
    assert dispatcher.dispatch("/pace") is None
    assert dispatcher.dispatch("what is the WOD for Tuesday") is None


# ─── 2. Defensive shape ────────────────────────────────────────────
def test_dispatch_empty_message_returns_none():
    from core import dispatcher
    assert dispatcher.dispatch("") is None
    assert dispatcher.dispatch(None) is None


def test_match_route_returns_name_only():
    from core import dispatcher
    assert dispatcher.match_route("/pace") == "slash"
    assert dispatcher.match_route(
        "what is the WOD for Tuesday"
    ) == "gym_wod_on_day"
    assert dispatcher.match_route("hello there") is None


def test_list_routes_returns_names_in_order():
    from core import dispatcher
    names = dispatcher.list_routes()
    assert names[0] == "slash", "slash must always be first"
    assert "gym_wod_on_day" in names
    assert len(names) == len(set(names)), "route names must be unique"


# ─── 3. Route pattern matching ─────────────────────────────────────
# For each route, assert it matches its intended phrasings. The
# stub handlers return a recognizable string so we can verify dispatch
# without invoking real Kobe handlers.

@pytest.fixture
def stub_handlers(monkeypatch):
    """Replace every handler with a stub that returns its route name.
    Lets us verify routing without running the real handler stack."""
    from core import dispatcher

    stub_calls: list[str] = []

    def make_stub(route_name: str):
        def _stub(msg, match):
            stub_calls.append(route_name)
            return f"stub:{route_name}"
        return _stub

    new_routes = [
        dispatcher.Route(r.name, r.pattern, make_stub(r.name))
        for r in dispatcher.ROUTES
    ]
    monkeypatch.setattr(dispatcher, "ROUTES", new_routes)
    return stub_calls


@pytest.mark.parametrize("msg,expected_route", [
    # Slash
    ("/pace", "slash"),
    ("/today", "slash"),
    ("/plan next", "slash"),
    ("  /pace", "slash"),  # leading whitespace tolerated

    # Gym WOD on a specific weekday — the 2026-05-18 bug fix.
    ("what is the WOD for Tuesday", "gym_wod_on_day"),
    ("What is the WOD for Saturday", "gym_wod_on_day"),
    ("what's the wod for monday", "gym_wod_on_day"),
    ("gym workout for Friday", "gym_wod_on_day"),
    ("whats at the gym on Wednesday", "gym_wod_on_day"),
    ("show me Thursday's workout", "show_day_workout"),

    # Numeric mutators
    ("weight 198", "weight_log"),
    ("wt: 197.5", "weight_log"),
    ("hrv 42", "hrv_log"),
    ("tier hammer", "tier_set"),
    ("tier red", "tier_set"),

    # Status
    ("pace", "pace"),
    ("on track?", "pace"),
    ("how am I doing", "pace"),

    # Plan
    ("what is the plan for next week", "show_plan_next_week"),
    ("next week's plan", "show_plan_next_week"),
    ("which days am I working out next week", "show_plan_next_week"),
    ("what is the plan", "show_plan_this_week"),
    ("show me my plan", "show_plan_this_week"),
    ("plan", "show_plan_this_week"),
    ("which days am I working out", "show_plan_this_week"),

    # Workout today (cadence)
    ("what is the workout today", "workout_today"),
    ("am I working out today", "workout_today"),

    # Read-only state
    ("what is my weight", "current_weight"),
    ("how much do I weigh", "current_weight"),
    ("what are my dislikes", "list_dislikes"),
    ("show me my blacklist", "list_dislikes"),

    # Protocols
    ("7/15 breathing", "breathing_715"),
    ("box breath", "breathing_box"),
    ("pre-workout fuel", "pre_fuel"),
    ("what should I eat before", "pre_fuel"),
    ("cool-down routine", "post_recovery"),

    # Weekly summary
    ("how much kcal remaining this week", "weekly_remaining"),
    ("last week burn summary", "last_week"),
])
def test_route_matches_intended_phrasings(stub_handlers, msg, expected_route):
    from core import dispatcher
    result = dispatcher.dispatch(msg)
    assert result == f"stub:{expected_route}", (
        f"{msg!r} should match route {expected_route!r} but dispatched to "
        f"{result!r}. Check ROUTES order and pattern."
    )


# ─── 4. Route order — specific patterns beat generic ───────────────
def test_route_order_specific_before_generic():
    """gym_wod_on_day MUST be earlier than show_plan_this_week (both could
    arguably match phrasings with 'Tuesday' / 'plan'). Slash MUST be
    first. This pins the order so a future refactor reordering ROUTES
    fires the test."""
    from core import dispatcher
    names = dispatcher.list_routes()
    assert names.index("slash") == 0
    assert names.index("gym_wod_on_day") < names.index("show_plan_this_week")
    assert names.index("show_plan_next_week") < names.index("show_plan_this_week")
    assert names.index("weight_log") < names.index("pace")  # mutator before status


# ─── 5. Negative space — unmatched messages return None ────────────
@pytest.mark.parametrize("msg", [
    "hello there",
    "tell me about your day",
    "I'm feeling tired",
    "explain Zone-2 training",
    "what is HRV anyway",
])
def test_open_ended_messages_fall_through(stub_handlers, msg):
    """Open-ended conversational messages should NOT match any route.
    These go to the reasoner — the dispatcher returns None."""
    from core import dispatcher
    result = dispatcher.dispatch(msg)
    assert result is None, (
        f"{msg!r} matched a route unexpectedly. The dispatcher should "
        f"only catch FACTUAL queries; open-ended chat falls through to "
        f"the reasoner."
    )


# ─── 6. Handler exception safety ──────────────────────────────────
def test_handler_exception_returns_none_not_crash(monkeypatch):
    """A handler that raises should return None to the caller, not
    propagate. The reasoner takes over as graceful fallback."""
    from core import dispatcher

    def _crashing_handler(msg, match):
        raise RuntimeError("synthetic crash")

    crash_route = dispatcher.Route(
        "crash_test",
        re.compile(r"crashtest"),
        _crashing_handler,
    )
    monkeypatch.setattr(dispatcher, "ROUTES", [crash_route])
    result = dispatcher.dispatch("crashtest please")
    assert result is None, (
        "A handler raising should be swallowed; dispatcher returns None "
        "so the caller can fall through to the reasoner."
    )
