"""Single source of truth for the production agents Miya registers at boot.

Adding an agent to the mesh = adding its class here (and `__main__` registers
from this list). This is also what the round-2 P0-2 structured-output tripwire
checks: any agent here that is NOT grandfathered must declare
`emits_structured_facts = True` (see core/agent.py + the contract test).
"""
from __future__ import annotations

from agents.the_scientist.agent import KobeAgent
from agents.fraser.agent import FraserAgent

# Production agents Miya registers, in order. The 4th agent goes here.
PRODUCTION_AGENT_CLASSES = [KobeAgent, FraserAgent]

# Agents that predate the structured-output contract (PF-2026-06-17-002) and
# rely on the outbound validator as their content gate. New agents are NOT
# grandfathered — they must emit numeric facts as typed fields.
GRANDFATHERED_AGENT_NAMES = frozenset(
    {"kobe", "fraser", "huberman", "the_scientist"})
