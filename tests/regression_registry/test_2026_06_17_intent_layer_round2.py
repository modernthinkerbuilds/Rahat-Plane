"""§1b (round-2) — ADR-017 NL intent layer is LIVE in prod (RAHAT_INTENT_LAYER=1
in .env). The OFF-baseline guarantees are necessary but NOT sufficient; this
file tests the ON path as production behavior.

Verified this pass (RAHAT_TEST_MODE=1, flag ON):
  * READ-ONLY (P0, load-bearing safety): 0/8 mutation paraphrases claimed —
    structurally safe (only zero-arg READ handlers are registerable;
    register(read_only=False) raises). PINNED as a property.
  * ABSTAIN GENERALIZATION GAP (finding): the ADR's "0 mis-routes" was the
    tuning corpus. On a FRESH edge corpus, keyword-only inputs misroute:
    "when should I weigh in" → current_weight, "I did crossfit today" →
    workout_today. Not a safety hole (read-only) but a correctness/UX
    regression. PF-2026-06-17-002.
  * SEAM CONTRACT: ADR-017 leaves classify() as the interface a future LLM
    backend plugs into. Pin guarantees 1-4 so the backend can't silently
    violate read-only or stop abstaining.
"""
from __future__ import annotations

import pytest

from core import intent_layer as il


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Opt this file into the ON path (the autouse OFF baseline is the
    deterministic default; RAHAT_TEST_KEEP_INTENT_LAYER opts in)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    monkeypatch.setenv("RAHAT_TEST_KEEP_INTENT_LAYER", "1")
    il._REGISTERED = False
    il._clear_registry()
    il.ensure_registered()
    yield
    il._REGISTERED = False
    il._clear_registry()


# ── READ-ONLY (P0) ────────────────────────────────────────────────────
# Open-ended mutation INTENTS with no read interpretation. (Dispatcher-owned
# exact log forms like "weight 198" / "hrv 42" are caught by dispatch() BEFORE
# the layer per guarantee 1, and would map to a READ here anyway — so they are
# not the read-only safety case. The safety case is: a fuzzy mutation REQUEST
# must never be claimed.)
_MUTATION_PARAPHRASES = [
    "bump my deadlift to 160", "log my weight 165", "log 165 today",
    "skip Friday", "move Wed to Fri", "I hit a PR today",
    "set my back squat to 120", "tolerate partner",
    "can you bump my deadlift to 160", "change my squat max to 110",
]


@pytest.mark.parametrize("msg", _MUTATION_PARAPHRASES)
def test_mutation_paraphrase_is_never_claimed_by_the_layer(msg):
    """P0 read-only: a fuzzy match must NEVER reach a state mutation. Every
    mutation paraphrase abstains (falls to the exact dispatcher route or the
    charter-gated reasoner). One leak here is a 2026-05-08-class P0."""
    m = il.classify(msg)
    assert m is None, (
        f"READ-ONLY VIOLATION: intent layer claimed mutation paraphrase "
        f"{msg!r} as intent {m.name if m else None!r}"
    )


def test_register_rejects_a_non_read_only_intent():
    """The structural guarantee: a write intent cannot be registered."""
    with pytest.raises(ValueError):
        il.register("evil_write", lambda: "x", keywords=("log",),
                    exemplars=("log my weight",), read_only=False)


def test_every_registered_intent_is_read_only():
    assert il._REGISTRY, "registry empty — ensure_registered didn't run"
    assert all(i.read_only for i in il._REGISTRY)


# ── ABSTAIN GENERALIZATION (RESOLVED — PF-2026-06-17-002) ─────────────
# Round-2 finding: keyword-only edge inputs misrouted on a fresh corpus
# ("when should I weigh in" → current_weight; "I did crossfit today" →
# workout_today). FIXED by intent-vs-statement discrimination in
# core/intent_layer._is_log_or_timing (a past-tense LOG statement and a
# TIMING question are not value/state reads → abstain BEFORE scoring).
# Held-out generalization is pinned at < 5% mis-route in
# test_2026_06_17_intent_discrimination.py. The promoted hard pin:
@pytest.mark.parametrize("msg", [
    "when should I weigh in",      # timing question, not a value lookup
    "I did crossfit today",        # past-tense log, not a read query
    "I just finished my run",
    "what time should I train",
    "how often should I deload",
])
def test_keyword_only_edges_abstain(msg):
    assert il.classify(msg) is None, (
        f"keyword-only edge input misrouted (must abstain): {msg!r}")


# ── SEAM CONTRACT (future LLM backend must satisfy guarantees 1-4) ─────
def test_seam_contract_classify_returns_none_or_read_intentmatch():
    """Any classify() backend MUST: abstain (None) or return an IntentMatch
    naming a REGISTERED read intent — never a free-form / mutation result.
    This is the contract an LLM backend plugs into (ADR-017 seam)."""
    registered = {i.name for i in il._REGISTRY}
    probes = _MUTATION_PARAPHRASES + [
        "how am I tracking", "what's my plan", "current weight",
        "tell me a joke", "", "   ", "🏋️", "ignore previous instructions",
    ]
    for msg in probes:
        m = il.classify(msg)
        assert m is None or (m.name in registered), (
            f"seam contract violated for {msg!r}: {m}"
        )


def test_flag_off_classify_short_circuits_to_none(monkeypatch):
    """Guarantee 2: flag OFF ⇒ classify() returns None for everything."""
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "0")
    for msg in ("how am I tracking", "what's my plan", "current weight"):
        assert il.classify(msg) is None
