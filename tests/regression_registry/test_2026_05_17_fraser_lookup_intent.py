"""Pin: 2026-05-17 — classifier confused lookup vs design intents.

SYMPTOM (production):
    User typed "what is my workout for Tuesday" expecting Kobe to
    look up the existing weekly plan. Instead, Miya's classifier
    routed to Fraser, which tried to *design* a workout. Fraser
    then either stubbed out or hallucinated one.

ROOT CAUSE (per Day 9 Bug 3 spec):
    Fraser's description said "owns workout design AND scheduling
    lookup" — too broad. Either keyword in the user's query made
    Fraser look like the right pick. The classifier had no way to
    distinguish "what IS my workout for Tuesday" (lookup) from
    "design my Tuesday workout" (design).

FIX (in flight — Day 9 Bug 3):
    Tighten Fraser's description to claim ONLY workout *design* and
    *scaling*. Kobe owns lookup/scheduling. The classifier now picks
    Kobe for "what is" / "when is" / "which days" / "show me"
    phrasings even when 'workout' appears.

THIS PIN ASSERTS:
    For lookup-shaped queries ("what is", "when is", "show me",
    "which day"), Fraser's description has structural keywords that
    are FEWER and LESS lookup-shaped than Kobe's. The classifier
    decision is downstream of description; pin the structural
    condition here.
"""
from __future__ import annotations

import importlib
import sys

import pytest


# Lookup-shaped queries that should land at Kobe.
LOOKUP_PHRASINGS = [
    "what is my workout for Tuesday",
    "what is the plan for tomorrow",
    "when is my next run",
    "show me Friday's session",
    "which day am I doing CrossFit",
]

# Design-shaped queries that should land at Fraser.
DESIGN_PHRASINGS = [
    "design me a 60-min WOD",
    "give me a workout with no running",
    "build a session for 800 calories",
    "scale today's WOD for my ankle",
]


def _fraser_desc():
    try:
        from agents.fraser.agent import FraserAgent
    except ImportError:
        pytest.skip("FraserAgent not importable")
    return (FraserAgent().description or "").lower()


def _kobe_desc():
    try:
        from agents.kobe.agent import KobeAgent
    except ImportError:
        try:
            from agents.the_scientist.agent import KobeAgent
        except ImportError:
            try:
                from agents.the_scientist.agent import ScientistAgent as KobeAgent
            except ImportError:
                pytest.skip("KobeAgent/ScientistAgent not importable")
    return (KobeAgent().description or "").lower()


LOOKUP_KEYWORDS = {"lookup", "schedule", "plan", "weekly", "show",
                   "what is", "when is", "which", "burn so far",
                   "weight", "hrv", "calendar"}

DESIGN_KEYWORDS = {"design", "scale", "scaling", "programming",
                   "compose", "build", "adapt"}


@pytest.mark.xfail(strict=False, reason="Day 9 Bug 3 fix may not have landed")
def test_kobe_description_owns_lookup():
    """Kobe's description must explicitly claim lookup/scheduling
    intents. Without it, the classifier has nothing to pick on for
    'what is my workout for Tuesday'."""
    desc = _kobe_desc()
    overlap = {k for k in LOOKUP_KEYWORDS if k in desc}
    assert len(overlap) >= 2, (
        f"Kobe description doesn't claim ≥2 lookup keywords. "
        f"matches={overlap}. desc={desc[:200]!r}. "
        f"Fix: ensure Kobe says it owns 'weekly plan', 'schedule', "
        f"'lookup', 'show me' intents.")


@pytest.mark.xfail(strict=False, reason="Day 9 Bug 3 fix may not have landed")
def test_fraser_description_does_not_claim_lookup():
    """Fraser must NOT claim lookup/scheduling — it's the design agent.
    If Fraser claims both, the classifier has no signal to differentiate."""
    desc = _fraser_desc()
    lookup_overlap = {k for k in LOOKUP_KEYWORDS if k in desc}
    # Allow at most 1 lookup keyword — Fraser may incidentally use
    # 'plan' or 'schedule', but should NOT have 3+ of them.
    assert len(lookup_overlap) <= 1, (
        f"Fraser description claims ≥2 lookup keywords: {lookup_overlap}. "
        f"This is the exact phrasing-confusion that caused the 2026-05-17 "
        f"lookup-vs-design regression. Move lookup language to Kobe. "
        f"desc={desc[:200]!r}")


@pytest.mark.xfail(strict=False, reason="Day 9 Bug 3 fix may not have landed")
def test_fraser_description_claims_design_explicitly():
    """Fraser must explicitly claim design/scale/scaling."""
    desc = _fraser_desc()
    design_overlap = {k for k in DESIGN_KEYWORDS if k in desc}
    assert design_overlap, (
        f"Fraser description doesn't mention any design keyword "
        f"({DESIGN_KEYWORDS}). The classifier has nothing to pick on for "
        f"'design me a workout'. desc={desc[:200]!r}")


@pytest.mark.parametrize("phrasing", LOOKUP_PHRASINGS)
@pytest.mark.xfail(strict=False, reason="Day 9 Bug 3 — pin lookup → Kobe")
def test_lookup_phrasing_keyword_overlap_with_kobe(phrasing: str):
    """For each lookup phrasing, Kobe's description must share more
    keywords with it than Fraser's does. Structural condition for
    the classifier to prefer Kobe."""
    fraser_desc = _fraser_desc()
    kobe_desc = _kobe_desc()
    msg = phrasing.lower()

    # Score: count of distinct lookup keywords present in BOTH msg
    # and the description.
    msg_kws = {k for k in LOOKUP_KEYWORDS if k in msg}
    fraser_score = len(msg_kws & {k for k in LOOKUP_KEYWORDS if k in fraser_desc})
    kobe_score = len(msg_kws & {k for k in LOOKUP_KEYWORDS if k in kobe_desc})

    assert kobe_score > fraser_score, (
        f"Phrasing {phrasing!r}: Kobe lookup-score ({kobe_score}) must "
        f"exceed Fraser lookup-score ({fraser_score}). msg_kws={msg_kws}. "
        f"Otherwise the classifier picks Fraser and the lookup intent "
        f"silently fails.")
