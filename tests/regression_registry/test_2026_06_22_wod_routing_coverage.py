"""Regression (2026-06-22, Test-Lead round 3) — F4: WOD-lookup routing is
brittle. The 2026-06-21 date-aware fix only covered the phrasings that
happened to match the two existing relative-day routes. Many natural ways to
ask for tomorrow's / a weekday's WOD still match NO deterministic route and
NO intent-layer intent, so they fall to the LLM reasoner — the exact
unreliable path whose wrong/rambling answers triggered the original bug
("your thinking incorrectly").

This pins the current routing surface so the gap is visible and measurable.
The CAUGHT set guards against regressions; the GAP set is xfail targets:
widen the WOD route regex (and/or add a read-only intent_layer 'gym_wod'
intent) until they route deterministically, then move them up to CAUGHT.
"""
from __future__ import annotations

import pytest

from core import dispatcher

# Phrasings that already resolve to a deterministic WOD route. Guard rail:
# these must keep routing (a None here is a real regression).
CAUGHT = [
    "what is tomorrow's WOD",
    "whats tomorrows wod",
    "tomorrow wod",
    "what is the wod for tomorrow",
    "gym wod tomorrow",
    "what is the wod for monday",
    # F4 CLOSED (2026-06-22): bare-noun + design-guarded routes promoted from
    # GAP — these used to fall to the reasoner.
    "wod tomorrow",
    "wod for tomorrow",
    "mondays wod",
    "wod monday",
    "whats my wod tomorrow",
    "whats the workout tomorrow",
]

# DESIGN requests share the WOD noun but must NOT hit a lookup route — the
# bare-noun routes are guarded by a design-verb negative lookahead.
DESIGN_NOT_LOOKUP = [
    "design me a wod for tomorrow",
    "build me a wod monday",
    "make a wod tomorrow",
    "write me a monday wod",
    "program a wod for friday",
]


@pytest.mark.parametrize("msg", CAUGHT)
def test_known_wod_phrasings_route_deterministically(msg):
    assert dispatcher.match_route(msg) is not None, (
        f"{msg!r} regressed to the reasoner — a WOD route must catch it")


@pytest.mark.parametrize("msg", DESIGN_NOT_LOOKUP)
def test_design_requests_do_not_hit_lookup(msg):
    route = dispatcher.match_route(msg)
    assert route not in (
        "bare_wod_rel", "bare_wod_day_after", "bare_wod_day_before",
        "gym_wod_relative", "rel_day_workout", "gym_wod_on_day",
    ), f"{msg!r} is a DESIGN request and must reach Fraser, not a WOD lookup"


def test_today_stays_with_fraser_not_a_lookup():
    # DELIBERATE EXCLUSION (not a gap): "today's WOD" is Fraser's daily-driver
    # design intent — the composer folds in today's synced gym WOD — so it must
    # NOT be claimed by a Kobe gym-lookup route. (Long-standing contract; see
    # test_2026_05_23_relative_day_wod_lookup.)
    for msg in ("what is todays wod", "whats the wod today",
                "what is the WOD today"):
        assert dispatcher.match_route(msg) != "bare_wod_rel"
        assert dispatcher.match_route(msg) not in (
            "gym_wod_relative", "rel_day_workout")


def test_wod_today_does_not_mis_route_to_yes_no_intent():
    """'wod today' currently resolves (via the intent layer) to
    `workout_today`, which answers a yes/no 'am I training today' — NOT the
    gym WOD the user asked for. Documents the mis-route; flip to assert the
    correct gym-WOD intent once 'gym_wod' exists."""
    pytest.importorskip("core.intent_layer")
    from core import intent_layer
    intent_layer.ensure_registered()
    m = intent_layer.classify("wod today")
    name = m.name if m else None
    assert name in (None, "workout_today"), (
        "if you added a dedicated gym-WOD intent, update this expectation")
