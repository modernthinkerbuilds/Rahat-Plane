"""Bug pin (2026-08-20, 12:44 AM, live) — design intent preempts every
deterministic Kobe read.

THE INCIDENT. The owner sent a rich authoring request:

    "Ok, I have a catch in my right hip after deadlifts last week, but
    I already did strict press for strength this week. Can you give me
    a bench press strength , a good WOD and accessories work for arms
    to burn 800 calories in 75 min"

— and Bade Miya answered, twice, with: "Last week (Aug 10–Aug 16):
6,775 kcal — 113% of 6,000 kcal target." The phrase 'deadlifts last
week' matched _STATUS_QUERY_RE ('last week') at classifier step 5,
and the design guard — which exists precisely so authoring asks reach
Fraser — was only consulted at steps 8/8a, after the status/pain reads
it needed to protect against. The same message also matched
_PAIN_PROFILE_RE ('catch in my right hip'): TWO deterministic reads
queued up ahead of the guard.

THE PIN. Design verb + workout noun → "orchestrate", checked BEFORE
status/plan-view/pain/recovery/lookup reads — and deliberately AFTER
plan mutations and state logs, which keep their claims.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


LIVE_MESSAGE = ("Ok, I have a catch in my right hip after deadlifts last "
                "week,  but I already did strict press for strength this "
                "week. Can you give me a bench press strength , a good WOD "
                "and accessories work for arms to burn 800 calories in "
                "75 min")


# ─────────────── the incident can never recur ───────────────
def test_live_message_orchestrates_instead_of_kcal_lookup():
    path, _ = classify_delegation(LIVE_MESSAGE)
    assert path == "orchestrate", (
        f"the 12:44 AM message routed to {path!r} — the last-week kcal "
        f"hijack is back")


@pytest.mark.parametrize("msg", [
    "design me a workout around my sore hip",
    "give me a WOD for tomorrow morning",
    "build me a 60 min conditioning session, low impact",
    "can you create an upper body strength day, I ran yesterday",
    "need a metcon that avoids overhead work",
])
def test_design_asks_orchestrate_even_with_read_bait(msg):
    assert classify_delegation(msg)[0] == "orchestrate"


# ─────────────── Kobe keeps every legitimate claim ───────────────
@pytest.mark.parametrize("msg,route", [
    # Status reads with NO design intent stay deterministic.
    ("how did last week go", "kobe_route"),
    ("week so far", "kobe_route"),
    ("how many more calories do I need today", "kobe_route"),
    # Pain WITHOUT authoring stays Kobe's pain/profile path.
    ("my hip hurts", "kobe_route"),
    # WOD lookup (no design verb) stays Kobe.
    ("what is tomorrow's WOD", "kobe_route"),
    # Plan mutations keep their claim even with guard verbs + nouns —
    # the design preempt sits AFTER step 3 by design.
    ("swap Monday with Tuesday", "kobe_route"),
    # State logs are untouched.
    ("154.5", "kobe_route"),
    ("HRV 38", "kobe_route"),
])
def test_deterministic_kobe_claims_survive(msg, route):
    assert classify_delegation(msg)[0] == route


def test_guard_verbs_without_a_workout_noun_do_not_divert():
    """'give me a day-by-day burn breakdown' has a guard VERB but no
    workout noun — it is a status ask and must stay Kobe's."""
    assert classify_delegation(
        "give me a day-by-day burn breakdown")[0] == "kobe_route"


def test_genie_household_asks_are_not_stolen():
    """'give me a family-friendly weekend' carries a guard verb and no
    workout noun — Genie keeps it (the 8a-genie contract)."""
    assert classify_delegation(
        "give me a family-friendly weekend plan")[0] == "genie_route"
