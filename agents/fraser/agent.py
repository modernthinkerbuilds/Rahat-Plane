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
    # Day-8 rewrite per ADR-006 §"Required updates". The classifier
    # reads this string alongside Kobe's description; "DOES NOT own"
    # is the territory-disambiguating line that stops Kobe winning
    # workout questions. Phrasings in the "Use for:" list are the
    # ones the classifier will actually see — they're paraphrases
    # of how the owner asks.
    # Day-9 rewrite (2026-05-17 production incident): "What is my
    # workout for Tuesday?" was landing at Fraser and hitting Fraser's
    # default-mode stub instead of being recognized as a Kobe lookup
    # of an already-prescribed plan. The two Day-8 "DOES NOT own"
    # disclaimer blocks correctly identified the territory but were
    # diffuse — the classifier reads compact "Defer to X for: …"
    # sentences (Kobe's pattern) much more reliably than prose
    # disclaimers. This rewrite KEEPS both Day-8 disclaimers (they
    # carry the long-form domain enumeration the classifier still
    # benefits from) AND adds the compact "Defer to Kobe for: …"
    # sentence mirroring Kobe's "Defer to Fraser for: …" pattern.
    #
    # All three byte-pinned by tests/test_fraser_description_contract.py.
    description = (
        "CrossFit + Zone-2 workout designer. Given today's SugarWOD "
        "programming and your 1RMs / HRV / dislikes / equipment / "
        "injuries, produces a fully adapted Workout Card with warm-up, "
        "scaled WOD movements with calculated loads, predicted burn "
        "against Kobe's target, cool-down, and PRVN reset. "
        "Use for: 'what's my WOD', 'give me today's workout', "
        "'I want to do PRVN now', 'make-up session for Friday', "
        "'can I substitute X for Y', "
        "'scale this WOD for me', 'do today's CrossFit with "
        "adjustments for my knee', 'give me a 75-minute session "
        "that burns 800 kcal' — any workout-prescription, scaling, "
        "substitution, or adaptation question. "
        "DOES NOT own: weight tracking, weekly burn targets, HRV "
        "interpretation, weight-loss timeline math, recovery tier "
        "selection. For those, delegate to kobe or huberman. "
        "DOES NOT own: lookup of scheduled workouts, "
        "'what is my workout on [day]', "
        "'what is my workout for [day]', 'what's planned', "
        "weekly plan view, which days am I working out. "
        "For all lookup questions about the user's synced plan, "
        "defer to kobe. "
        "Defer to Kobe for: weekly plan lookups, weekday-specific "
        "workout lookups, weight tracking, HRV interpretation, "
        "recovery tier."
    )
    # Day-8: triggers stay empty per ADR-006 (capability classifier
    # uses descriptions; triggers are fallback only). The legacy
    # regex-trigger world is being retired across the mesh.
    triggers: list[str] = []
    version = "0.9.0-day9-bug3-lookup-boundary"

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
