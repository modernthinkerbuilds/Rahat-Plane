"""Rebrand contract — Scientist→Kobe, Bajrangi→Huberman (2026-05-12).

Pins the invariants the sprint promised so a future refactor can't
silently undo the rebrand or the substrate-preservation guarantees:

  1. `agents.kobe.X` and `agents.the_scientist.X` are the SAME module
     for every submodule. No accidental duplication.
  2. `KobeAgent` and `ScientistAgent` are the SAME class.
  3. The agent's canonical name is "kobe".
  4. Legacy "the_scientist" is listed in `aliases` so Miya's
     classifier still routes legacy LLM outputs correctly.
  5. The decisions-ledger actor string is STILL "scientist" — trace
     continuity is preserved (see ADR-002).
  6. `agents.huberman` is wired the same way (alias of `agents.bajrangi`).
  7. Miya's `list_capabilities()` surfaces aliases so external callers
     can discover them.
  8. The ADR exists and names the fallback options.

Every test is offline. No GEMINI_API_KEY, no Telegram, no DB writes.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


# ─── 1. Module identity ──────────────────────────────────────────
@pytest.mark.parametrize("name", [
    "agent", "coach_system", "handler", "main", "memory",
    "protocols", "reasoner", "state", "tools",
])
def test_kobe_submodule_is_same_object_as_the_scientist(name):
    """agents.kobe.X must be the SAME module object as
    agents.the_scientist.X. The kobe package is a sys.modules alias —
    if a future refactor copies files instead, this test fails."""
    import importlib
    new = importlib.import_module(f"agents.kobe.{name}")
    old = importlib.import_module(f"agents.the_scientist.{name}")
    assert new is old, (
        f"agents.kobe.{name} is NOT the same object as "
        f"agents.the_scientist.{name}. The rebrand alias broke — "
        f"see agents/kobe/__init__.py for the sys.modules aliasing."
    )


def test_huberman_memory_is_same_object_as_bajrangi():
    import importlib
    new = importlib.import_module("agents.huberman.memory")
    old = importlib.import_module("agents.bajrangi.memory")
    assert new is old


# ─── 2-4. Agent class + name + aliases ───────────────────────────
def test_kobe_agent_class_is_same_as_scientist_agent():
    from agents.kobe.agent import KobeAgent
    from agents.the_scientist.agent import ScientistAgent
    assert KobeAgent is ScientistAgent, (
        "KobeAgent and ScientistAgent must be the SAME class. The "
        "rebrand keeps the original class definition and aliases the "
        "name via `ScientistAgent = KobeAgent` — if someone defines a "
        "separate class, downstream isinstance() checks break."
    )


def test_canonical_name_is_kobe():
    from agents.kobe.agent import KobeAgent
    assert KobeAgent.name == "kobe", (
        f"KobeAgent.name = {KobeAgent.name!r}, expected 'kobe'. "
        "The brand is the canonical name; legacy 'the_scientist' "
        "lives in `aliases` instead."
    )


def test_legacy_name_lives_in_aliases():
    from agents.kobe.agent import KobeAgent
    assert "the_scientist" in KobeAgent.aliases, (
        f"KobeAgent.aliases = {KobeAgent.aliases!r}. The legacy name "
        "'the_scientist' must be listed so Miya's LLM classifier still "
        "routes a 'the_scientist' output to this agent. Drop after one "
        "week of green nightlies (see ADR-002)."
    )


# ─── 5. Decisions ledger actor — trace continuity ────────────────
def test_decisions_actor_still_says_scientist():
    """The decisions ledger records actor='scientist' for every trace
    the reasoner emits. Renaming the actor string would break every
    historical trace lookup — preserve it explicitly. See ADR-002."""
    src = (ROOT / "agents" / "the_scientist" / "reasoner.py").read_text()
    assert 'actor="scientist"' in src, (
        "agents/the_scientist/reasoner.py must keep actor='scientist' "
        "in the decisions.span() calls. Trace continuity across the "
        "rebrand depends on the substrate string staying the same."
    )


# ─── 7. Miya capability registry exposes aliases ─────────────────
def test_miya_list_capabilities_includes_aliases():
    """The capability dict returned by Miya must include `aliases` so
    external callers (CLI, future agent-mesh-aware tools) can see the
    full set of names this agent answers to."""
    from core import miya
    from agents.kobe.agent import KobeAgent
    miya.clear_registry()
    try:
        miya.register(KobeAgent())
        caps = miya.list_capabilities()
        assert len(caps) == 1
        assert "aliases" in caps[0], (
            "Miya.list_capabilities() must surface the `aliases` field. "
            "Without it, external callers can't discover that 'kobe' "
            "and 'the_scientist' refer to the same agent."
        )
        assert "the_scientist" in caps[0]["aliases"]
    finally:
        miya.clear_registry()


def test_miya_classifier_recognizes_legacy_alias(monkeypatch):
    """Miya's LLM classifier must accept 'the_scientist' as a vote for
    the agent whose canonical name is 'kobe'. This is the routing
    invariant — without it, the LLM output 'the_scientist' would fail
    to match and Miya would fall back to first-candidate."""
    from core import miya
    from core import io as cio
    from agents.kobe.agent import KobeAgent

    monkeypatch.setattr(cio, "llm_generate", lambda prompt: "the_scientist")

    agent = KobeAgent()
    # Two candidates so _classify_via_llm actually runs the matcher.
    class _Decoy:
        name = "decoy"
        description = "should never be picked"
        aliases: list[str] = []

    picked = miya._classify_via_llm("anything", [agent, _Decoy()])
    assert picked is agent, (
        "Miya's LLM classifier must map 'the_scientist' (a legacy "
        "alias) back to the new agent. KobeAgent.aliases contract."
    )


# ─── 8. ADR exists ───────────────────────────────────────────────
def test_adr_002_exists_with_fallback_table():
    """The rebrand-risk ADR (ADR-002) must exist with the namesake-
    objects fallback options spelled out — that's our ripcord if
    either namesake ever objects."""
    adr = ROOT / "specs" / "ADR-002-rebrand-risk.md"
    assert adr.exists(), f"{adr} is missing"
    body = adr.read_text()
    for required in ("Mamba", "Andrew", "The Lab"):
        assert required in body, (
            f"ADR-002 must mention the fallback option {required!r}."
        )
    assert "namesake" in body.lower(), (
        "ADR-002 must explicitly discuss the namesake-objects risk."
    )
