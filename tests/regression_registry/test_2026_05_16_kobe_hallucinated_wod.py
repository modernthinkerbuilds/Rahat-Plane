"""Pin: 2026-05-16 — Kobe hallucinated WODs when Fraser should have answered.

SYMPTOM (production):
    User asked "what is the WOD" / "give me today's workout" via Telegram.
    Fraser was registered in the mesh but Miya's classifier picked Kobe.
    Kobe synthesized a workout from its priors instead of routing to
    Fraser. Bot voice sounded "right" but the prescription bypassed
    Fraser's real source-workout pipeline.

ROOT CAUSE:
    Fraser's `description` field was too generic ("CrossFit programming")
    while Kobe's description covered "weekly plan, schedule" — which the
    LLM classifier interpreted as covering "WOD" too. No trigger regex
    forced WOD-shaped queries to Fraser.

FIX:
    Fraser's description now explicitly enumerates WOD-shaped intents
    (workout, WOD, today's session, programming, scaling). The
    description-contract test (tests/test_fraser_description_contract.py)
    locks the keyword list.

THIS PIN ASSERTS:
    For each historical phrasing in the bug report, Fraser's description
    contains the routing-relevant keyword the classifier needs to see.
    Independent of the LLM (which we don't run in tests), the keyword
    presence is the structural fix. If a future refactor strips those
    keywords, this test goes red and the merge gate blocks.

The complementary "classifier actually picks Fraser given these
descriptions" assertion lives in tests/adversarial/phrasings.py — that's
a behavior test against the live classifier. This file pins the
*configuration* — the necessary condition for the behavior.
"""
from __future__ import annotations

import importlib
import sys

import pytest


WOD_PHRASINGS = [
    "what is the WOD",
    "what's the WOD today",
    "give me today's workout",
    "today's session",
    "todays wod",
    "what's my workout today",
    "design my workout",
]


def _fraser_description() -> str:
    """Pull the live FraserAgent description used by Miya's classifier."""
    try:
        from agents.fraser.agent import FraserAgent
    except ImportError:
        pytest.skip("FraserAgent not importable in this branch")
    return (FraserAgent().description or "").lower()


def _kobe_description() -> str:
    try:
        from agents.kobe.agent import KobeAgent  # alias
    except ImportError:
        try:
            from agents.the_scientist.agent import KobeAgent
        except ImportError:
            try:
                from agents.the_scientist.agent import ScientistAgent as KobeAgent
            except ImportError:
                pytest.skip("KobeAgent/ScientistAgent not importable")
    return (KobeAgent().description or "").lower()


def test_fraser_description_mentions_wod():
    """Fraser must surface 'WOD' (or 'workout of the day') in its
    description so the classifier prefers it for WOD-shaped queries."""
    desc = _fraser_description()
    assert ("wod" in desc or "workout of the day" in desc), (
        f"Fraser description missing WOD keyword. "
        f"Without it, the classifier will pick Kobe for 'what is the WOD'. "
        f"Got description: {desc[:200]!r}")


def test_fraser_description_mentions_workout_design():
    """Fraser owns the workout-design intent. Description must say so."""
    desc = _fraser_description()
    has_workout = any(kw in desc for kw in
                      ["workout", "programming", "design", "wod"])
    assert has_workout, (
        f"Fraser description missing workout/programming keyword: {desc[:200]!r}")


def test_kobe_description_does_not_claim_workout_design():
    """Kobe is vitality/scheduling — must NOT claim to design workouts.
    If it does, the LLM classifier will tie-break toward Kobe."""
    desc = _kobe_description()
    # 'workout' the word can appear (it's in 'workout log') but the
    # phrase 'design a workout' or 'workout of the day' or 'WOD' must NOT.
    forbidden = ["design a workout", "workout of the day",
                 "design workouts", "wod design"]
    for phrase in forbidden:
        assert phrase not in desc, (
            f"Kobe description contains '{phrase}' — will cause classifier "
            f"to pick Kobe for Fraser-shaped queries. desc={desc[:200]!r}")


def test_fraser_description_distinct_from_kobe():
    """The two descriptions must have non-trivial diff — otherwise the
    classifier has no signal to pick between them."""
    fd, kd = _fraser_description(), _kobe_description()
    assert fd and kd, "both descriptions must be non-empty"
    assert fd != kd, "Fraser and Kobe descriptions are identical — classifier blind"


@pytest.mark.parametrize("phrasing", WOD_PHRASINGS)
def test_wod_phrasing_keywords_align_with_fraser(phrasing: str):
    """For each historical WOD-shaped phrasing, the Fraser description
    must share at least one strong keyword (workout, wod, design,
    programming, scaling, today's). This is the structural condition
    for the classifier to prefer Fraser."""
    desc = _fraser_description()
    msg = phrasing.lower()
    # Keywords that should appear in both the query and Fraser's desc.
    keywords = ["workout", "wod", "design", "programming", "scaling",
                "session", "today"]
    msg_kws = {k for k in keywords if k in msg}
    desc_kws = {k for k in keywords if k in desc}
    overlap = msg_kws & desc_kws
    assert overlap, (
        f"No keyword overlap between phrasing {phrasing!r} (kws={msg_kws}) "
        f"and Fraser description (kws={desc_kws}). Classifier will not "
        f"prefer Fraser. Fix: add the missing keyword to Fraser.description.")
