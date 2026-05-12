"""protocols — pure-math + constants for the Scientist (and downstream agents).

Phase Now follow-up: extracted from `main.py` so future agents (Coach,
Curriculum, etc.) can import the kcal math, HRV bands, scheduler rules,
and weight-timeline arithmetic without pulling in the Telegram poll
loop, the Gemini client, or the DB connection management.

What's in here:
    • Athlete + system constants (BMR, kcal/lb, intent ETAs, tiers)
    • Pure helpers (week_bounds, hrv_band, fmt_kcal, parse_weekdays, …)
    • Gym-plan parsing (GymDay, parse_gym_plan, eligible_cf_days)
    • Locked-rate timeline math (_eta_at_locked_rate, _locked_intake)

What's NOT in here (stays in main.py):
    • Anything that reads or writes the SQLite ledger
    • Anything that talks to Telegram or Gemini
    • The intent router, the handlers, the tick functions

Importing rule for downstream agents:
    from agents.the_scientist.protocols import (
        TIERS, hrv_band, parse_gym_plan, ...,
    )

This module is import-safe everywhere — no side effects on import beyond
loading .env (which is idempotent).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────── Athlete constants (PRD §3 + §6) ───────────────────────────
BMR_KCAL          = int(os.getenv("BMR_KCAL", "2100"))
KCAL_PER_LB_FAT   = 3500
KCAL_PER_KG_FAT   = 7700
# Five-day cadence (3 CF + 1 Z2 + 3 active rest) generates ~5,150 kcal of
# *scheduled* burn. The remaining ~850 kcal/wk comes from daily NEAT.
WEEKLY_ACTIVE_TARGET_KCAL = int(os.getenv("WEEKLY_ACTIVE_KCAL", "6000"))
# Daily intake is derived from a LOCKED deficit at the sustainable rate
# below — kept consistent week to week so the user isn't chasing a
# moving goalpost. TDEE = BMR + active = 2100 + 857 ≈ 2957.
# At 0.75 lb/wk: deficit 375 → intake 2,582 → round to 2,600.
DAILY_INTAKE_KCAL = int(os.getenv("INTAKE_KCAL", "2600"))

# Scientist's own North Stars (PRD §6). Seeded into the shared `intents`
# table on boot, recomputed on every weight log via recalibrate_intents().
INTENT_INTERMEDIATE_KG  = 84.0   # comfort-weight checkpoint
INTENT_INTERMEDIATE_LBS = INTENT_INTERMEDIATE_KG * 2.20462    # 185.2 lbs
INTENT_TARGET_KG        = 80.0
INTENT_TARGET_LBS       = INTENT_TARGET_KG * 2.20462          # 176.4 lbs
INTENT_INTERMEDIATE_DATE = "2026-08-11"   # 84 kg ETA
INTENT_TARGET_DATE       = "2026-11-03"   # 80 kg ETA
TARGET_LBS               = float(os.getenv("TARGET_LBS", str(INTENT_TARGET_LBS)))

# Sustainable rate of fat loss for this athlete. 0.5–0.75 lb/wk preserves
# muscle and HRV; faster reliably triggers water retention, cortisol
# spikes, and a stalled scale. The Scientist refuses to compute deficits
# above the upper bound — instead it pushes the target date out and
# proposes a realistic intake band.
EASY_LOSS_LB_PER_WEEK   = 0.5
MAX_LOSS_LB_PER_WEEK    = 0.75
# The "locked" rate the Scientist plans against. Picking 0.75 means a
# consistent 375 kcal/day deficit and 2,600 kcal/day intake — no
# rate-shopping week to week.
LOCKED_LOSS_LB_PER_WEEK = 0.75

# Typical session burns (kcal). Used by split-target math.
TYPICAL_BURN: dict[str, int] = {
    "crossfit":      850,
    "crossfit_hiit": 550,
    "z2_10k":        1100,
    "z2_walk_60":    350,
    "incline_30":    250,
    "rest_passive":  500,
}

# Recovery tiers — what "the program" looks like in different life phases.
TIERS: dict[str, dict] = {
    "survival":    {"weekly": 3500, "daily": 500, "cap": 600,
                    "note": "Newborn/illness. Walks + family movement only."},
    "re_entry":    {"weekly": 4200, "daily": 600, "cap": 800,
                    "note": "Coming back. Z2 walks + light mobility."},
    "baseline":    {"weekly": 5500, "daily": 785, "cap": 1100,
                    "note": "Sustainable. 3 CF + 1 Z2 + walks."},
    "performance": {"weekly": 6000, "daily": 857, "cap": 1100,
                    "note": "Default. 3-4 CF + 1 Z2."},
    "hammer":      {"weekly": 6500, "daily": 928, "cap": 1450,
                    "note": "Pre-weigh-in push. 4 CF + 1-2 Z2."},
}
DEFAULT_TIER = "performance"

# HRV interpretation thresholds (ms).
HRV_RED    = 30
HRV_YELLOW = 45
HRV_GREEN  = 50
HRV_ELITE  = 70

# Movements that disqualify a day for the user's CrossFit slots.
BLACKLIST: list[str] = ["partner", "handstand", "muscle up", "muscle-up",
                        "overhead squat", "ohs", "rope climb"]
STRENGTH_BLACKLIST: list[str] = ["snatch"]

# SugarWOD section titles to skip when scanning for blacklisted movements.
# Optional / accessory work is something the user doesn't have time for,
# so blacklisted movements appearing only in those sections shouldn't
# disqualify the day. Substring match against the section title, case-
# insensitive. Add variants here as the gym's title conventions evolve.
SKIP_SECTION_TITLES: list[str] = ["optional", "accessor"]

# Soft blockers — disqualify a day by default, BUT are tolerated if the
# day's strength portion features a movement the user loves (see
# LOVED_STRENGTH_MOVEMENTS). Rationale: rowing on its own is enough to
# skip the day, but if today's strength is a heavy back squat, one
# rowing block in the WOD isn't worth missing the lift over.
SOFT_BLACKLIST: list[str] = ["rowing"]

# Movements the user loves enough that they override a soft blocker on
# the same day. Substring match against the strength block (so
# "back squat", "clean & jerk", "hang clean", "front squat", "deadlift"
# all qualify). Hard BLACKLIST / STRENGTH_BLACKLIST hits still block.
LOVED_STRENGTH_MOVEMENTS: list[str] = ["back squat", "clean", "front squat", "deadlift"]

# Tunables for the scheduler + nudges.
Z2_RUN_KCAL_DEFAULT   = int(os.getenv("Z2_RUN_KCAL", "400"))
NONWORKOUT_BURN_FLOOR = int(os.getenv("NONWORKOUT_FLOOR", "250"))
NUDGE_MORNING_HOUR    = 8
NUDGE_HOURLY_START    = 10
NUDGE_HOURLY_END      = 20
NUDGE_RECOVERY_HOUR   = 21
HAMMER_KCAL           = 1000
# A past CF/Z2 day with active burn below this threshold is treated as
# "no workout happened" for plan-recalibration purposes. Per user spec:
# at 700 kcal you're well below CF target (850) and Z2 target (1100),
# so the workout almost certainly didn't happen.
MISSED_WORKOUT_THRESHOLD_KCAL = int(
    os.getenv("MISSED_WORKOUT_THRESHOLD", "700"))

# Day-type per-day targets scale with the active tier.
#
# Tuning note (2026-05): user's actual CF burns sit closer to 1,150 than
# the prior 850, so the performance tier was bumped to 1150 to reflect
# real-world output. Plan total now lands at ~6,050 (3×1150 + 1×1100 +
# 3×500), slightly over the locked weekly_target of 6,000 — a small
# buffer rather than the previous ~850 kcal NEAT shortfall the user
# had to make up via daily walks. Hammer bumped proportionally so it
# stays above performance. Other tiers untouched (rare-use cases).
DAY_TYPE_BY_TIER: dict[str, dict[str, int]] = {
    "survival":    {"cf": 0,    "z2": 0,    "rest": 500},
    "re_entry":    {"cf": 600,  "z2": 800,  "rest": 500},
    "baseline":    {"cf": 800,  "z2": 1100, "rest": 500},
    "performance": {"cf": 1150, "z2": 1100, "rest": 500},
    "hammer":      {"cf": 1300, "z2": 1400, "rest": 600},
}
DAY_TYPE_LABEL = {"cf": "CrossFit", "z2": "Zone-2 10K", "rest": "Active rest"}
WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
                 "Fri": 4, "Sat": 5, "Sun": 6}
WEEKDAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
Z2_PREFERRED_WEEKDAY = 5  # Saturday


# ─────────────────────────── Time + math helpers ───────────────────────────
def week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Mon-start..Sun-end week bounds for the week containing `now`."""
    now = now or datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59)
    return monday, sunday


def hrv_band(value: float) -> tuple[str, str]:
    """Map an HRV value (ms) to a (band, recommendation) pair.

    Pure function — same input, same output, no side effects. Coach,
    Curriculum, or any future agent can import and reason against the
    same thresholds.
    """
    if value < HRV_RED:
        return ("RED",
                "Total rest. No high-intensity. 20 min 7/15 breathing tonight, "
                "extra magnesium, in bed by 10pm.")
    if value < HRV_YELLOW:
        return ("YELLOW",
                "Z2 only — nasal-breathing walk or easy run. Skip the WOD. "
                "10 min 7/15 breathing tonight.")
    if value < HRV_ELITE:
        return ("GREEN",
                "Normal training is on the table. Hit your planned session.")
    return ("ELITE",
            "Green light for a hammer day if you want it. Body is primed.")


def fmt_kcal(x: float) -> str:
    return f"{x:,.0f} kcal"


def fmt_lbs(x: float) -> str:
    return f"{x:.1f} lbs"


def _empty_prefs() -> dict:
    """Default per-week preferences row."""
    return {"unavailable_days": [], "forced_cf_days": [],
            "forced_z2_day": None, "tolerated_blacklist": []}


def _eta_at_locked_rate(lbs_to_lose: float,
                        now: datetime | None = None) -> datetime:
    """Date at which `lbs_to_lose` is achieved at the locked rate."""
    weeks = lbs_to_lose / LOCKED_LOSS_LB_PER_WEEK
    return (now or datetime.now()) + timedelta(weeks=weeks)


def _locked_intake() -> int:
    """Daily intake under the locked-deficit model. Consistent week to week."""
    deficit = LOCKED_LOSS_LB_PER_WEEK * KCAL_PER_LB_FAT / 7    # 375 kcal/day
    daily_active = WEEKLY_ACTIVE_TARGET_KCAL / 7               # 857 kcal/day
    tdee = BMR_KCAL + daily_active                             # 2,957
    return int(round((tdee - deficit) / 50) * 50)              # → 2,600


# ─────────────────────────── Weekday parsing ───────────────────────────
_WEEKDAY_LOOKUP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_WEEKDAY_TOKEN_RE = re.compile(
    r"\b(mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|today|tomorrow)\b", re.I)


def parse_weekdays(text: str, include_relative: bool = True,
                   *, now: datetime | None = None) -> list[int]:
    """Pull weekday indices (0=Mon..6=Sun) out of any prose. Generic — no
    day is hardcoded. 'today' and 'tomorrow' resolve relative to `now`
    (or datetime.now() if not given) unless include_relative=False.
    """
    found: list[int] = []
    today = (now or datetime.now()).weekday()
    for m in _WEEKDAY_TOKEN_RE.finditer(text):
        tok = m.group(0).lower()
        if tok == "today":
            if not include_relative:
                continue
            idx = today
        elif tok == "tomorrow":
            if not include_relative:
                continue
            idx = (today + 1) % 7
        else:
            idx = _WEEKDAY_LOOKUP.get(tok)
        if idx is not None and idx not in found:
            found.append(idx)
    return found


# ─────────────────────────── Blacklist normalization ───────────────────────────
_BLACKLIST_NORMALIZE = {
    "muscle ups": "muscle up", "muscleups": "muscle up", "muscle-ups": "muscle up",
    "muscle-up": "muscle up", "mu": "muscle up",
    "ohs": "overhead squat",
    "hspu": "handstand", "handstands": "handstand", "handstand push-up": "handstand",
    "partner wod": "partner", "partner workout": "partner",
}


def normalize_blacklist_term(term: str) -> str:
    t = term.lower().strip()
    return _BLACKLIST_NORMALIZE.get(t, t)


# ─────────────────────────── Gym plan parsing ───────────────────────────
DAY_HEADER = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d+\s*$",
                        re.IGNORECASE | re.MULTILINE)


@dataclass
class GymDay:
    label: str
    weekday: str
    body: str
    strength: str
    blockers: list[str]


def parse_gym_plan(text: str | None = None,
                   *, plan_path=None) -> list[GymDay]:
    """Parse a SugarWOD-style weekly plan into GymDay objects.

    `text` takes precedence; otherwise we read `plan_path`. If neither is
    available, returns []. Both forms are blacklist-aware so downstream
    agents (Coach especially) get the same eligibility view as the
    Scientist's auto-picker.
    """
    if text is None:
        if plan_path is None or not plan_path.exists():
            return []
        text = plan_path.read_text(errors="ignore")

    parts: list[tuple[str, str]] = []
    current_label, buf = None, []
    for line in text.splitlines():
        if DAY_HEADER.match(line.strip()):
            if current_label is not None:
                parts.append((current_label, "\n".join(buf)))
            current_label = line.strip()
            buf = []
        else:
            buf.append(line)
    if current_label is not None:
        parts.append((current_label, "\n".join(buf)))

    days: list[GymDay] = []
    for label, body in parts:
        # Normalize weekday to title case ('Mon', 'Tue', …) so consumers
        # can do `WEEKDAY_INDEX.get(d.weekday[:3])` without worrying about
        # the SugarWOD bookmarklet's header style. The bookmarklet writes
        # 'MON 11' / 'TUE 12' / etc. (uppercase), but WEEKDAY_INDEX keys
        # are title case — without this normalization, the lookup returns
        # None at every call site, eligible_wds ends up empty, and replan
        # silently falls back to the default cadence. The 2026-05-11
        # "every day has blacklisted movements" false-positive bug.
        weekday = label.split()[0].title()
        chunks = re.split(r"^0 results\s*$", body, flags=re.MULTILINE)
        strength = chunks[0] if chunks else body

        # Drop optional/accessory sections from the body BEFORE scanning
        # for blockers — the user doesn't do those, so movements that
        # only appear there shouldn't disqualify the day.
        blockable_body = _strip_skip_sections(chunks)
        body_lc = blockable_body.lower()
        strength_lc = strength.lower()

        # Loved-strength override: if today's strength features back squat,
        # clean, front squat, or deadlift, we tolerate soft blockers (e.g.,
        # rowing) so we don't skip a day worth showing up for.
        has_loved_strength = any(loved in strength_lc
                                 for loved in LOVED_STRENGTH_MOVEMENTS)

        blockers: list[str] = []

        # Hard blockers — always disqualify, regardless of strength.
        for term in BLACKLIST:
            if term in body_lc:
                blockers.append(term)
        for term in STRENGTH_BLACKLIST:
            if term in strength_lc:
                blockers.append(f"{term} (strength)")

        # Soft blockers — disqualify only when the strength isn't a loved
        # movement. Tag with "(soft)" so the override is visible in logs
        # / debug output.
        for term in SOFT_BLACKLIST:
            if term in body_lc:
                if has_loved_strength:
                    # Tolerated — loved-strength override active.
                    continue
                blockers.append(f"{term} (soft)")

        days.append(GymDay(label, weekday, body, strength, blockers))
    return days


def _strip_skip_sections(chunks: list[str]) -> str:
    """Return the body with 'Optional Accessories'-style sections removed.

    `chunks` is the body already split by SugarWOD's '0 results' separator
    (one chunk per workout block). Each chunk's title is the first non-
    empty content line; if that title matches any pattern in
    SKIP_SECTION_TITLES, the chunk is excluded from the scan body.

    Used by parse_gym_plan so movements that only appear in optional /
    accessory work don't trigger blacklist hits — the user doesn't have
    time for those sections.
    """
    kept: list[str] = []
    for chunk in chunks or []:
        title = ""
        for line in chunk.splitlines():
            s = line.strip()
            # Skip the lone '0' marker from SugarWOD's day-header preamble.
            if s and s != "0":
                title = s.lower()
                break
        if any(skip in title for skip in SKIP_SECTION_TITLES):
            continue
        kept.append(chunk)
    return "\n".join(kept)


def eligible_cf_days(days: list[GymDay] | None = None) -> list[GymDay]:
    """Filter a parsed plan down to CF-eligible days (no blacklist hits)."""
    return [d for d in (days or []) if not d.blockers]
