"""Regression/feature: cool-down can yield to the LLM path (2026-05-25).

Symptom (transcript review #F, reinforced 2026-05-25): "give me a
cool-down" hits a STATIC canned block on Kobe's dispatcher, and
re-asking "is this specific to my mobility?" returns the identical text
— never personalized to HRV / pain / neck-traps.

Fix (flag-gated, default OFF so behavior is unchanged until opted in):
when RAHAT_COOLDOWN_LLM is on, the dispatcher YIELDS the canned
post_recovery / pre_fuel routes so the ask falls through to the
personalized LLM path instead of the static block.

Pins:
  1. Default (flag off): "give me a cool-down" still returns the canned
     block — no behavior change for existing users.
  2. Flag on: the same message yields (dispatch returns None) so it can
     reach the composer / reasoner.
  3. The flag is scoped — it does NOT disable unrelated routes.
"""
from __future__ import annotations

from core import dispatcher


def test_cooldown_returns_canned_block_by_default(monkeypatch):
    monkeypatch.delenv("RAHAT_COOLDOWN_LLM", raising=False)
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)
    out = dispatcher.dispatch("give me a cool-down")
    assert out is not None, "default behavior must still answer cool-down"
    assert "recovery" in out.lower() or "cool" in out.lower(), out


def test_cooldown_yields_when_flag_on(monkeypatch):
    monkeypatch.setenv("RAHAT_COOLDOWN_LLM", "1")
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)
    out = dispatcher.dispatch("give me a cool-down")
    assert out is None, (
        "with RAHAT_COOLDOWN_LLM on, the canned cool-down route must "
        "yield so the ask reaches the personalized LLM path")
    # match_route still names the route (observability), but the route
    # is in the yield set.
    assert dispatcher.match_route("give me a cool-down") == "post_recovery"
    assert "post_recovery" in dispatcher._COOLDOWN_LLM_ROUTES


def test_flag_does_not_disable_unrelated_routes(monkeypatch):
    """Scope guard: the cool-down flag must not swallow other routes."""
    monkeypatch.setenv("RAHAT_COOLDOWN_LLM", "1")
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)
    # A weight log is unrelated and must still be claimed.
    assert dispatcher.match_route("wt: 199") not in (None, "post_recovery")
