"""Byte-sensitive contract for FraserAgent.description (Day-9, Bug 3).

What this file pins
-------------------
The 2026-05-16 mesh-routing rollout (ADR-006) fixed Kobe→Fraser
routing for design questions ("what's my WOD"). The 2026-05-19
production bug surfaced the next boundary: Fraser ALSO winning
lookup-shaped questions ("what is my workout for Tuesday?"), because
the original Day-8 description claimed workout territory broadly
without disambiguating design-vs-lookup. The fix is description
tightening, NOT another regex in `_should_delegate` — the classifier
must read Fraser's description and route lookup queries to Kobe
(which now has `get_workout_on()` per Bug-2 wiring).

The verbatim clause from the Bug-3 brief is byte-pinned here so a
future refactor that reflows the wording surfaces in test diff.
Sister file: `tests/test_fraser_mesh_routing.py` / `test_fraser_
delegation.py` pin behavioral contracts; this file pins the string
itself.

Every test is offline — no GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations


# ─── 1. Verbatim lookup-disclaim clause (Day-9 Bug 3) ──────────────
def test_description_contains_verbatim_lookup_disclaim_clause():
    """The EXACT Bug-3 brief phrasing must appear in
    FraserAgent.description. If a future refactor reflows this
    clause (changes word order, swaps 'lookup of scheduled workouts'
    for a synonym, drops one of the day-name paraphrases), this test
    fires and the refactor author has to consciously revisit the
    Bug-3 brief.

    Load-bearing because the classifier prompt feeds the agent
    descriptions verbatim. Drift here weakens routing toward Kobe
    for lookup queries — exactly the regression Bug 3 is preventing.
    """
    from agents.fraser.agent import FraserAgent

    required = (
        "DOES NOT own: lookup of scheduled workouts, "
        "'what is my workout on [day]', "
        "'what is my workout for [day]', 'what's planned', "
        "weekly plan view, which days am I working out. "
        "For all lookup questions about the user's synced plan, "
        "defer to kobe."
    )
    assert required in FraserAgent.description, (
        f"FraserAgent.description must contain the verbatim "
        f"Bug-3 lookup-disclaim clause:\n\n  {required!r}\n\n"
        f"Currently:\n\n  {FraserAgent.description!r}"
    )


# ─── 2. Existing Day-8 disclaim clause still present ────────────────
def test_description_keeps_day8_kobe_huberman_disclaim():
    """Day-9 Bug 3 ADDS a new lookup-disclaim clause; it must NOT
    accidentally drop the Day-8 weight/HRV/tier disclaim. Both clauses
    coexist — they target different routing failure modes."""
    from agents.fraser.agent import FraserAgent

    required = (
        "DOES NOT own: weight tracking, weekly burn targets, HRV "
        "interpretation, weight-loss timeline math, recovery tier "
        "selection. For those, delegate to kobe or huberman."
    )
    assert required in FraserAgent.description, (
        f"FraserAgent.description must retain the Day-8 "
        f"weight/HRV/tier disclaim clause. Removing this would "
        f"re-introduce the 2026-05-16 hallucination bug.")


# ─── 3. Use-for list no longer contains lookup phrasings ────────────
def test_use_for_does_not_include_lookup_phrasings():
    """A lookup phrasing in the 'Use for:' list contradicts the
    lookup-disclaim. 'show me Friday's workout' was the offender —
    dropped in the Bug-3 commit. If a future edit re-adds it (or
    any equivalent lookup phrasing), the description self-conflicts
    and the classifier sees mixed signals."""
    from agents.fraser.agent import FraserAgent
    d = FraserAgent.description

    # The known offenders.
    forbidden_phrases = [
        "show me Friday's workout",
        "show me tomorrow's workout",
        "what is my workout on",
        "what is my workout for",
        "what's planned",
    ]
    # These phrases CAN appear inside the lookup-DISCLAIM clause
    # (that's where we're explicitly disclaiming them). The check
    # is: each forbidden phrase appears at most once — in the
    # disclaim. If it appears twice, one is in Use-for (the bug).
    for phrase in forbidden_phrases:
        count = d.count(phrase)
        assert count <= 1, (
            f"phrase {phrase!r} appears {count}× in description — "
            f"likely both in 'Use for:' AND the lookup-disclaim. "
            f"Remove from 'Use for:' so the description is "
            f"internally consistent.")


# ─── 4. The design-territory claim is unchanged ─────────────────────
def test_description_still_claims_workout_design_territory():
    """Bug 3 narrows Fraser's territory at the lookup boundary; it
    must NOT narrow the design territory. The lead sentence + the
    canonical design phrasings stay."""
    from agents.fraser.agent import FraserAgent
    d = FraserAgent.description
    # Lead sentence.
    assert "CrossFit + Zone-2 workout designer" in d
    # Design phrasings the classifier needs.
    for phrase in ("what's my WOD", "give me today's workout",
                   "scale this WOD", "substitute"):
        assert phrase in d, (
            f"design phrase {phrase!r} must stay in 'Use for:' so "
            f"the classifier routes design questions to Fraser")


# ─── 4b. Compact Kobe-style defer sentence (Day-9 Bug-3 update) ─────
def test_description_contains_compact_defer_to_kobe_sentence():
    """The mid-session Bug-3 amendment (2026-05-17) added a compact
    'Defer to Kobe for: ...' sentence mirroring Kobe's
    'Defer to Fraser for: ...' pattern. The classifier reads compact
    "Defer to X for: …" sentences more reliably than prose
    disclaimers; pinning the exact wording prevents drift.

    Coexists with the two longer 'DOES NOT own:' clauses (pinned
    elsewhere in this file) — both signals together are the
    belt-and-suspenders defense for the production-bug class."""
    from agents.fraser.agent import FraserAgent

    required = (
        "Defer to Kobe for: weekly plan lookups, weekday-specific "
        "workout lookups, weight tracking, HRV interpretation, "
        "recovery tier."
    )
    assert required in FraserAgent.description, (
        f"FraserAgent.description must contain the compact "
        f"Day-9 Bug-3 defer sentence mirroring Kobe's pattern:\n\n"
        f"  {required!r}\n\n"
        f"Currently:\n\n  {FraserAgent.description!r}"
    )


# ─── 5. Description still names the delegation targets ──────────────
def test_description_names_kobe_as_lookup_target():
    """The lookup-disclaim explicitly directs to Kobe. The classifier
    reads the recipient name to know where to route."""
    from agents.fraser.agent import FraserAgent
    d = FraserAgent.description
    # "defer to kobe" appears in the lookup clause; "delegate to
    # kobe or huberman" appears in the Day-8 weight clause. Both
    # name kobe as a routing target.
    assert "defer to kobe" in d, (
        "lookup-disclaim must explicitly name 'defer to kobe' so "
        "the classifier has a target")
    assert "delegate to kobe or huberman" in d


# ─── 6. Classifier-side smoke test — lookup query routes to Kobe ────
class TestClassifierRoutesLookupToKobe:
    """End-to-end proof against a mocked classifier: given Fraser's
    Day-9 description and the production-bug query 'what is my
    workout for Tuesday', the classifier (mocked here to behave
    like a 0-shot reader) should score Kobe higher than Fraser."""

    def _setup(self, monkeypatch, classifier_response):
        import json as _json
        from core import miya, io as cio
        from agents.fraser.agent import FraserAgent
        from agents.the_scientist.agent import KobeAgent
        miya.register(KobeAgent())
        miya.register(FraserAgent())
        monkeypatch.setattr(
            cio, "llm_generate",
            lambda prompt, *, model=None: _json.dumps(classifier_response))
        return miya

    def test_lookup_query_routes_to_kobe(self, monkeypatch):
        """Production-bug repro: 'what is my workout for Tuesday'
        should NOT win Fraser when the classifier reads Fraser's
        Day-9 lookup-disclaim. We mock the classifier to behave as
        a well-instructed 0-shot reader — score Kobe higher because
        the lookup-disclaim explicitly defers."""
        miya = self._setup(monkeypatch,
                           {"kobe": 0.85, "fraser": 0.10})
        scores = miya.classify_intent(
            "what is my workout for Tuesday?")
        assert scores.get("kobe", 0) > scores.get("fraser", 0), (
            f"Kobe must outscore Fraser for lookup queries; "
            f"got {scores}")
        reply = miya.route("what is my workout for Tuesday?")
        assert reply is not None
        assert "[Fraser]" not in reply.text, (
            f"Fraser MUST NOT answer lookup queries; got "
            f"reply.text={reply.text[:80]!r}")

    def test_design_query_still_routes_to_fraser(self, monkeypatch):
        """Symmetry: Bug-3 narrows the lookup boundary; design
        questions still belong to Fraser."""
        miya = self._setup(monkeypatch,
                           {"fraser": 0.85, "kobe": 0.15})
        scores = miya.classify_intent(
            "give me today's workout, 60 minutes")
        assert scores.get("fraser", 0) > scores.get("kobe", 0)
        reply = miya.route("give me today's workout, 60 minutes")
        assert reply is not None
        assert "[Fraser]" in reply.text or "fraser" in reply.text.lower()
