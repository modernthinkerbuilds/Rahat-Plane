"""Genie — Rahat agent: household & weekend planning.

Thin, importlib-loadable entry point mirroring the four-file shape of
agents/the_scientist and agents/fraser:

    protocols.py → state.py → handler.py → main.py (here)

This file does two things:

  1. Star re-exports every public symbol from protocols/state/handler so
     the legacy short-name contract works — `genie.<symbol>` resolves the
     same way `sci.<symbol>` / `fraser.<symbol>` do. ScientistAgent and
     FraserAgent load their main.py via importlib under a short name; the
     GenieAgent below does the same.

  2. Exposes `GenieAgent` (name="genie") — a core.Agent subclass Miya can
     register and route to. The parent wires registration in
     new_plane/miya_runner/__main__.py (register(GenieAgent())); this
     module deliberately does NOT self-register so it stays import-safe
     and test-clean.

Multi-subject doctrine (PM thesis §3 rule #1): Genie reads ROLE-based
family Subjects from vault/family_profile.json (gitignored, PII-free).
See specs/agents/GENIE_AGENT_SPEC.md for the interface contract.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

# Repo root on path so package imports resolve under importlib loading.
# Idempotent — same pattern as the Scientist / Fraser main.py.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.agent import Agent, Reply  # noqa: E402

# Star-import order matters (protocols → state → handler) so every public
# symbol hangs off this module as `genie.<name>`.
from agents.genie.protocols import *  # noqa: F401, F403, E402
from agents.genie.state import *      # noqa: F401, F403, E402
from agents.genie.handler import *    # noqa: F401, F403, E402

from agents.genie import handler as _handler  # noqa: E402


_GENIE_MAIN_PATH = Path(__file__).resolve()


def _load_genie_module():
    """Import this main.py once under the short name 'genie' for the
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
    version = "0.1.0-genie-scaffold"

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
        """Delegate to handler.route(). A scaffold-phase error must not
        crash Miya's poll loop — log and decline."""
        try:
            text = _handler.route(msg, chat_id=chat_id) or ""
        except Exception as e:  # noqa: BLE001
            print(f"[genie.main] route() failed: {e}")
            return None
        confidence = 1.0 if text else 0.3
        return Reply(text=text, confidence=confidence)

    def tick(self, now: datetime | None = None) -> list[Reply]:
        """No background nudges in the scaffold phase. A future phase may
        emit a Friday weekend-plan preview here — Charter's quiet-hours
        policy already gates any nudge this method would produce."""
        return []


__all__ = ["GenieAgent"]


if __name__ == "__main__":
    # Symmetric with the Scientist / Fraser main.py shape. start() is a
    # no-op stub — Genie runs under Miya, not as its own process.
    from agents.genie.handler import start
    start()
