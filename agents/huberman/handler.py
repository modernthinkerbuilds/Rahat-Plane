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
# live misroute: "I did today's WOD and <a joint> feels sore, give me a
# good stretching routine" → "give me a" + "WOD" tripped design-preempt
# → orchestrate → the WOD lookup dumped the day's programming instead
# of a stretch). The no-workout-noun-between
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

# QA-20 (2026-10-11): on a CrossFit-plan day with nothing synced, the
# autorun says it cannot tell and asks "Trained?". For OFFER_HOURS a short
# reply answers it: training words or a bare yes → the post-workout
# version, a no → the rest-day session stands.
OFFER_HOURS = 3
_TRAINED_RE = re.compile(
    r"\b(?:did|done|finished|crushed|smashed|hit)\b.{0,20}?"
    r"\b(?:wod|workout|crossfit|class|session)\b"
    r"|\btrained\b|\bworked\s+out\b|\bwent\s+to\s+(?:the\s+)?(?:gym|box|class)\b"
    r"|\bafter\s+(?:the\s+|my\s+|today'?s\s+)?(?:wod|workout|session|class)\b"
    r"|\bpost[\s-]?(?:workout|wod|session)\b", re.I)
_RESTED_RE = re.compile(
    r"\b(?:didn'?t|did\s+not|not|never|skipped|missed)\b.{0,12}?"
    r"\b(?:train(?:ed)?|go|went|wod|workout|crossfit|class|gym)\b"
    r"|\bno\s+(?:workout|wod|training|crossfit|class|gym)\b"
    r"|\brest(?:ed)?\b|\bskipped\b", re.I)
_YES_RE = re.compile(r"^\s*(?:yes|yep|yeah|yup|ya|y|sure|done|i\s+did|did\s+it)\b[\s.!]*$", re.I)
_NO_RE = re.compile(r"^\s*(?:no|nope|nah|n|not\s+today)\b[\s.!]*$", re.I)
REST_ACK = "Rest day, then. Tonight's session above stands. 🌙"


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


def _says_trained(text: str) -> bool:
    return bool(_TRAINED_RE.search(text)) and not _RESTED_RE.search(text)


def offer_reply(message: str, now: datetime | None = None) -> str | None:
    """How a reply answers tonight's open "Trained?" (QA-20): "specific"
    when it names training either way ("did the WOD", "didn't train"),
    "generic" for a bare yes or no, None when no offer is open or the
    reply is about something else. Never raises."""
    try:
        from agents.huberman import state
        if not state.offer_open(now or datetime.now(), OFFER_HOURS):
            return None
    except Exception:  # noqa: BLE001 — no state → no offer
        return None
    text = (message or "").strip()
    if _RESTED_RE.search(text) or _TRAINED_RE.search(text):
        return "specific"
    if _YES_RE.match(text) or _NO_RE.match(text):
        return "generic"
    return None


def _answer_offer(text: str, now: datetime, trace_id: str | None) -> str:
    from agents.huberman import coach, state
    state.close_offer()
    if _RESTED_RE.search(text) or _NO_RE.match(text):
        logger.info("[autocool] answered: rest day")
        return REST_ACK
    logger.info("[autocool] answered: trained — the post-workout version")
    return coach.generate_cooldown(minutes=_minutes_from(text),
                                   steering="post-workout: they trained today",
                                   now=now, trace_id=trace_id, trained=True)


def route(message: str, now: datetime | None = None,
          trace_id: str | None = None) -> str | None:
    """Answer cooldown-shaped asks, and a reply to tonight's "Trained?";
    None hands everything else back to the caller's fallback (Kobe mesh,
    for recovery-status questions)."""
    text = (message or "").strip()
    if not text:
        return None
    now = now or datetime.now()
    if offer_reply(text, now):
        return _answer_offer(text, now, trace_id)
    if not wants_cooldown(text):
        return None
    from agents.huberman import coach
    return coach.generate_cooldown(minutes=_minutes_from(text),
                                   steering=text, now=now,
                                   trace_id=trace_id,
                                   trained=_says_trained(text))


def _watch_silence(now: datetime) -> str | None:
    """Kobe's "No Watch data since …" when the feed has been silent over
    a day (QA-21, 2026-10-11); None otherwise. Never raises."""
    try:
        from agents.the_scientist.state import watch_silence
        return watch_silence(now)
    except Exception:  # noqa: BLE001
        return None


def _with_offer(text: str) -> str:
    """The honest line leads (the model path may not say it) and the
    question closes the message."""
    from agents.huberman.coach import OFFER_LINE, UNSURE_LINE
    if "nothing has synced" not in text.lower():
        text = f"_{UNSURE_LINE}_\n\n{text}"
    return f"{text}\n\n{OFFER_LINE}"


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
    ctx = hctx.gather(now)
    workouts = ctx["workouts"]
    cf = ctx["crossfit_today"]
    cardio = ctx.get("cardio_today") or []
    # Spec change 2026-08-25 (owner: "I'd also want a cool down on days
    # that I don't workout"): rest days now FIRE — a maintenance
    # mobility + down-regulation session biased to the hotspots.
    # 2026-10-18 (owner): a run day gets the post-run cooldown — the
    # Watch's minute data shows the run hours before the workout export
    # (context.sessions_from_vitals), and that night's "rest day" was
    # wrong. The only silent case left is a walk-only day, where the
    # owner works with Huberman directly (original 08-23 spec).
    # 2026-10-19: a gym session read from the heart-rate trace is in `cf`
    # like a typed one (the export names today's sessions tomorrow).
    if workouts and not cf and not cardio:
        return None                      # walk-only day → he asks
    from agents.huberman import coach
    steering = None
    # 2026-09-03: "rest day" framing only when the day really looks
    # like rest. If the plan says CrossFit and a WOD is programmed but
    # the Watch export hasn't run (the 09-02 "No workout logged" night),
    # context carries trained_today/assumed_from_plan and the coach
    # frames it honestly as the programmed WOD.
    unsure = bool(ctx.get("plan_unsynced"))
    if cardio and not cf and not ctx.get("trained_today"):
        s = cardio[0]
        word = coach.CARDIO_WORD.get(s.get("kind"), "session")
        dist = f", {s['distance_km']} km" if s.get("distance_km") else ""
        steering = (f"post-{word} — a {s['minutes']}-minute {word} today{dist} and no "
                    f"CrossFit session: coach it as the post-{word} cooldown, what the "
                    f"{word} loaded first (calves, feet, hamstrings, hip flexors), then "
                    "the chronic hotspots, ending in down-regulation for sleep; do not "
                    "call it a rest day")
    elif not workouts and not ctx.get("trained_today"):
        steering = ("rest day — no training logged today, so skip the "
                    "post-workout framing: make this a maintenance "
                    "mobility session on the chronic hotspots, ending "
                    "in down-regulation for sleep")
        if unsure:                      # QA-20: the plan said CrossFit
            steering = ("the plan says CrossFit but nothing has synced from "
                        "the Watch or the gym, so you cannot tell whether "
                        "they trained: make this the rest-day maintenance "
                        "session on the chronic hotspots, ending in "
                        "down-regulation for sleep, and do not say they rested")
    text = coach.generate_cooldown(now=now, steering=steering)
    if unsure:
        text = _with_offer(text)
        state.open_offer(now)
    silent = _watch_silence(now)        # QA-21: say why nothing synced
    if silent:
        text = f"_{silent}._\n\n{text}"
    state.mark_autocool(today)
    logger.info("[autocool] fired (%s)",
                ", ".join(w["name"] + (f" {w['minutes']} min (from the heart-rate trace)" if w.get("inferred") else "")
                          for w in cf)
                or ", ".join(f"{w['name']} {w['minutes']} min" + (" (inferred)" if w.get("inferred") else "")
                             for w in cardio)
                or ("programmed WOD, Watch unsynced"
                    if ctx.get("assumed_from_plan")
                    else "plan CrossFit, nothing synced — asked whether trained"
                    if unsure else "rest day"))
    return text
