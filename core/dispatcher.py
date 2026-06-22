"""core.dispatcher — Single ordered route table (ADR-009, Option C).

THE PROBLEM
-----------
Pre-ADR-009 routing layered ten decision points (Miya classifier → slash
bypass → trigger fallback → clarification → agent classifier → agent slash
dispatch → agent delegate → agent reasoner → agent legacy regex → agent
default-mode stub). Each layer could intercept a message and route it
incorrectly. In 48 hours we shipped seven P0 fixes for the same class of
bug — wrong layer winning.

THE FIX
-------
Replace the cake with ONE ordered dispatch table. First regex match wins.
The LLM reasoner becomes a last-resort fallback for genuinely open-ended
queries that have no factual pattern. The reasoner NEVER gets a chance to
hallucinate "what is the WOD for Tuesday" because the dispatcher matches
it deterministically and calls handle_gym_wod_on() directly.

THE GUARANTEE
-------------
For every query in the dispatch table, there is exactly ONE place where
its routing is decided. Bugs become localized.

USAGE
-----
    from core import dispatcher

    result = dispatcher.dispatch(msg)
    if result is not None:
        return result                  # handler ran, return its text
    # else: fall through to reasoner

ADDING A ROUTE
--------------
Add a new entry to the ROUTES list below. Each route is a Route dataclass
with a name, regex, handler factory, and optional priority.

The route ORDER in the list IS the priority. Earlier entries win over
later entries. When in doubt, place more-specific patterns FIRST.

ROLLBACK
--------
Set RAHAT_USE_DISPATCHER=0 to bypass the dispatcher entirely. Production
falls back to the legacy ten-layer flow.

TESTING
-------
- tests/test_dispatcher.py — unit tests for every Route
- tests/regression_registry/test_2026_05_19_single_dispatcher_routes.py —
  pins each route against real user phrasings from the production
  decisions ledger.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Optional, Pattern


# ─────────────────────── Feature flag ───────────────────────
_ENV_USE = "RAHAT_USE_DISPATCHER"


def enabled() -> bool:
    """Return True if the dispatcher should run. Default ON."""
    val = os.getenv(_ENV_USE, "1").lower().strip()
    return val not in ("0", "false", "off", "no")


# Routes that return a STATIC coaching block and should YIELD to the
# personalized LLM path when RAHAT_COOLDOWN_LLM is on. The 2026-05-25
# transcript showed a cool-down ask returning the identical canned block
# on every re-ask, ignoring HRV / pain / mobility. With the flag on, the
# dispatcher skips these so the message falls through to delegation /
# the composer / the reasoner. Default OFF preserves current behavior.
# See ARCHITECT_REVIEW_2026-05-24.md (A4).
_COOLDOWN_LLM_ROUTES = frozenset({"post_recovery", "pre_fuel"})


def cooldown_llm_enabled() -> bool:
    """When True, the canned cool-down / pre-fuel routes yield so the ask
    reaches the personalized LLM path instead of a static block."""
    return os.getenv("RAHAT_COOLDOWN_LLM", "0").lower().strip() in (
        "1", "true", "yes", "on")


# ─────────────────────── Route shape ───────────────────────
@dataclass(frozen=True)
class Route:
    """One entry in the ordered dispatch table.

    Attributes:
        name: Human-readable identifier (used in tests + decisions ledger).
        pattern: Compiled regex. If .search(msg) returns a match, this
            route fires.
        handler: Callable taking (msg, match) and returning a reply string.
            The match object is provided so handlers can extract captures
            (e.g. the numeric weight, the weekday name).
        agent: OPTIONAL owning-agent dimension (ADR-016, Seam 3). Default
            None == today's behavior: every route is implicitly Kobe's
            (the_scientist), as it is now. A SECOND agent can contribute
            routes by setting ``agent="<name>"`` so its routes coexist in
            the one ordered table without forking the dispatcher. Today
            this field is carried for observability/audit only — dispatch()
            does NOT branch on it (so unset-default changes no routing). The
            wiring point (per-agent handler resolution) is described in
            specs/ADR-016_platform_seams.md §"Seam 3 wiring".
    """
    name: str
    pattern: Pattern
    handler: Callable[[str, re.Match], str]
    agent: Optional[str] = None


# ─────────────────────── Route handlers ───────────────────────
# Each handler imports its dependencies lazily so this module stays
# importable without triggering the full agent stack. Handler signatures:
# (msg: str, match: re.Match) -> str
#
# Handlers are thin wrappers: their job is to extract the right args from
# `match` and call the existing kobe / fraser handler functions. The
# heavy logic lives in the agent modules.

_WEEKDAY_INDICES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _h_slash(msg: str, match: re.Match):
    """Slash dispatcher — delegates to kobe's _try_slash_command which
    knows the full /pace /today /next /week /plan /fix table.

    Why this lives in the dispatcher: slash commands were the FIRST
    routing bug we hit on 2026-05-17. Centralizing slash routing here
    means the bypass logic can't get lost again.

    Returns None for UNRECOGNIZED slash commands (e.g. "/pase" typo)
    so the caller falls through to the reasoner — the existing
    test_slash_command_unknown_falls_through_to_reasoner contract."""
    from agents.the_scientist import handler as _kobe
    return _kobe._try_slash_command(msg)  # None = unknown, fall through


def _h_gym_wod_on_day(msg: str, match: re.Match) -> str:
    """Gym WOD lookup for a specific weekday — reads parse_gym_plan
    directly, ignoring cadence. Production-bug fix 2026-05-18."""
    from agents.the_scientist import handler as _kobe
    weekday_token = match.group("weekday").lower()
    idx = _WEEKDAY_INDICES.get(weekday_token)
    if idx is None:
        return f"I don't recognize {weekday_token!r} as a weekday."
    return _kobe.handle_gym_wod_on(idx)


def _h_gym_wod_relative(msg: str, match: re.Match) -> str:
    """Gym WOD lookup for a RELATIVE day (tomorrow / yesterday). Resolves
    the token to a weekday index and calls the same handle_gym_wod_on as
    the named-weekday route, so "what's the WOD tomorrow" behaves like
    "what's the WOD for Tuesday".

    2026-05-23 (#41): named-day routes only matched explicit weekdays, so
    relative days fell through to Fraser. "today"/"tonight" are handled by
    Fraser (daily-driver design), so they are NOT routed here."""
    from datetime import datetime, timedelta
    from agents.the_scientist import handler as _kobe
    rel = match.group("rel").lower()
    # Typo-tolerant: tomorrow / tommorow / tomorow / tmrw / tmr → +1.
    if rel.startswith("tom") or rel in ("tmrw", "tmr"):
        offset = 1
    elif rel.startswith("yester"):
        offset = -1
    else:
        offset = 0
    idx = (datetime.now() + timedelta(days=offset)).weekday()
    return _kobe.handle_gym_wod_on(idx)


def _h_plan_mutation(msg: str, match: re.Match):
    """Deterministic plan-EDIT dispatch (pick days, mark unavailable, set
    a rest day, tolerate, swap, clear, replan). Delegates to Kobe's
    _try_plan_mutation, which does the precise per-intent routing and
    returns None when `msg` isn't actually a plan edit — in which case
    this route returns None and the caller falls through to the reasoner.

    2026-05-23 fix (#47): these handlers used to live only in the dead
    legacy router, so plan edits silently no-op'd in production. This is
    the LAST route in the table so it only claims messages no read route
    matched, and never steals a query a specific route should own."""
    from agents.the_scientist import handler as _kobe
    return _kobe._try_plan_mutation(msg)  # None → caller falls through


def _h_weight_log(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_weight(float(match.group(1)))


def _h_hrv_log(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_hrv(float(match.group(1)))


def _h_set_tier(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_set_tier(match.group(1))


def _h_pace(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_pace()


def _h_show_plan_this_week(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_show_plan(next_week=False)


def _h_show_plan_next_week(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_show_plan(next_week=True)


def _h_workout_today(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_workout_today()


def _h_current_weight(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_current_weight()


def _h_list_dislikes(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_list_dislikes()


def _h_breathing_715(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_breathing("7-15")


def _h_breathing_box(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_breathing("box")


def _h_pre_fuel(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_pre_fuel()


def _h_post_recovery(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_post_recovery()


def _h_weekly_remaining(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_weekly_remaining()


def _h_last_week(msg: str, match: re.Match) -> str:
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_last_week()


def _h_daily_breakdown(msg: str, match: re.Match) -> str:
    """'calories by the day' → deterministic per-day burn breakdown.
    Bug 2026-06-21: this fell to the reasoner, which has only the weekly
    total and (correctly) refused to invent a per-day split."""
    from agents.the_scientist import handler as _kobe
    return _kobe.handle_daily_burn_breakdown()


def _h_one_rm_set(msg: str, match: re.Match):
    """Natural-language 1RM set → the tested ``/profile set`` persist path.

    Bug 2026-06-14: "My back squat max is 120 kg" never reached
    ``/profile set`` (the classifier didn't recognize it and no NL handler
    existed), so the update silently no-op'd and the synth fabricated a
    confirmation ("now 102 ... up from 102", with a double lbs conversion
    "225 lbs (264.6 lbs)"). This route normalizes the NL forms and reuses
    ``handle_profile`` so the update actually persists and the confirmation
    is correct (single conversion, real stored value).

    The route pattern (_ONE_RM_SET_RE) is a permissive coarse gate; the
    STRICT patterns below decide whether this is genuinely a *set* intent.
    Returns None for non-sets (e.g. "I'll squat at 120 today") so the
    caller falls through to the reasoner — same contract as _h_slash /
    _h_plan_mutation.
    """
    from agents.the_scientist import handler as _kobe
    for pat in _ONE_RM_STRICT:
        m = pat.search(msg)
        if not m:
            continue
        lift = m.group("lift")
        num = float(m.group("num"))
        unit = (m.group("unit") or "kg").lower()
        if unit.startswith("lb") or unit.startswith("pound"):
            num = round(num / 2.2046, 1)  # imperial → kg, single conversion
        return _kobe.handle_profile(f"set {lift} {num:g}")
    return None


# ─────────────────────── Route patterns ───────────────────────
# ORDER MATTERS. First match wins. Place more-specific patterns first.

# Slash always wins.
_SLASH_RE = re.compile(r"^\s*/", re.I)

# Gym WOD for a specific weekday — MUST come before generic show-plan or
# workout-today. The weekday capture uses a single named group; alternatives
# all funnel into the same trailing weekday position. Python's stdlib `re`
# disallows duplicate named groups across | branches, hence the structure.
_WEEKDAY_ALT = (
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|"
    r"thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)
_GYM_WOD_DAY_RE = re.compile(
    r"\b(?:"
    # Bug 2026-06-14: accept apostrophe-less "whats" and the noun
    # "workout/session/programming" (not just "wod") before for/on <day>.
    r"what(?:'?s|\s+is)\s+(?:the\s+|today'?s\s+)?(?:wod|workout|session|programming)\s+(?:for|on)\s+"
    r"|gym\s+(?:workout|wod|session)\s+(?:for|on)\s+"
    r"|what'?s?\s+at\s+the\s+gym\s+(?:on|for)\s+"
    r")"
    r"(?P<weekday>" + _WEEKDAY_ALT + r")\b",
    re.I,
)
# Companion route for "show me Friday's workout" / "show me Saturday's gym"
# — the weekday appears BEFORE the workout/gym noun, so it needs its own
# regex. Same handler.
_SHOW_DAY_WORKOUT_RE = re.compile(
    r"\bshow\s+(?:me\s+)?(?P<weekday>" + _WEEKDAY_ALT + r")"
    r"(?:'?s)?\s+(?:workout|wod|session|gym)\b",
    re.I,
)
# Relative-day gym WOD lookup ("what's the WOD tomorrow", "gym wod
# yesterday"). Requires a WOD/gym anchor AND a relative-day token, so a
# bare "what's the WOD" (no day) still falls through to Fraser. NOTE:
# "today"/"tonight" are deliberately NOT here — "what's the WOD today" is
# Fraser's daily-driver design intent, not a schedule peek. The day token
# may be preceded by an optional "for"/"on".
_GYM_WOD_RELATIVE_RE = re.compile(
    r"\b(?:"
    r"what(?:'s|\s+is)\s+(?:the\s+|my\s+|today'?s\s+)?wod"
    r"|gym\s+(?:workout|wod|session)"
    r"|what'?s?\s+at\s+the\s+gym"
    r")"
    r"(?:\s+(?:for|on))?\s+(?P<rel>tom+or+ow|tmrw|tmr|yesterday)\b",
    re.I,
)
# Companion for the OTHER word order: "tomorrow's WOD", "what is tomorrow's
# workout", "tomorrows session" — relative day (possessive) BEFORE the gym
# noun. Bug 2026-06-21: "what is tomorrow's WOD" matched no deterministic
# route and fell to the reasoner, which answered inconsistently ("no WOD
# synced" / "Monday is a rest day"). Same handler as the relative route.
# "today"/"tonight" stay OUT (Fraser's daily-driver design intent).
_REL_DAY_WORKOUT_RE = re.compile(
    r"\b(?P<rel>tom+or+ow|tmrw|tmr|yesterday)(?:'?s)?\s+"
    r"(?:wod|workout|session|gym|programming)\b",
    re.I,
)

# Numeric logging — unambiguous mutators.
_WEIGHT_LOG_RE = re.compile(r"\b(?:weight|wt)[:\s]+(\d+\.?\d*)\b", re.I)
_HRV_LOG_RE = re.compile(r"\bhrv\s+(\d{2,3})\b", re.I)
_TIER_SET_RE = re.compile(
    r"\btier\s+(survival|re.?entry|baseline|performance|hammer|red|yellow|green)\b",
    re.I,
)

# 1RM set (mutation). Natural-language forms that must persist via the same
# path as `/profile set` — see _h_one_rm_set + bug 2026-06-14.
_LIFT_ALT = (
    # Olympic jerk variants FIRST so multi-word forms win over bare "clean"/
    # "jerk" (2026-06-16 bug: "my 1RM split jerk would be 65" fell through to
    # the reasoner, which FABRICATED "noted as 65 kg" without persisting).
    r"clean\s*(?:and|&)\s*jerk|clean\s*jerk|split\s*jerk|push\s*jerk|"
    r"back\s*squat|front\s*squat|squat\s*clean|power\s*clean|"
    r"bench\s*press|strict\s*press|push\s*press|shoulder\s*press|"
    r"overhead\s*press|deadlift|squat|bench|press|clean|snatch|jerk|ohp|row"
)
# Coarse gate for the route table (permissive). The handler re-parses with
# the STRICT patterns and returns None for false positives.
_ONE_RM_SET_RE = re.compile(
    r"\b(?:set\s+(?:my\s+)?|my\s+|(?:my\s+)?1\s*rm\s+(?:for\s+)?)?"
    r"(?:" + _LIFT_ALT + r")\b"
    r"[^.\d]{0,16}"
    r"(?:is\s+now|is|to|at|=|max|1\s*rm|pr|would\s+be|should\s+be)"
    r"[^.\d]{0,10}\d",
    re.I,
)
# STRICT patterns — only a genuine *set* intent matches. Ordered; first wins.
# Each requires an explicit set signal: "set ...", "1rm for ...", a
# max/1rm/pr qualifier, or "is now" — so "I'll squat at 120" does NOT match.
_SET_SIG = r"is\s+now|is|=|to|at|would\s+be|should\s+be"
_ONE_RM_STRICT = [
    re.compile(
        r"\bset\s+(?:my\s+)?(?P<lift>" + _LIFT_ALT + r")\s+(?:to|at|=)\s+"
        r"(?P<num>\d{1,3}(?:\.\d+)?)\s*(?P<unit>kgs?|lbs?|pounds?)?", re.I),
    # "(my) 1RM (for) <lift> is/=/to/at/would be <num>" — handles spaced
    # "1 RM" and "would be" (2026-06-16: the split-jerk false-save case).
    re.compile(
        r"\b(?:my\s+)?1\s*rm\s+(?:for\s+)?(?P<lift>" + _LIFT_ALT + r")\s+"
        r"(?:" + _SET_SIG + r")\s+"
        r"(?:now\s+)?(?P<num>\d{1,3}(?:\.\d+)?)\s*(?P<unit>kgs?|lbs?|pounds?)?", re.I),
    re.compile(
        r"\b(?:my\s+)?(?P<lift>" + _LIFT_ALT + r")\s+(?:1\s*rm|max|pr)\s+"
        r"(?:" + _SET_SIG + r")\s+"
        r"(?:now\s+)?(?P<num>\d{1,3}(?:\.\d+)?)\s*(?P<unit>kgs?|lbs?|pounds?)?", re.I),
    re.compile(
        r"\b(?:my\s+)?(?P<lift>" + _LIFT_ALT + r")\s+(?:is\s+now|would\s+be|should\s+be)\s+"
        r"(?P<num>\d{1,3}(?:\.\d+)?)\s*(?P<unit>kgs?|lbs?|pounds?)?", re.I),
]

# Status / pace checks.
_PACE_RE = re.compile(r"\b(?:pace|on\s+track|status|how\s+am\s+i\s+doing)\b", re.I)

# Plan views — explicit "this week" / "next week" / general "plan".
# Bug 2026-06-14: `what(?:'?s|...)` accepts apostrophe-less "whats".
_PLAN_NEXT_RE = re.compile(
    r"\b(?:what(?:'?s|\s+is)?\s+(?:my\s+|the\s+)?(?:plan|schedule)\s+for\s+next\s+week"
    r"|next\s+week(?:'s)?\s+(?:plan|schedule)"
    r"|(?:plan|schedule)\s+for\s+next\s+week"
    r"|which\s+days\s+(?:am|are|do|will)\s+i\s+(?:be\s+)?(?:work(?:ing)?\s+out)\s+next\s+week)\b",
    re.I,
)
_PLAN_THIS_RE = re.compile(
    r"\b(?:what(?:'?s|\s+is)?\s+(?:my\s+|the\s+)?(?:plan|schedule)(?:\s+for\s+(?:this\s+)?week)?"
    r"|this\s+week(?:'s)?\s+(?:plan|schedule)"
    r"|(?:plan|schedule)\s+for\s+(?:this\s+|the\s+)week"
    r"|which\s+days\s+(?:am|are|do|will)\s+i\s+(?:be\s+)?(?:work(?:ing)?\s+out)"
    r"|show\s+(?:me\s+)?(?:my\s+|the\s+)?plan"
    r"|^\s*plan\s*$)",
    re.I,
)

# Workout today (cadence — distinct from gym-WOD-day which goes to gym data).
_WORKOUT_TODAY_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)?\s+(?:my\s+|the\s+)?workout\s+today"
    r"|am\s+i\s+working\s+out\s+today"
    r"|what\s+(?:am\s+i\s+|do\s+i\s+)?do(?:ing)?\s+today)\b",
    re.I,
)

# Current weight + dislike list — read-only Kobe state.
_CURRENT_WEIGHT_RE = re.compile(
    r"\b(?:current\s+weight"
    r"|how\s+much\s+do\s+i\s+weigh"
    r"|weight\s+now"
    r"|what(?:'s|\s+is)?\s+my\s+weight)\b",
    re.I,
)
_LIST_DISLIKES_RE = re.compile(
    r"\b(?:what\s+are\s+my\s+dislikes"
    r"|list\s+(?:my\s+)?dislikes"
    r"|show\s+(?:me\s+)?(?:my\s+)?(?:dislikes|blacklist)"
    r"|what(?:'s|\s+is)?\s+(?:on\s+)?my\s+blacklist)\b",
    re.I,
)

# Coaching protocols. Box breath and 7/15 are different routines —
# split into separate routes so the right handler arg gets passed.
_BREATH_BOX_RE = re.compile(r"\bbox\s+breath(?:ing)?\b", re.I)
_BREATH_715_RE = re.compile(
    r"\b(?:7\s*/?\s*15\s+breathing|breathing\s+protocol|"
    r"how\s+do\s+i\s+breathe)\b", re.I,
)
_PRE_FUEL_RE = re.compile(
    r"\b(?:pre[-\s]?(?:workout|fuel|run)\s+(?:fuel|snack|meal|eat)"
    r"|what\s+(?:should\s+i\s+)?eat\s+before)\b", re.I,
)
_POST_RECOVERY_RE = re.compile(
    r"\b(?:cool[-\s]?down|post[-\s]?(?:workout|wod)\s+(?:routine|stretch)"
    r"|recovery\s+routine\s+after)\b", re.I,
)

# Weekly summary.
_WEEKLY_REMAIN_RE = re.compile(
    r"\b(?:remain(?:ing)?|left|how\s+(?:much|many))\b.*\b(?:week|kcal|cal|burn)\b",
    re.I,
)
_LAST_WEEK_RE = re.compile(
    r"\blast\s+week\b.*\b(?:burn|kcal|cal|workout|stat|summary|how)\b",
    re.I,
)
# Per-day burn breakdown — "calories by the day", "burn each day", "daily
# breakdown", "day by day". Requires the by-day phrasing (NOT a bare "per
# day", which is the weekly-remaining "≈ 206 kcal/day" sense). Bug
# 2026-06-21: this had no route and fell to the reasoner.
_DAILY_BREAKDOWN_RE = re.compile(
    r"\b(?:cal(?:orie)?s?|burn|kcal)\b.{0,25}\b(?:by\s+(?:the\s+)?day|"
    r"each\s+day|day[\s-]by[\s-]day)\b"
    r"|\b(?:by\s+(?:the\s+)?day|each\s+day|day[\s-]by[\s-]day)\b.{0,25}"
    r"\b(?:cal(?:orie)?s?|burn|kcal)\b"
    r"|\bdaily\s+(?:burn|calorie|kcal)?\s*breakdown\b"
    r"|\bbreakdown\b.{0,25}\bby\s+day\b",
    re.I,
)

# Plan EDITS (mutations). Coarse gate — the precise per-intent routing +
# weekday/question gating happens in handler._try_plan_mutation, which
# returns None (→ fall through to the reasoner) for anything that isn't
# actually a plan edit. This route is placed LAST so it only claims
# messages no read route matched. Over-matching here is safe.
_PLAN_MUTATION_RE = re.compile(
    r"\b(?:"
    r"pick|crossfit\s+on|cf\s+on|do\s+(?:crossfit|cf|wod)\s+on|"
    r"for\s+(?:crossfit|cf|wod|run|z2|zone\s*2|easy\s+run)|"
    r"can'?t|cannot|won'?t|skip|miss|busy|unavailable|out\s+(?:on|for)|no\s+gym|"
    r"rest|off\s+day|day\s+off|take\s+\w+\s+off|"
    r"replan|rebuild\s+plan|reset\s+plan|new\s+plan|reset\s+week|"
    r"clear\s+(?:prefs|preferences|overrides)|use\s+defaults|forget\s+my\s+(?:prefs|preferences|overrides)|"
    r"tolerate|prefer|instead\s+of|rather\s+than|in\s+place\s+of"
    r")\b",
    re.I,
)


# ─────────────────────── The ordered route table ───────────────────────
# Add new routes by appending. Place specific patterns FIRST. Tests in
# tests/test_dispatcher.py pin every entry. Tests in
# tests/regression_registry/ pin specific real-user phrasings.
ROUTES: list[Route] = [
    # 1. Slash — always wins.
    Route("slash", _SLASH_RE, _h_slash),

    # 2. Gym-WOD-day lookup — must beat generic plan/workout patterns.
    # Two routes share the same handler: phrasings where the weekday
    # comes AFTER the lead-in ("what is the WOD for Tuesday") and
    # phrasings where it comes BEFORE the noun ("show me Tuesday's
    # workout"). Both extract a named group called 'weekday'.
    Route("gym_wod_on_day", _GYM_WOD_DAY_RE, _h_gym_wod_on_day),
    Route("show_day_workout", _SHOW_DAY_WORKOUT_RE, _h_gym_wod_on_day),
    Route("gym_wod_relative", _GYM_WOD_RELATIVE_RE, _h_gym_wod_relative),
    Route("rel_day_workout", _REL_DAY_WORKOUT_RE, _h_gym_wod_relative),

    # 3. Numeric mutators — unambiguous.
    Route("weight_log", _WEIGHT_LOG_RE, _h_weight_log),
    Route("hrv_log", _HRV_LOG_RE, _h_hrv_log),
    Route("tier_set", _TIER_SET_RE, _h_set_tier),
    # 1RM set — NL "my back squat max is 120 kg" → /profile set persist
    # path. Coarse gate; handler returns None for non-set phrasings so they
    # fall through to the reasoner. Bug 2026-06-14.
    Route("one_rm_set", _ONE_RM_SET_RE, _h_one_rm_set),

    # 4. Plan views — explicit "next week" must beat "this week".
    Route("show_plan_next_week", _PLAN_NEXT_RE, _h_show_plan_next_week),
    Route("show_plan_this_week", _PLAN_THIS_RE, _h_show_plan_this_week),

    # 5. Workout today (cadence — not gym data).
    Route("workout_today", _WORKOUT_TODAY_RE, _h_workout_today),

    # 6. Status / state read-only.
    Route("pace", _PACE_RE, _h_pace),
    Route("current_weight", _CURRENT_WEIGHT_RE, _h_current_weight),
    Route("list_dislikes", _LIST_DISLIKES_RE, _h_list_dislikes),
    # Per-day breakdown must beat weekly_remaining (both mention burn/cal).
    Route("daily_breakdown", _DAILY_BREAKDOWN_RE, _h_daily_breakdown),
    Route("weekly_remaining", _WEEKLY_REMAIN_RE, _h_weekly_remaining),
    Route("last_week", _LAST_WEEK_RE, _h_last_week),

    # 7. Coaching protocols — box-breath FIRST, more specific than 7/15.
    Route("breathing_box", _BREATH_BOX_RE, _h_breathing_box),
    Route("breathing_715", _BREATH_715_RE, _h_breathing_715),
    Route("pre_fuel", _PRE_FUEL_RE, _h_pre_fuel),
    Route("post_recovery", _POST_RECOVERY_RE, _h_post_recovery),

    # 8. Plan EDITS — LAST. Only claims messages no read route matched;
    # _try_plan_mutation returns None for non-edits → fall through to the
    # reasoner. This is the deterministic catch that stops plan edits
    # ("Mon for crossfit", "Wed rest", "replan") from silently no-op'ing.
    Route("plan_mutation", _PLAN_MUTATION_RE, _h_plan_mutation),
]


# ─────────────────────── Public API ───────────────────────
def dispatch(msg: str) -> Optional[str]:
    """Try each route in order. Return the first handler's output, or None
    if no route matches.

    Returns None when:
        - The feature flag RAHAT_USE_DISPATCHER=0 is set
        - The message is empty / None
        - No route's regex matches
        - A matched handler raises (logged + None returned so caller can
          fall back gracefully)

    Callers must treat None as "I don't know" and fall through to the
    reasoner.
    """
    if not enabled():
        return None
    if not msg:
        return None

    yield_cooldown = cooldown_llm_enabled()
    for route in ROUTES:
        if yield_cooldown and route.name in _COOLDOWN_LLM_ROUTES:
            # Flag on: yield the canned cool-down / pre-fuel block so the
            # ask reaches the personalized LLM path instead.
            continue
        match = route.pattern.search(msg)
        if match is None:
            continue
        try:
            return route.handler(msg, match)
        except Exception as e:
            # Don't crash the bot on a handler exception — log and let the
            # caller fall through to the reasoner. The handler functions
            # should be defensive but this is the safety net.
            import sys
            print(
                f"[dispatcher] route {route.name!r} handler raised: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            return None
    return None


def list_routes() -> list[str]:
    """Return route names in order. Used by tests + decisions audit."""
    return [r.name for r in ROUTES]


def match_route(msg: str) -> Optional[str]:
    """Return the NAME of the first route that matches msg, without
    calling the handler. Used in tests + observability."""
    if not msg:
        return None
    for route in ROUTES:
        if route.pattern.search(msg):
            return route.name
    return None


__all__ = [
    "Route",
    "ROUTES",
    "cooldown_llm_enabled",
    "dispatch",
    "enabled",
    "list_routes",
    "match_route",
]
