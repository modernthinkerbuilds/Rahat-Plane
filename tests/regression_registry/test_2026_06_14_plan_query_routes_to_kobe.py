"""Regression: 'plan for next week' was paraphrased by the synth.

Bug (2026-06-14, live transcript)
---------------------------------
Asking "What is the plan for next week" repeatedly gave DIFFERENT generic
schedules and dropped the kcal target / blacklist awareness. One ask would
show the real "6,000 kcal, behind pace" plan; the next would invent a bare
"CrossFit Tue/Wed/Thu" list.

Root cause: core.dispatcher already routes these to show_plan_this_week /
show_plan_next_week (handle_show_plan — which IS blacklist- and
kcal-aware). But new_plane.miya_runner.delegate_classifier sent the phrasings
to "orchestrate" first, so they reached the paraphrasing synth instead of the
deterministic handler. Non-deterministic synth output = inconsistent answers.

Fix: classifier routes plan-view queries to kobe_route, so the dispatcher +
handle_show_plan run deterministically.
"""
from __future__ import annotations

import pytest

from core import dispatcher
from new_plane.miya_runner.delegate_classifier import classify_delegation


@pytest.mark.parametrize("msg,expected_route", [
    ("What is the plan for next week", "show_plan_next_week"),
    ("What is my plan for next week", "show_plan_next_week"),
    ("Which days am I working out next week ?", "show_plan_next_week"),
    ("What is the plan for the week", "show_plan_this_week"),
    ("What is the plan for this week", "show_plan_this_week"),
    ("show me my plan", "show_plan_this_week"),
])
def test_plan_queries_route_to_kobe_and_dispatch_deterministically(msg, expected_route):
    # 1. New-plane classifier must hand it to Kobe (not the synth).
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", (
        f"{msg!r} routed to {path!r}; expected kobe_route so the deterministic "
        f"plan handler runs instead of the paraphrasing synth."
    )
    # 2. Kobe's dispatcher then matches the right deterministic plan route.
    assert dispatcher.match_route(msg) == expected_route


def test_design_intent_still_orchestrates():
    # Guard: a workout-DESIGN request must NOT be captured as a plan view.
    path, _ = classify_delegation("design me a workout for next week")
    assert path in ("orchestrate", "fraser_route"), (
        f"design intent wrongly routed to {path!r}"
    )
