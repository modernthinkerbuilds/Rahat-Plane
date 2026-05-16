"""FraserAgent — Fraser as a `core.Agent` subclass.

Mirrors `agents/the_scientist/agent.py` (which wraps the Scientist as
`KobeAgent`). Two responsibilities:

    1. Load `agents/fraser/main.py` via importlib and register it in
       `sys.modules` under the short-name `fraser` so the eval suite's
       `fraser.<symbol>` contract works.
    2. Implement the Agent ABI (`name`, `description`, `triggers`,
       `route()`, `tick()`) so Miya can route messages to Fraser.

Day-1 status:
    - `triggers` is EMPTY on purpose. Without explicit triggers, Miya
      falls back to description-based classification — and Fraser's
      route() returns Reply(confidence=0.1), so the Reply will only
      ever be shown if Miya's classifier confidently picks Fraser
      over Kobe. This keeps the live deployment safe while the
      reasoner is stubbed.
    - `miya.register(FraserAgent())` is INTENTIONALLY commented out
      in `core/miya_main.py`. Uncomment on Day 3 when the reasoner
      is real.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from core.agent import Agent, Reply

_FRASER_MAIN_PATH = Path(__file__).resolve().parent / "main.py"


def _load_fraser_module():
    """Import the legacy main.py once. Cached in sys.modules under
    'fraser' for compatibility with the eval-suite naming convention
    (sci.<name> for Scientist, fraser.<name> for Fraser)."""
    if "fraser" in sys.modules:
        return sys.modules["fraser"]
    spec = importlib.util.spec_from_file_location("fraser", _FRASER_MAIN_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(
            f"could not load Fraser module at {_FRASER_MAIN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fraser"] = mod
    spec.loader.exec_module(mod)
    return mod


class FraserAgent(Agent):
    """The CrossFit programming & performance agent.

    Owns workout design, weight prescription (% of 1RM math), equipment
    substitution, injury-driven mutes, the PRVN cycle, the 10-week chest
    progression, and the Workout Card output artifact.

    Coordination contract (spec §2):
        • READS Kobe (tier, 1RMs), Huberman (HRV, sleep, recovery),
          own state (workouts, injuries, PRVN, progression, prefs).
        • WRITES eleven kinds, all gated by `core/charter.review()`.
          Quiet-hour and HRV-red guards live in Charter policies.

    Identity:
        • name="fraser"
        • triggers=[]  (Day-1 safety — description-only classification)
        • aliases=[]   (no rebrand; net-new agent)
    """

    name = "fraser"
    aliases: list[str] = []
    description = (
        "CrossFit programming & performance lead. Owns workout design, "
        "weight prescription against 1RMs, equipment substitution, "
        "injury-driven movement mutes, the PRVN cycle, the 10-week "
        "chest progression, route metadata, and the Workout Card "
        "output artifact. Use for any question about today's workout, "
        "WOD design, EMOM / AMRAP / Tabata composition, working "
        "weights, calorie targets, movement substitutions, "
        "warm-ups / cool-downs, injury registration, or 1RM uploads."
    )
    # Day-1: NO triggers. The route() method returns a low-confidence
    # stub Reply so Miya's classifier will only land here for the
    # narrowest fitness questions. Day-3 wiring populates triggers
    # alongside the real reasoner.
    triggers: list[str] = []
    version = "0.1.0-day1-scaffold"

    def __init__(self) -> None:
        super().__init__()
        # Pre-load the main module so any caller that does
        # `import fraser` (short-name) after FraserAgent boots picks
        # up the same module instance. Idempotent.
        self._mod = _load_fraser_module()

    # ── Agent ABI ─────────────────────────────────────────────────
    def route(self, msg: str) -> Reply | None:
        """Delegate to handler.route(). Returns the low-confidence
        stub Reply on Day 1. Day 3 swaps in the real reasoner output.
        """
        try:
            return self._mod.route(msg)
        except Exception as e:
            # An error during the stub phase should NOT crash Miya's
            # poll loop — log and decline. Day-3 hardening can replace
            # this with proper error-bubble policy.
            print(f"[fraser.agent] route() failed: {e}")
            return None

    def tick(self, now=None):
        """No background nudges on Day 1. Day 3+ may schedule a morning
        Workout Card preview here. The Charter's quiet-hours policy
        already gates any nudge this method would emit."""
        return []


__all__ = ["FraserAgent"]
