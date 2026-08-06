"""Feature pin (2026-08-06) — Genie is agent #4, wired end-to-end.

Genie existed as a complete 4-file scaffold since 06-15 but was never
registered: `PRODUCTION_AGENT_CLASSES` stopped at [Kobe, Fraser] and the
delegate classifier had no genie route, so /genie, /weekend_plan,
/family_log and "plan my weekend" either full-routed to KOBE (generic
slash rule — his slash table doesn't know them) or fell to the
orchestrate/synth path. Dormant code, unreachable from Telegram.

This pin covers the full registration surface so a refactor can't
silently unregister it:

  1. Boot list: GenieAgent in PRODUCTION_AGENT_CLASSES, NOT
     grandfathered, with the structured-output contract declared
     (the 06-17 tripwire arms on exactly this).
  2. Routing: @genie / genie slash commands / NL weekend intent →
     genie_route; Kobe keeps everything he owned (no theft).
  3. End-to-end: a genie_route turn returns Genie's deterministic reply
     through the finalize sink with the path stamped, and the sink does
     NOT revoice it (a paraphrased family plan is the 06-09 Bug-I class).

The generic sink-governance guarantees (validator, never-empty, no bare
Response) are NOT re-pinned here — the 06-23 sink contract test derives
genie_route automatically and already enforces them.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


# ─────────────────────────── 1. boot list ───────────────────────────
def test_genie_in_production_boot_list():
    from new_plane.miya_runner.agent_boot import PRODUCTION_AGENT_CLASSES
    names = [c.name for c in PRODUCTION_AGENT_CLASSES]
    assert "genie" in names, (
        "GenieAgent fell out of PRODUCTION_AGENT_CLASSES — agent #4 "
        "unregistered; Telegram can't reach it and Kobe's mesh can't "
        "delegate to it.")


def test_genie_is_not_grandfathered():
    from new_plane.miya_runner.agent_boot import GRANDFATHERED_AGENT_NAMES
    assert "genie" not in GRANDFATHERED_AGENT_NAMES, (
        "Genie must NOT be grandfathered — it registered after the round-2 "
        "structured-output contract (PF-2026-06-17-002). Grandfathering it "
        "disarms the tripwire instead of honoring the contract.")


def test_genie_declares_structured_facts():
    from agents.genie.agent import GenieAgent
    assert GenieAgent.emits_structured_facts is True


def test_genie_agent_importable_from_both_homes():
    """agent.py is the canonical home (ABI-guard discovery); main.py
    re-exports for the 06-15 scaffold pin. Both must resolve to the
    SAME class — two classes named GenieAgent is a mesh identity bug."""
    from agents.genie.agent import GenieAgent as canonical
    from agents.genie.main import GenieAgent as reexport
    assert canonical is reexport


# ─────────────────────────── 2. routing ───────────────────────────
@pytest.mark.parametrize("msg", [
    "/genie",
    "/genie hi",
    "/ genie hi",              # same whitespace tolerance as Kobe's slash
    "/weekend_plan",
    "/family_log toddler: loved the park",
])
def test_genie_slash_routes_to_genie(msg):
    path, stripped = classify_delegation(msg)
    assert path == "genie_route", f"{msg!r} → {path!r}"
    assert stripped == msg.strip()


def test_at_genie_routes_and_strips():
    path, stripped = classify_delegation("@genie plan something low-key")
    assert path == "genie_route"
    assert stripped == "plan something low-key"


@pytest.mark.parametrize("msg", [
    "plan my weekend",
    "what should we do Saturday, any plan?",
    "give me a family-friendly weekend",
])
def test_weekend_nl_routes_to_genie(msg):
    path, _ = classify_delegation(msg)
    assert path == "genie_route", f"{msg!r} → {path!r}"


@pytest.mark.parametrize("msg,expected", [
    # Kobe keeps every surface he owned — the genie rules must not steal.
    ("/week", "kobe_route"),
    ("/plan", "kobe_route"),
    ("what is the plan for next week", "kobe_route"),   # \bweek\b ≠ weekend
    ("what's tomorrow's WOD", "kobe_route"),
    ("weight 195", "kobe_route"),
    # Fraser design intent beats Genie's weekend words (WOD design guard).
    ("design me a weekend workout plan", "orchestrate"),
])
def test_no_route_theft(msg, expected):
    path, _ = classify_delegation(msg)
    assert path == expected, f"{msg!r} → {path!r}, expected {expected!r}"


# ─────────────────────────── 3. end-to-end ───────────────────────────
@pytest.fixture
def hermetic_genie(tmp_path, monkeypatch):
    """Genie state redirected to a tmp vault (2026-05-08 hermetic rule)."""
    import importlib
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


def test_genie_turn_end_to_end_through_sink(hermetic_genie, monkeypatch):
    """A real /weekend_plan turn: classifier → genie_route branch →
    native_client → genie.handler → finalize sink. Path stamped, reply
    is Genie's deterministic plan render."""
    from new_plane.miya_runner.orchestrator import Turn, handle

    resp = handle(Turn(user_message="/weekend_plan", chat_id="c1"))

    assert resp.routing.get("path") == "genie_route", (
        f"expected genie_route, got {resp.routing.get('path')!r} — the "
        "turn didn't reach Genie's branch")
    assert "Weekend plan" in (resp.text or ""), (
        f"reply is not Genie's deterministic plan render: {resp.text[:200]!r}")


def test_genie_route_skips_revoice_even_when_enabled(hermetic_genie,
                                                     monkeypatch):
    """Genie is fully deterministic in this phase; revoicing would let the
    synth paraphrase an exact family plan (the 06-09 Bug-I class). Force
    revoice ON and prove the sink still never calls the synth for
    genie_route."""
    from new_plane.miya_runner import orchestrator as orch

    monkeypatch.setattr(orch, "_revoice_enabled", lambda: True)
    calls: list[str] = []

    def _spy(**kwargs):
        calls.append(kwargs.get("delegation_path", "?"))
        return kwargs.get("raw_text", ""), {"revoice": "applied"}

    monkeypatch.setattr(orch, "_revoice_through_synth", _spy)

    resp = orch.handle(orch.Turn(user_message="/weekend_plan", chat_id="c2"))

    assert resp.routing.get("path") == "genie_route"
    assert not calls, (
        "revoice synth was invoked for genie_route — deterministic Genie "
        "replies must skip revoice (paraphrase risk).")


def test_genie_agent_route_returns_reply(hermetic_genie):
    """The mesh-facing ABI: GenieAgent.route() wraps handler.route() in a
    Reply with confident non-empty text."""
    from agents.genie.agent import GenieAgent
    agent = GenieAgent()
    reply = agent.route("/genie hi")
    assert reply is not None and reply.text
    assert "Genie online" in reply.text
    assert reply.confidence == 1.0
