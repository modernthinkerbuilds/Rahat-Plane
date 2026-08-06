"""GenieAgent — household & weekend planning as a core.Agent subclass.

Canonical home of the class (moved from main.py, 2026-08-06) so the
agents.<pkg>.agent discovery convention holds: the ABI scaffold guard
(tests/regression_registry/test_2026_06_22_agent_abi_scaffold_guard.py)
imports `agents.*.agent` modules and collects Agent subclasses DEFINED
there — and its defense-in-depth check requires every class in
`PRODUCTION_AGENT_CLASSES` to be one it discovered. Kobe and Fraser
already follow this layout; Genie now matches. main.py re-exports
GenieAgent so the 06-15 scaffold pin (`from agents.genie.main import
GenieAgent`) keeps working.

Structured-output contract (PF-2026-06-17-002): Genie is the first
agent registered AFTER round 2, so it is NOT grandfathered —
`emits_structured_facts = True` below is load-bearing (the tripwire in
test_2026_06_17_structured_output_contract.py arms on this class). The
declaration is honest by construction in this phase: Genie is fully
deterministic (no LLM in the reply path), and every number in its
output is rendered from typed state — the WeekendPlan dataclass, the
categorical energy budget, and role-based FamilySubjects. There is no
free-text path through which a fabricated numeric fact can enter a
Genie reply. When the LLM overlay phase lands, the overlay must keep
facts in typed fields to keep this True truthful — the tripwire and
this comment are the tripwire for THAT.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

from core.agent import Agent, Reply

from agents.genie import handler as _handler

_GENIE_MAIN_PATH = Path(__file__).resolve().parent / "main.py"


def _load_genie_module():
    """Import main.py once under the short name 'genie' for the
    eval-suite naming convention (sci.<name> / fraser.<name> /
    genie.<name>). Idempotent."""
    if "genie" in sys.modules:
        return sys.modules["genie"]
    spec = importlib.util.spec_from_file_location("genie", _GENIE_MAIN_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"could not load Genie module at {_GENIE_MAIN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["genie"] = mod
    spec.loader.exec_module(mod)
    return mod


class GenieAgent(Agent):
    """The household & weekend-planning agent.

    Owns weekend-plan proposals, the family log, and the household energy
    model. Reads ROLE-based family Subjects (primary / spouse / toddler /
    newborn) — never real names / PII. Every state write is gated by
    core.charter.review() (in agents/genie/state.py).

    Coordination contract (spec):
        • READS family Subjects (vault/family_profile.json), own household
          store, and — future — cross-agent signals from Bourdain (travel
          preferences) and Disney (kid-itinerary energy).
        • WRITES two charter-gated kinds: genie.weekend_plan.commit and
          genie.family_log.append.

    Identity:
        • name="genie"
        • triggers=[]  (description-only classification, like Fraser Day-1)
        • aliases=[]
    """

    name = "genie"
    aliases: list[str] = []
    description = (
        "Household & weekend planner. Proposes Saturday/Sunday family "
        "plans sized to the household's energy budget (driven by the "
        "youngest family members), and keeps a family log of what worked. "
        "Use for: 'plan my weekend', 'what should we do Saturday', "
        "'give me a family-friendly weekend', 'log that the toddler "
        "loved the park', 'plan something low-key with the newborn' — "
        "any household, weekend, or family-activity planning question. "
        "DOES NOT own: workout design (defer to fraser), weight / HRV / "
        "recovery (defer to kobe). "
        "Defer to Kobe for: fitness, weight, HRV. "
        "Defer to Fraser for: workout / CrossFit programming."
    )
    triggers: list[str] = []
    version = "0.2.0-genie-registered"

    # PF-2026-06-17-002 — see module docstring for why this is honest.
    emits_structured_facts = True

    def __init__(self) -> None:
        super().__init__()
        # Pre-load the short-name module so `import genie` resolves to the
        # same instance after GenieAgent boots. Idempotent.
        self._mod = _load_genie_module()

    # ─── Agent ABI ─────────────────────────────────────────────────
    def route(
        self,
        msg: str,
        *,
        chat_id: str | int | None = None,
        db_path: str | None = None,
    ) -> Reply | None:
        """Delegate to handler.route(). An error must not crash Miya's
        poll loop — log and decline."""
        try:
            text = _handler.route(msg, chat_id=chat_id) or ""
        except Exception as e:  # noqa: BLE001
            print(f"[genie.agent] route() failed: {e}")
            return None
        confidence = 1.0 if text else 0.3
        return Reply(text=text, confidence=confidence)

    def tick(self, now: datetime | None = None) -> list[Reply]:
        """No background nudges yet. A future phase may emit a Friday
        weekend-plan preview here — Charter's quiet-hours policy already
        gates any nudge this method would produce."""
        return []


__all__ = ["GenieAgent"]
