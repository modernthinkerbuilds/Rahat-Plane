"""agents.huberman.handler — routing surface + the 9:30 PM autocool.

Huberman S1 (2026-08-24, owner):
  "I want huberman at 9:30 pm autorun and see if i did a crossfit
   workout, if i did, i want it to look at my metrics, my limitations
   and give me a cool down. In case i dont like it, could talk to it on
   what kind of cooldown i want - ideally start with give 15 min
   including transition time. On days that i run, i will work with it
   directly and ask or if i do a different crossfit workout in a day, i
   will ask it directly."

Two entry points:

  route(message)        → str | None
      The @huberman / classifier path. Returns the cooldown for any
      cooldown/stretch/mobility-shaped ask (the message steers the
      re-cut: "more hips, less neck", "make it 10 min"). Returns None
      for everything else so native_client.huberman_route can keep
      delegating recovery-STATUS questions ("is my HRV ok") to Kobe's
      mesh — the 2026-06-14 parked-route pin's delegation contract
      stays true for that message class.

  maybe_autocool(now)   → str | None
      The miya_runner minute-tick hook (same family as Kobe's
      maybe_morning_briefing). Fires in the 21:30–21:59 window, once
      per day (vault marker), ONLY when a CrossFit-family workout
      landed in workouts_hk today. Run/walk-only days and rest days
      stay silent by design. Flag HUBERMAN_AUTOCOOL default ON
      (explicit owner request), =0 kills it.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("huberman")

# Cooldown-shaped asks. Deliberately DISJOINT from Kobe's _RECOVERY_RE
# family (breathing protocols, pre-fuel, post-recovery, "recovery
# routine") — Kobe keeps those; Huberman owns cooldown / stretching /
# mobility / down-regulation.
COOLDOWN_RE = re.compile(
    r"\b("
    r"cool[\s-]?downs?|"
    r"stretch(?:es|ing)?|"
    r"mobilit(?:y|ies)|mobili[sz]e|"
    r"down[\s-]?regulat\w*|downshift|"
    r"foam\s+roll\w*|lacrosse\s+ball|peanut\s+ball"
    r")\b",
    re.I,
)

_MINUTES_RE = re.compile(r"\b(\d{1,3})\s*(?:min(?:ute)?s?)\b", re.I)

AUTOCOOL_FLAG = "HUBERMAN_AUTOCOOL"
AUTOCOOL_HOUR = 21          # fire window 21:30–21:59
AUTOCOOL_MINUTE = 30


def autocool_enabled() -> bool:
    """Default ON — the 9:30 PM autorun is the feature the owner asked
    for by name. HUBERMAN_AUTOCOOL=0 disables."""
    return os.getenv(AUTOCOOL_FLAG, "1").lower().strip() in (
        "1", "true", "yes", "on")


def wants_cooldown(message: str) -> bool:
    return bool(COOLDOWN_RE.search(message or ""))


def _minutes_from(message: str) -> float | None:
    m = _MINUTES_RE.search(message or "")
    if m:
        val = int(m.group(1))
        if 3 <= val <= 120:
            return float(val)
    return None


def route(message: str, now: datetime | None = None,
          trace_id: str | None = None) -> str | None:
    """Answer cooldown-shaped asks; None hands everything else back to
    the caller's fallback (Kobe mesh, for recovery-status questions)."""
    text = (message or "").strip()
    if not text or not wants_cooldown(text):
        return None
    from agents.huberman import coach
    return coach.generate_cooldown(minutes=_minutes_from(text),
                                   steering=text, now=now,
                                   trace_id=trace_id)


def maybe_autocool(now: datetime | None = None) -> str | None:
    """The 21:30 tick. Contract mirrors Kobe's maybe_* nudges: cheap
    early-outs, marker dedup, never raises to the caller's loop."""
    now = now or datetime.now()
    if not autocool_enabled():
        return None
    if now.hour != AUTOCOOL_HOUR or now.minute < AUTOCOOL_MINUTE:
        return None
    from agents.huberman import state
    today = now.strftime("%Y-%m-%d")
    if state.autocool_sent(today):
        return None
    from agents.huberman import context as hctx
    cf = hctx.crossfit_workouts_today(now)
    if not cf:
        return None                      # run-only / rest day → silent
    from agents.huberman import coach
    names = ", ".join(w["name"] for w in cf)
    text = coach.generate_cooldown(now=now)
    state.mark_autocool(today)
    logger.info("[autocool] fired for: %s", names)
    return text
