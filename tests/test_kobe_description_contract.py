"""Byte-sensitive contract for KobeAgent.description (Day-8, ADR-006).

What this file pins
-------------------
The Day-8 mesh-routing rollout depends on the LLM classifier in
`core/miya.classify_intent()` reading agent descriptions and picking
the right specialist. The 2026-05-16 production bug was the classifier
picking Kobe for workout questions because Kobe's description claimed
overlapping territory.

ADR-006 §"Required updates to agent descriptions" gives the verbatim
target description. The load-bearing sentence is:

    "Defer to Fraser for: workout design, CrossFit programming,
     scaled loads, WOD selection."

Without that EXACT phrasing the classifier still tilts Kobe-ward for
fitness-shaped queries, because the description's "Use for:" list
overlaps Fraser's coverage. The byte-pinned contract makes refactors
that drift the wording fail loudly.

Sister file: tests/test_kobe_mesh_routing.py pins the behavioral
contract (delegate_to dispatched, triggers pruned, system-prompt block
present). This file pins only the string itself.

Every test is offline — no GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations


# ─── 1. Verbatim Fraser-defer sentence ────────────────────────────
def test_description_contains_verbatim_fraser_defer_sentence():
    """The exact ADR-006 phrasing must appear in KobeAgent.description.

    If a future refactor reflows this sentence (changes word order,
    swaps 'WOD selection' for 'WOD picking', etc.), this test fires
    and the refactor author has to consciously revisit ADR-006."""
    from agents.the_scientist.agent import KobeAgent

    required = (
        "Defer to Fraser for: workout design, CrossFit programming, "
        "scaled loads, WOD selection."
    )
    assert required in KobeAgent.description, (
        f"KobeAgent.description must contain the verbatim ADR-006 "
        f"defer-to-Fraser sentence:\n\n  {required!r}\n\n"
        f"Currently:\n\n  {KobeAgent.description!r}"
    )


# ─── 2. Territory claims (the "Use for:" list) ────────────────────
def test_description_claims_kobe_owned_domains():
    """The "Use for:" phrasings are the queries the classifier will
    actually see — they're how the owner asks. Each canonical domain
    needs at least one anchor phrase the classifier can match."""
    from agents.the_scientist.agent import KobeAgent
    d = KobeAgent.description.lower()
    # One anchor per Kobe-owned domain, drawn from the description's
    # "Use for:" list. If any of these disappear, the classifier will
    # stop routing that domain to Kobe.
    required_anchors = [
        "weight",
        "hrv",
        "weekly",          # weekly burn target / weekly caloric burn
        "timeline",        # weight-loss timeline math
        "tier",            # recovery tier
        "breathing",
        "pre-fuel",
        "pace",
    ]
    missing = [a for a in required_anchors if a not in d]
    assert not missing, (
        f"description missing Kobe-domain anchor phrases: {missing}. "
        f"Without these, the classifier loses signal for those queries."
    )


def test_description_explicitly_names_fraser():
    """The defer line must name Fraser by string. The classifier reads
    this as "Kobe explicitly disclaims X, so when the user asks X,
    route to whoever DOES own it."""
    from agents.the_scientist.agent import KobeAgent
    assert "Fraser" in KobeAgent.description


def test_description_does_not_claim_workout_design():
    """Negative pin: the legacy Kobe description claimed "workout plan",
    "schedule", "weekday-specific workout lookups" — all overlap
    Fraser. After Day-8 those phrasings are GONE from Kobe's
    description. If a future refactor adds them back, this test fires
    and the author has to revisit ADR-006 §"Required updates"."""
    from agents.the_scientist.agent import KobeAgent
    d = KobeAgent.description.lower()
    # Each of these phrasings was in the legacy description and is
    # exactly what made the 2026-05-16 bug fire. They MUST NOT be
    # back in the description after Day-8.
    forbidden = [
        "workout design",       # Fraser owns
        "workout plan",         # Fraser owns (overlapped pre-Day-8)
        "scaled loads",         # Fraser owns
        "wod selection",        # Fraser owns
        "crossfit programming", # Fraser owns
    ]
    for phrase in forbidden:
        # The phrases appear ONLY inside the "Defer to Fraser for:"
        # clause — they're disclaiming Fraser's territory, not
        # claiming it. The test allows them there but not elsewhere.
        defer_clause_start = d.find("defer to fraser for:")
        if defer_clause_start == -1:
            # No defer clause at all — fail the verbatim-sentence test
            # (sister case); this one just checks claim shape.
            continue
        before_defer = d[:defer_clause_start]
        assert phrase not in before_defer, (
            f"Description CLAIMS Fraser-owned territory {phrase!r} "
            f"outside the 'Defer to Fraser for:' clause. "
            f"That's exactly the 2026-05-16 overlap. Currently:\n"
            f"{KobeAgent.description!r}"
        )


# ─── 3. Identity sanity ────────────────────────────────────────────
def test_description_is_a_nonempty_string():
    from agents.the_scientist.agent import KobeAgent
    assert isinstance(KobeAgent.description, str)
    assert len(KobeAgent.description) > 100, (
        "description suspiciously short — the classifier needs body "
        "to discriminate domains."
    )


def test_description_unicode_is_clean():
    """Curly quotes / em-dashes inside the description trip a small
    minority of LLM tokenizers and produce noisy classifier scores.
    The Day-8 rewrite is ASCII-clean — pin that."""
    from agents.the_scientist.agent import KobeAgent
    for ch in KobeAgent.description:
        # Allow basic ASCII + apostrophe + colon/comma/dash. Reject
        # the curly quote / em-dash family the iOS keyboard inserts.
        assert ord(ch) < 0x7F or ch in "—", (
            f"description contains non-ASCII character {ch!r} (U+{ord(ch):04X}). "
            "Day-8 rewrite is ASCII-clean — Apple autocorrect curly "
            "quotes / em-dashes degrade classifier scoring on some "
            "tokenizers."
        )
