"""Regression: the NL intent layer (ADR-017) is strictly additive
(2026-06-18, owner — built during the 48h autonomous window).

The intent layer catches PARAPHRASES of Kobe's read intents between the
deterministic dispatcher and the reasoner. These pins guard that it can
NEVER change existing routing:

  1. Flag OFF (default) ⇒ the layer abstains for everything — byte-identical
     to today's behavior.
  2. ORDERING INVARIANT: every phrasing the deterministic dispatcher owns
     is matched by the dispatcher, so route() returns BEFORE the intent
     layer is ever consulted. (The layer only runs after dispatch()==None.)
  3. READ-ONLY INVARIANT: no registered intent maps to a state-mutating
     handler — a fuzzy classifier can never trigger a write.
"""
from __future__ import annotations

import pytest

from core import dispatcher
from core import intent_layer as il


# ─────────────── 1. Flag OFF ⇒ no-op ───────────────
def test_flag_off_layer_is_inert(monkeypatch):
    monkeypatch.delenv("RAHAT_INTENT_LAYER", raising=False)
    # Strongest possible paraphrases still abstain when the flag is off.
    for msg in ("how am i doing", "what does my week look like",
                "how did last week go", "what's my plan next week"):
        assert il.classify(msg) is None
        assert il.route(msg) is None


# ─────────────── 2. Ordering invariant ───────────────
# Phrasings the dispatcher already owns. If the dispatcher matches, route()
# returns before the intent layer is reached, so the layer cannot override
# these no matter how it scores them.
_DISPATCHER_OWNED = [
    "/pace",
    "/plan",
    "what is the wod for tuesday",
    "show me friday's workout",
    "weight 198",
    "hrv 42",
    "tier hammer",
    "what's my plan",
    "what's my plan for next week",
    "how am i doing",
    "what's my current weight",
    "box breathing",
]


@pytest.mark.parametrize("msg", _DISPATCHER_OWNED)
def test_dispatcher_owns_its_phrasings(msg):
    """match_route returns a route name ⇒ dispatch() handles it ⇒ route()
    returns before the intent layer. This is the structural guarantee that
    the layer can only catch what the dispatcher MISSED."""
    assert dispatcher.match_route(msg) is not None, (
        f"{msg!r} is expected to be owned by the deterministic dispatcher; "
        "if this fails the intent layer's 'additive only' guarantee is at "
        "risk and routing must be re-checked")


# ─────────────── 3. Read-only invariant ───────────────
def test_no_registered_intent_is_a_mutation(monkeypatch):
    """Belt-and-braces: even if someone adds an intent later, the registry
    must contain only read handlers. We assert the registered handlers are
    NOT any of Kobe's known state-mutating entry points."""
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    il._clear_registry()
    il._REGISTERED = False
    il.ensure_registered()
    import agents.the_scientist.handler as k
    mutators = {
        getattr(k, n) for n in (
            "handle_weight", "handle_hrv", "handle_set_tier",
            "handle_profile", "handle_tolerate", "handle_swap",
            "handle_pick_days", "handle_unavailable", "handle_replan",
            "handle_manual_burn", "handle_fix_burn", "handle_clear_prefs",
        ) if hasattr(k, n)
    }
    for intent in il._REGISTRY:
        assert intent.handler not in mutators, (
            f"intent {intent.name!r} maps to a mutation handler — the "
            "intent layer must be read-only (guarantee 3)")
        assert intent.read_only is True
    il._clear_registry()
    il._REGISTERED = False
