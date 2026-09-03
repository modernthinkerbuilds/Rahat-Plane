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
      per day (vault marker), when a CrossFit-family workout landed in
      workouts_hk today OR when no workout landed at all (rest-day
      maintenance session — spec change 2026-08-25, owner: "I'd also
      want a cool down on days that I don't workout"). Run/walk-ONLY
      days stay silent by design — the owner asks directly on those.
      Flag HUBERMAN_AUTOCOOL default ON (explicit owner request),
      =0 kills it.
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
# `stre?t?ch`: the live 2026-09-02 ask arrived as "Streching routine"
# (phone keyboard) and fell straight past this pattern.
_COOLDOWN_NOUN = (
    r"cool[\s-]?downs?|"
    r"stre?t?ch(?:es|ing)?|"
    r"mobilit(?:y|ies)|mobili[sz]e|"
    r"down[\s-]?regulat\w*|downshift|"
    r"foam\s+roll\w*|lacrosse\s+ball|peanut\s+ball"
)
COOLDOWN_RE = re.compile(r"\b(" + _COOLDOWN_NOUN + r")\b", re.I)

# A cooldown REQUEST — request verb, then at most five words that are
# NOT a workout noun, then a cooldown noun. Consulted by the delegate
# classifier AHEAD of the design-preempt and pain rungs (2026-09-02
# live misroute: "I did today's WOD and my right hip feels sore at the
# catch, give me a good Streching routine" → "give me a" + "WOD"
# tripped design-preempt → orchestrate → the WOD lookup dumped the
# day's programming instead of a stretch). The no-workout-noun-between
# guard keeps "design me a workout with stretching at the end" on the
# Fraser side, and the request verb keeps bare pain reports ("my hip
# hurts when stretching") on Kobe's.
COOLDOWN_REQUEST_RE = re.compile(
    r"\b(?:give\s+me|i\s+need|need|i\s+want|want|suggest|recommend|"
    r"can\s+(?:you|i)\s+(?:get|have|do)|what'?s\s+a\s+good|design|"
    r"build|make\s+me|put\s+together|send\s+me)\b"
    r"(?:\s+(?!(?:workouts?|wods?|sessions?|metcons?|amrap|emom)\b)"
    r"[\w'\-]+){0,5}?\s+"
    r"(?:" + _COOLDOWN_NOUN + r")\b",
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
    workouts = hctx.workouts_today(now)
    cf = [w for w in workouts if not w["ask_direct"]]
    # Spec change 2026-08-25 (owner: "I'd also want a cool down on days
    # that I don't workout"): rest days now FIRE — a maintenance
    # mobility + down-regulation session biased to the hotspots. The
    # only silent case left is a run/walk-ONLY day, where the owner
    # works with Huberman directly (original 08-23 spec, unchanged).
    if workouts and not cf:
        return None                      # run/walk-only day → he asks
    from agents.huberman import coach
    steering = None
    if not workouts:
        steering = ("rest day — no training logged today, so skip the "
                    "post-workout framing: make this a maintenance "
                    "mobility session on the chronic hotspots, ending "
                    "in down-regulation for sleep")
    text = coach.generate_cooldown(now=now, steering=steering)
    state.mark_autocool(today)
    logger.info("[autocool] fired (%s)",
                ", ".join(w["name"] for w in cf) or "rest day")
    return text
