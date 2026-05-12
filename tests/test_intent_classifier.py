"""intent_classifier — unit + integration tests.

The semantic classifier replaces the prompt-anchor whack-a-mole with
an embedding-based intent match. The contracts pinned here:

  1. classify() returns (intent, similarity); intent is None when no
     anchor scored above INTENT_THRESHOLD.
  2. dispatch() runs the typed handler for a given intent — no LLM.
  3. route() consults the classifier *between* slash commands and the
     reasoner. A high-confidence match short-circuits the reasoner.
  4. Low-confidence (or RAHAT_INTENT_CLASSIFIER=0) falls through.
  5. An embedding failure (no API key, network down) MUST abstain,
     not crash — the reasoner takes over.

Each test is offline: no GEMINI_API_KEY, no Telegram, no live DB.
We stub `_embed` to return scripted vectors so the contract assertions
are deterministic without a network call.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# ─── 1. Math: cosine similarity ──────────────────────────────────
def test_cosine_identity():
    """A vector compared against itself should score 1.0."""
    from agents.the_scientist.intent_classifier import _cosine
    v = [1.0, 2.0, 3.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    """Perpendicular vectors must score 0.0."""
    from agents.the_scientist.intent_classifier import _cosine
    assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_zero_vector_is_safe():
    """A zero vector (failed embedding) must NOT raise — returns 0.0
    so the classifier abstains cleanly."""
    from agents.the_scientist.intent_classifier import _cosine
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_mismatched_dims_is_safe():
    """Length mismatch shouldn't crash — abstain via 0.0."""
    from agents.the_scientist.intent_classifier import _cosine
    assert _cosine([1.0], [1.0, 1.0]) == 0.0


# ─── 2. Anchors: every intent has the typed handler wired ────────
def test_every_intent_has_a_dispatch_binding():
    """If you add an intent to INTENT_ANCHORS, you MUST also wire it
    in dispatch()'s mapping. A new intent without a binding is dead
    code that misleads anyone reading the anchors file."""
    from agents.the_scientist import intent_classifier as ic

    # Build the dispatch mapping by calling dispatch() with each intent
    # against a stub handler that just records its call — but that's a
    # lot. Simpler: scrape the mapping out of dispatch().
    import inspect
    src = inspect.getsource(ic.dispatch)
    missing = []
    for intent in ic.INTENT_ANCHORS:
        # Each intent must appear as a dict key in dispatch's body.
        if f'"{intent}"' not in src and f"'{intent}'" not in src:
            missing.append(intent)
    assert not missing, (
        f"INTENT_ANCHORS has these intents with no dispatch binding: "
        f"{missing}. Every anchor must map to a typed handler."
    )


def test_intent_anchors_are_nonempty():
    """Every intent must have at least one canonical phrasing. An
    empty anchor list disables the intent silently."""
    from agents.the_scientist import intent_classifier as ic
    empty = [k for k, v in ic.INTENT_ANCHORS.items() if not v]
    assert not empty, f"These intents have no phrasings: {empty}"


def test_intent_anchors_have_unique_phrasings():
    """Duplicates across intents are an ambiguity bug — the same phrase
    can't belong to two intents."""
    from agents.the_scientist import intent_classifier as ic
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for intent, phrasings in ic.INTENT_ANCHORS.items():
        for p in phrasings:
            key = p.lower().strip()
            if key in seen:
                duplicates.append((p, seen[key], intent))
            else:
                seen[key] = intent
    assert not duplicates, (
        f"Duplicate phrasings across intents (will cause ambiguous "
        f"classification): {duplicates}"
    )


# ─── 3. Classify: deterministic via embedding stub ───────────────
@pytest.fixture
def stub_embeddings(monkeypatch, tmp_path):
    """Stub _embed() so each anchor lives in a distinct direction in
    a tiny 8-d space. The user message points exactly at one anchor's
    direction → cosine ~1.0 → that intent wins.

    Uses a per-test cache path (tmp_path) so cache writes from one test
    don't bleed into another — the anchor cache key is content-based,
    so two tests with the same INTENT_ANCHORS but different stubs
    would otherwise read stale embeddings from each other."""
    from agents.the_scientist import intent_classifier as ic

    # Reset the in-memory cache and point the disk cache at a fresh
    # path that no other test will share.
    monkeypatch.setattr(ic, "_ANCHOR_EMBEDDINGS", None)
    monkeypatch.setattr(ic, "_CACHE_PATH",
                        tmp_path / "intent_anchors.cache.json")

    # Map known phrases to fixed unit-vectors. Anything else gets a
    # zero vector (forces abstain).
    phrases: dict[str, list[float]] = {}

    def _stub_embed(text: str) -> list[float]:
        return phrases.get(text.lower().strip(), [0.0] * 8)

    monkeypatch.setattr(ic, "_embed", _stub_embed)

    class _F:
        def bind(self, text: str, vec: list[float]) -> None:
            phrases[text.lower().strip()] = vec

    return _F()


def test_classify_returns_none_below_threshold(stub_embeddings):
    """An unknown message produces a zero vector → all similarities
    are 0 → no intent."""
    from agents.the_scientist import intent_classifier as ic
    intent, sim = ic.classify("xx unknown gibberish")
    assert intent is None
    assert sim == 0.0


def test_classify_returns_top_match_above_threshold(stub_embeddings):
    """Bind a known anchor phrasing AND the user message to the same
    unit vector; classify must pick that intent with sim ~ 1.0."""
    from agents.the_scientist import intent_classifier as ic

    # Pick a known anchor for week_shape.
    target = ic.INTENT_ANCHORS["week_shape"][0]
    unit = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    stub_embeddings.bind(target, unit)

    # User message embeds to the same vector → cosine 1.0.
    user_msg = "which days am I in the gym this week"
    stub_embeddings.bind(user_msg, unit)

    intent, sim = ic.classify(user_msg)
    assert intent == "week_shape"
    assert sim > 0.99


def test_classify_picks_highest_among_two_intents(stub_embeddings):
    """When two intents would both match, the one with higher cosine
    wins. Anchor pace_check to (1,0,…) and weekly_remaining to slightly
    off-axis (0.9, 0.1,…); user message at (1,0,…) must pick pace_check."""
    from agents.the_scientist import intent_classifier as ic

    pace = ic.INTENT_ANCHORS["pace_check"][0]
    weekly = ic.INTENT_ANCHORS["weekly_remaining"][0]
    stub_embeddings.bind(pace,   [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    stub_embeddings.bind(weekly, [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    user_msg = "am I behind right now"
    stub_embeddings.bind(user_msg, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    intent, sim = ic.classify(user_msg)
    assert intent == "pace_check"
    # weekly_remaining's first phrasing scored ~0.9; pace scored ~1.0.
    assert sim > 0.99


def test_classify_off_switch(stub_embeddings, monkeypatch):
    """RAHAT_INTENT_CLASSIFIER=0 disables the classifier even when an
    anchor would match. This is the incident-debug escape hatch."""
    from agents.the_scientist import intent_classifier as ic
    monkeypatch.setenv("RAHAT_INTENT_CLASSIFIER", "0")

    target = ic.INTENT_ANCHORS["week_shape"][0]
    stub_embeddings.bind(target, [1.0] + [0.0] * 7)
    stub_embeddings.bind("which days am I in the gym this week",
                         [1.0] + [0.0] * 7)
    intent, _sim = ic.classify("which days am I in the gym this week")
    assert intent is None


def test_classify_empty_message_returns_none(stub_embeddings):
    from agents.the_scientist import intent_classifier as ic
    assert ic.classify("") == (None, 0.0)
    assert ic.classify("   ") == (None, 0.0)


# ─── 4. Dispatch: intent → typed handler ─────────────────────────
def test_dispatch_runs_typed_handler(monkeypatch):
    """dispatch('week_shape') must invoke handle_show_plan."""
    from agents.the_scientist import intent_classifier as ic
    from agents.the_scientist import handler as h

    monkeypatch.setattr(h, "handle_show_plan", lambda: "STUB_PLAN")
    assert ic.dispatch("week_shape") == "STUB_PLAN"


def test_dispatch_unknown_intent_returns_none():
    """A bogus intent name returns None so the caller falls through."""
    from agents.the_scientist import intent_classifier as ic
    assert ic.dispatch("definitely_not_an_intent") is None


def test_dispatch_handler_crash_returns_none(monkeypatch):
    """A handler exception inside dispatch() must NOT propagate — the
    classifier is a hint, not an oracle. Returning None lets the
    caller fall through to the reasoner."""
    from agents.the_scientist import intent_classifier as ic
    from agents.the_scientist import handler as h

    def _explode():
        raise RuntimeError("simulated handler crash")

    monkeypatch.setattr(h, "handle_pace", _explode)
    assert ic.dispatch("pace_check") is None


# ─── 5. route() integration ──────────────────────────────────────
def test_route_uses_classifier_before_reasoner(monkeypatch):
    """When the classifier returns a high-confidence intent, route()
    MUST dispatch to the typed handler and NOT consult the reasoner.
    This is the cost-discipline guarantee — typical paraphrasings
    never round-trip through Gemini."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h
    from agents.the_scientist import intent_classifier as ic
    import agents.the_scientist.reasoner as reasoner

    # Make the classifier return week_shape with high confidence.
    monkeypatch.setattr(ic, "classify", lambda msg: ("week_shape", 0.91))
    # The handler the intent dispatches to.
    monkeypatch.setattr(h, "handle_show_plan", lambda: "FROM_CLASSIFIER")
    # If the reasoner is called at all, the test fails.
    monkeypatch.setattr(reasoner, "reason",
                        lambda msg: pytest.fail(
                            f"reasoner.reason called with {msg!r} — "
                            "classifier short-circuit didn't fire"))

    assert h.route("anything matching week_shape") == "FROM_CLASSIFIER"


def test_route_falls_through_when_classifier_abstains(monkeypatch):
    """Low-confidence (intent=None) must fall through to the reasoner
    so genuinely free-form coaching questions still get the model."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h
    from agents.the_scientist import intent_classifier as ic
    import agents.the_scientist.reasoner as reasoner

    monkeypatch.setattr(ic, "classify", lambda msg: (None, 0.4))
    monkeypatch.setattr(reasoner, "reason", lambda msg: "FROM_REASONER")

    out = h.route("write me a poem about HRV")
    assert out == "FROM_REASONER"


def test_route_falls_through_when_classifier_crashes(monkeypatch):
    """A classifier bug MUST NOT take down the message path. If
    classify() raises, route() falls through to the reasoner."""
    monkeypatch.delenv("RAHAT_LEGACY_DISPATCH", raising=False)

    from agents.the_scientist import handler as h
    from agents.the_scientist import intent_classifier as ic
    import agents.the_scientist.reasoner as reasoner

    def _explode(msg):
        raise RuntimeError("simulated classifier crash")

    monkeypatch.setattr(ic, "classify", _explode)
    monkeypatch.setattr(reasoner, "reason", lambda msg: "FROM_REASONER")
    assert h.route("anything") == "FROM_REASONER"


def test_route_slash_command_beats_classifier(monkeypatch):
    """Order discipline: a /pace shortcut runs the slash dispatch,
    NEVER the classifier. Slash commands are cheapest and most
    explicit; they must come first."""
    from agents.the_scientist import handler as h
    from agents.the_scientist import intent_classifier as ic

    monkeypatch.setattr(h, "handle_pace", lambda: "FROM_SLASH")
    monkeypatch.setattr(ic, "classify", lambda msg: pytest.fail(
        f"classify called with {msg!r} for a slash command — "
        "the slash precheck did not fire first"))

    assert h.route("/pace") == "FROM_SLASH"
