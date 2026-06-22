"""Round-2 P0-2 — structured-output tripwire for the 4th agent.

The outbound validator is the SOLE content gate (the charter is content-blind)
and, even after the round-2 fix, is a phrasing-bounded backstop. The agreed
contract (PF-2026-06-17-002): every agent registered AFTER round 2 must emit
numeric facts as TYPED FIELDS, not free text, so a fabricated number is
structurally impossible — the validator demotes to a backstop.

Agent #4 doesn't exist yet, so this is a TRIPWIRE: it skips while only the
grandfathered agents (kobe / fraser / huberman) are in the boot list, and goes
RED the moment a NON-grandfathered agent is added with the contract unmet.
Genuineness: a deferred safety promise needs a failing test, not prose.

Checks class attributes only — no agent is instantiated (no DB / boot side
effects at collection time).
"""
from __future__ import annotations

import pytest

from core.agent import Agent
from new_plane.miya_runner.agent_boot import (
    PRODUCTION_AGENT_CLASSES,
    GRANDFATHERED_AGENT_NAMES,
)

_NON_GRANDFATHERED = [
    c for c in PRODUCTION_AGENT_CLASSES
    if getattr(c, "name", "") not in GRANDFATHERED_AGENT_NAMES
]


def test_contract_marker_exists_on_base():
    """The base Agent declares the contract (defaults to False)."""
    assert hasattr(Agent, "emits_structured_facts")
    assert Agent.emits_structured_facts is False


def test_boot_list_is_the_single_source_of_truth():
    """`__main__` must register from agent_boot.PRODUCTION_AGENT_CLASSES so a
    new agent can't be added to the runtime without passing this contract."""
    import inspect
    from new_plane.miya_runner import __main__ as boot
    src = inspect.getsource(boot)
    assert "PRODUCTION_AGENT_CLASSES" in src, (
        "__main__ must register agents from agent_boot.PRODUCTION_AGENT_CLASSES "
        "— otherwise a 4th agent can register without tripping this contract")


@pytest.mark.skipif(
    not _NON_GRANDFATHERED,
    reason="no post-contract agent in the boot list yet (agent #4 absent) — "
           "tripwire arms automatically when one is added",
)
@pytest.mark.parametrize(
    "agent_cls", _NON_GRANDFATHERED,
    ids=[getattr(c, "name", c.__name__) for c in _NON_GRANDFATHERED] or ["none"],
)
def test_new_agent_emits_structured_facts(agent_cls):
    assert getattr(agent_cls, "emits_structured_facts", False) is True, (
        f"{getattr(agent_cls, 'name', agent_cls.__name__)!r} is registered "
        f"after the round-2 structured-output contract but returns free-text "
        f"facts (emits_structured_facts=False). The validator is the SOLE "
        f"content gate and is phrasing-bounded — a fabricated number it can't "
        f"pattern-match will ship. New agents must emit numeric facts as typed "
        f"fields: set emits_structured_facts=True and return a typed reply.")
