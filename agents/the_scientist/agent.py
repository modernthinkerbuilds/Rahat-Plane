"""ScientistAgent — the Sports Scientist as a core.Agent subclass.

Phase Now refactor: wraps the existing `agents/the_scientist/main.py`
behavior in the unified Agent contract so Miya can register and route to
it the same way every future agent gets registered. No behavior change —
the eval suite (eval_suite.py, 125 cases) must pass identically.

A deeper split into `protocols.py` (pure math) + `handler.py` (route
dispatch) is intentionally deferred to a follow-up commit. The wrapper
shape here is what the orchestrator needs to ship the rest of Phase Now
with zero regression risk.

Why a wrapper over a rewrite right now:
    • The existing main.py boots a 24/7 launchd process today. Breaking
      it is a real-life-affecting incident.
    • The eval suite is exhaustive (~125 cases). Wrapping preserves the
      contract it tests, byte-for-byte.
    • Future agents (Coach, Curriculum) start clean — they get the new
      shape from day one without the Scientist having to reorg first.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from core.agent import Agent, Reply

_SCI_MAIN_PATH = Path(__file__).resolve().parent / "main.py"


def _load_scientist_module():
    """Import the legacy main.py once. Cached in sys.modules under
    'sci' for compatibility with the existing eval_suite naming.
    """
    if "sci" in sys.modules:
        return sys.modules["sci"]
    spec = importlib.util.spec_from_file_location("sci", _SCI_MAIN_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"could not load Scientist module at {_SCI_MAIN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = mod
    spec.loader.exec_module(mod)
    return mod


class KobeAgent(Agent):
    """The vitality agent (rebranded from ScientistAgent on 2026-05-12).

    Wraps the legacy main.py route()/tick() surface. Same code, same
    eval suite, same behavior — only the brand changed.

    Back-compat:
        • `name` is "kobe"; legacy "the_scientist" is recognized via
          `aliases` (Miya consults both when classifying).
        • The module-level `ScientistAgent` alias below lets any caller
          using the old class name keep working for one nightly cycle.
        • The decisions ledger STILL records `actor="scientist"` —
          trace continuity is preserved across the rename. Future
          actor-string migration is a separate, scoped change.

    See specs/ADR-002-rebrand-risk.md for the namesake-objects
    fallback (The Lab / Andrew / The Mamba — substrate unchanged).
    """

    name = "kobe"
    # Legacy name still recognized by Miya's routing layer. Drop after
    # one full week of green nightlies.
    aliases: list[str] = ["the_scientist"]
    # Day-8 rewrite per ADR-006 §"Required updates to agent descriptions".
    # The classifier in core/miya.classify_intent() reads this string
    # alongside Fraser's and Huberman's; the load-bearing line is the
    # final "Defer to Fraser for: …" sentence — without it the
    # classifier keeps picking Kobe for anything fitness-shaped because
    # Kobe's domain overlaps Fraser's. This is the exact failure mode
    # of the 2026-05-16 production bug ("what is the WOD" → Kobe
    # hallucinates instead of deferring).
    #
    # The verbatim string "Defer to Fraser for: workout design,
    # CrossFit programming, scaled loads, WOD selection." is pinned by
    # tests/test_kobe_description_contract.py — drift the wording in
    # a future refactor and the contract test fires.
    description = (
        "Vitality coach. Owns weight tracking, HRV interpretation, "
        "weekly caloric burn targets, weight-loss timeline math, "
        "recovery tier selection, breathing / cooldown / pre-fuel "
        "protocols, and the Hyderabadi-direct coaching voice. "
        "Use for: 'what's my weight', 'log my weight 195', "
        "'what's my HRV say', 'how am I tracking this week', "
        "'when will I hit 80 kg', 'set tier hammer', "
        "'7/15 breathing', 'pre-workout fuel', 'pace check', "
        "'what's my weekly burn target' — any weight-tracking, "
        "HRV-interpretation, burn-target, timeline-math, recovery-"
        "tier, or breathing/cooldown/pre-fuel question. "
        "Defer to Fraser for: workout design, CrossFit programming, "
        "scaled loads, WOD selection."
    )
    version = "0.8.0-day8-mesh-routing"

    # Day-8: trigger list PRUNED per ADR-006. The capability classifier
    # in core/miya.classify_intent() now reads the description above
    # and handles fuzzy semantic routing — broad workout-keyword
    # patterns ("workout" / "crossfit" / "cf" / "wod" / "zone 2" /
    # "z2" / "plan" / "schedule") were REMOVED because they captured
    # every fitness-shaped query and starved Fraser of its own domain
    # in the fallback path.
    #
    # What's left is the deterministic backstop: numeric weight logging,
    # explicit HRV numbers, today/yesterday/last-week/remain lookups,
    # tier color, breathing/cooldown/pre-fuel, manual burn logging,
    # pace/status. These are unambiguous Kobe territory even when the
    # classifier is unavailable (no API key, network error, test
    # sandbox). See ADR-006 §"Retirement" — these go away entirely
    # after one full week of green nightlies on classifier-routing.
    triggers = [
        # Daily / weekly burn lookups
        r"\b(today|yesterday|now)\b",
        r"\bthis\s+week\b",
        r"\blast\s+week\b",
        r"\b(remain|left|remaining)\b.*\b(week|cal|kcal|burn|deficit)\b",
        # Weight lookups + logging
        r"\b(?:weight|wt)[:\s]+\d",
        r"\b(?:current\s+weight|how\s+much\s+do\s+i\s+weigh|weight\s+now)\b",
        r"\bhow\s+long\s+to\s+\d+\s*(?:kg|lbs?)\b",
        r"\bto\s+\d+\s*(?:kg|lbs?)\s+by\b",
        # HRV (numeric — "hrv 42" is unambiguous; bare "hrv" alone
        # could be a Huberman interpretation question and is left to
        # the classifier).
        r"\bhrv\s+\d",
        # Tier color — "tier hammer" / "tier red" etc. The bare word
        # "tier" used to match anything-tier; tightened to require a
        # color/level token so "tier list" / "tier guide" don't fire.
        r"\btier\s+(survival|re.?entry|baseline|performance|hammer|red|yellow|green)\b",
        # Coaching protocols
        r"\b(7\s*/?\s*15|box\s+breath|breathing|cooldown|stretch|pre[-\s]?workout|pre[-\s]?fuel)\b",
        # Manual logging
        r"\b(burned|wod|run|walk)\s+\d+\b",
        # Pace / status
        r"\b(pace|on\s+track|status)\b",
        # ───── REMOVED in Day-8 (ADR-006) ─────────────────────────
        # The following patterns are DELETED on purpose. Classifier
        # owns these now via the description's "Defer to Fraser for"
        # line. Keep this comment as the rationale anchor — future
        # refactors that re-add these patterns reintroduce the
        # 2026-05-16 bug.
        #
        # r"\b(plan|schedule|which\s+days|when\s+(?:do|am|will)\s+i)\b"
        # r"\b(crossfit|cf|wod|zone\s*2|z2|workout)\b"
        # ──────────────────────────────────────────────────────────
    ]

    def __init__(self) -> None:
        super().__init__()
        self._sci = _load_scientist_module()

    # ─── routing ───
    def route(
        self,
        msg: str,
        *,
        chat_id: str | int | None = None,
        db_path: str | None = None,
    ) -> Reply | None:
        """Delegate to legacy main.route(). Returns None if the legacy
        router would have fallen through to LLM coaching with no domain
        anchor — Miya may then route elsewhere or synthesize.

        For Phase Now we always return a Reply: the legacy router never
        returns None today (it falls through to llm_coach). Confidence
        is 1.0 for trigger-matched messages, 0.5 for fallthroughs that
        landed here by classifier.

        `chat_id`/`db_path` are accepted for ABI parity (Miya passes
        them to every agent) but Kobe doesn't keep per-conversation
        memory yet, so they're intentionally unused here.
        """
        text = self._sci.route(msg) or ""
        confidence = 1.0 if self.matches(msg) else 0.5
        return Reply(text=text, confidence=confidence)

    # ─── scheduler ───
    def tick(self, now: datetime | None = None) -> list[Reply]:
        """Return zero or more nudge Replies. Wraps the four legacy
        ticker functions; preserves their throttling state in the DB.
        """
        replies: list[Reply] = []
        # Recalibrate intent ETAs against the freshest weight (cheap).
        try:
            self._sci.recalibrate_intents()
        except Exception as e:
            print(f"[scientist.tick] recalibrate failed: {e}")
        for fn in (self._sci.maybe_morning_briefing,
                   self._sci.maybe_weekly_reset,
                   self._sci.maybe_recovery_nudge,
                   self._sci.maybe_walk_nudge):
            try:
                msg = fn()
            except Exception as e:
                print(f"[scientist.tick] {fn.__name__} failed: {e}")
                continue
            if msg:
                # Nudges are unsolicited — lower confidence so Miya / the
                # Charter can budget them against quiet hours.
                replies.append(Reply(text=msg, confidence=0.7))
        return replies

    def on_start(self) -> None:
        """Idempotent — touches the DB to seed intents and create tables."""
        try:
            self._sci._db().close()
        except Exception as e:
            print(f"[scientist.on_start] db seed failed: {e}")


# Module-level singleton. Miya imports this directly; agent host CLIs can
# also instantiate fresh ones for offline/test runs.
KOBE = KobeAgent()

# Back-compat aliases — kept for one nightly cycle. Code that imports
# ScientistAgent / SCIENTIST keeps working unchanged. Schedule for
# removal: one week after 2026-05-12 (see ADR-002).
ScientistAgent = KobeAgent
SCIENTIST = KOBE
