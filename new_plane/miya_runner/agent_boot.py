"""Single source of truth for the production agents Miya registers at boot.

Adding an agent to the mesh = adding its class here (and `__main__` registers
from this list). This is also what the round-2 P0-2 structured-output tripwire
checks: any agent here that is NOT grandfathered must declare
`emits_structured_facts = True` (see core/agent.py + the contract test).
"""
from __future__ import annotations

from agents.the_scientist.agent import KobeAgent
from agents.fraser.agent import FraserAgent
from agents.genie.agent import GenieAgent

# Production agents Miya registers, in order.
# Genie (2026-08-06) is agent #4 — the first NON-grandfathered agent, so
# it is the first to arm the structured-output tripwire
# (test_2026_06_17_structured_output_contract.py) and the first real
# exercise of the 06-23 delegation-sink scaling contracts.
PRODUCTION_AGENT_CLASSES = [KobeAgent, FraserAgent, GenieAgent]

GENIE_OFF_LINE = ("Genie is off while we're {where}: no weekend plans, household calendar or event digest "
                  "from here. It comes back with the home site.")


def production_agents() -> list[type]:
    """The classes Miya registers at this site (core/site.py): the list
    above, without Genie where the site turns it off."""
    try:
        from core import site
        if not site.active().genie:
            return [c for c in PRODUCTION_AGENT_CLASSES if c is not GenieAgent]
    except Exception:  # noqa: BLE001 — an unreadable site never changes the roster
        pass
    return list(PRODUCTION_AGENT_CLASSES)


def genie_off_line() -> str | None:
    """What Miya says on Genie's rungs at a site that turns Genie off; None at home."""
    try:
        from core import site
        s = site.active()
        if not s.genie:
            return GENIE_OFF_LINE.format(where=f"in {s.label}" if s.label and s.label != s.name else "away")
    except Exception:  # noqa: BLE001
        pass
    return None


# Agents that predate the structured-output contract (PF-2026-06-17-002) and
# rely on the outbound validator as their content gate. New agents are NOT
# grandfathered — they must emit numeric facts as typed fields.
GRANDFATHERED_AGENT_NAMES = frozenset(
    {"kobe", "fraser", "huberman", "the_scientist"})
