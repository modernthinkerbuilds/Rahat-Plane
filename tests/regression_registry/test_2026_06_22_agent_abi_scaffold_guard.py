"""Scaling guard (2026-06-22, Test-Lead) — every Agent subclass under
agents/ must satisfy the core Agent ABI, whether or not it is wired into the
production boot list yet.

WHY. The owner is adding agents fast (genie, bajrangi already scaffolded).
The round-2 structured-output tripwire only checks agents ALREADY in
`PRODUCTION_AGENT_CLASSES` — a half-built agent can sit in the tree with a
broken ABI and nothing catches it until boot. This guard discovers every
`Agent` subclass by import and asserts the contract that the mesh relies on,
so a new agent is caught the moment its `agent.py` lands — before it is
registered, not after it breaks Telegram.

Pure class introspection: no agent is instantiated, no DB / network / boot
side effects.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import agents as _agents_pkg
from core.agent import Agent


def _discover_agent_classes() -> list[tuple[str, type]]:
    """Import every agents.*.agent module and collect concrete Agent
    subclasses. Returns (dotted_path, cls). Soft-skips dirs with no agent
    module (pure stubs like bajrangi's memory-only adapter)."""
    found: list[tuple[str, type]] = []
    for mod in pkgutil.iter_modules(_agents_pkg.__path__):
        if not mod.ispkg:
            continue
        agent_mod = f"agents.{mod.name}.agent"
        try:
            m = importlib.import_module(agent_mod)
        except ModuleNotFoundError:
            continue  # stub package without an agent.py — fine
        for attr in vars(m).values():
            if (isinstance(attr, type) and issubclass(attr, Agent)
                    and attr is not Agent
                    and attr.__module__ == m.__name__):
                found.append((f"{agent_mod}.{attr.__name__}", attr))
    return found


_AGENT_CLASSES = _discover_agent_classes()
_IDS = [name for name, _ in _AGENT_CLASSES]


def test_at_least_the_known_agents_are_discovered():
    names = {cls.name for _, cls in _AGENT_CLASSES}
    assert {"kobe", "fraser"} <= names, (
        f"expected kobe + fraser Agent subclasses; discovered {names}. "
        "If agent.py module layout changed, update the discovery.")


@pytest.mark.parametrize("path,cls",
                         _AGENT_CLASSES,
                         ids=_IDS or ["<none>"])
class TestAgentABI:
    def test_has_real_name(self, path, cls):
        assert isinstance(cls.name, str) and cls.name and cls.name != "unnamed", (
            f"{path}: every agent must set a non-default `name` (used for "
            "routing, logs, governance).")

    def test_has_description(self, path, cls):
        assert isinstance(getattr(cls, "description", ""), str) and \
            cls.description, f"{path}: agent must set a `description`."

    def test_route_is_overridden(self, path, cls):
        # An agent that doesn't override route() raises NotImplementedError at
        # runtime — a silent mesh hole. Require a real override.
        assert cls.route is not Agent.route, (
            f"{path}: agent must override route(); the base raises "
            "NotImplementedError.")

    def test_declares_structured_facts_contract(self, path, cls):
        # The marker must be an explicit bool on the class chain (the base
        # defaults to False). This is what the P0-2 tripwire keys on.
        assert isinstance(cls.emits_structured_facts, bool), (
            f"{path}: emits_structured_facts must be a bool.")

    def test_manifest_is_wellformed(self, path, cls):
        # manifest() is pure metadata (no I/O); a new agent must produce a
        # name-bearing manifest so registration + observability work.
        man = Agent.manifest(cls.__new__(cls))  # no __init__ side effects
        assert man.get("name") == cls.name


def test_every_booted_agent_was_discovered():
    """Defense in depth: every class in the production boot list must be one
    this guard actually checks — so the boot list can't reference an agent
    that dodges the ABI guard."""
    from new_plane.miya_runner.agent_boot import PRODUCTION_AGENT_CLASSES
    discovered = {cls for _, cls in _AGENT_CLASSES}
    for c in PRODUCTION_AGENT_CLASSES:
        assert c in discovered, (
            f"{c.__name__} is booted but not discovered by the ABI guard — "
            "the guard's discovery missed a registered agent.")
