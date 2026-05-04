"""
The Scientist — Rahat agent: data-driven CrossFit + Z2 coach.

Design (L8 control-plane lens):
- Deterministic core does the math (burn lookups, weekly remaining, scheduler,
  gym-day filter, weight-loss timeline, HRV interpretation, weigh-in timing,
  split-target distribution, nudge thresholds). The LLM is only invoked for
  free-form coaching questions.
- Intent router dispatches the inbound Telegram message to typed handlers.
- Background tickers (9pm recovery check, hourly walk nudge) run on the same
  poll loop. Throttling state lives in the vault DB.
- Single source of truth for athlete constants, tiers, blacklists, and
  fueling/breathing protocols at top of file.

Data sources:
- vault/rahat.db
    raw_vitals(active_calories, weight)
    weekly_campaigns(target_active_calories, ...)
    hrv_log(value, ts)             — added by this agent
    weighin_log(weight_lbs, ts)    — added by this agent
    user_state(key, value)         — added by this agent (recovery_tier, etc.)
    workout_log(kind, kcal, ts)    — added by this agent (manual entries)
    nudge_log(kind, day)           — throttling
- workspace/gym-programming/weekly_plan.txt — gym's weekly schedule

Intent surface:
  burn lookups       "today" / "yesterday" / "last week"
  weekly math        "remaining" / "left this week"
  split target       "I have 2 workouts and 1 rest day, how many each"
  scheduler          "schedule" / "plan my week"
  gym filter         "filter" / "which days"
  weigh-in timing    "when should I weigh in"
  HRV log + read     "hrv 27" / "my hrv is 44"
  breathing          "7/15" / "box breathing" / "improve hrv"
  pre-workout fuel   "what should I eat before"
  post recovery      "cooldown" / "stretch"
  run vs CF          "should I run or crossfit"
  recovery tier      "tier survival" / "tier hammer"
  manual log         "burned 1463 today" / "wod 850" / "run 1100"
  weight timeline    "to 185 by oct 15" / "how long to 84 kg"
  weight anchor      "wt 195" / "weight 195"
  fallback           LLM coach with full context (no goal-nag on day burn)
"""
from __future__ import annotations  # 3.9-safe: defer PEP 604 / PEP 585 evaluation

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai

# ─────────────────────────── Config ───────────────────────────
load_dotenv()
API_KEY  = os.getenv("GEMINI_API_KEY")
TOKEN    = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
HOME     = Path.home()
DB_PATH  = HOME / "developer/agency/rahat/vault/rahat.db"
PLAN_PATH = HOME / "developer/agency/rahat/staging/workspace/gym-programming/weekly_plan.txt"

# ── Athlete constants (per Rahat PRD §3 Scientist + §6 Success Metrics) ─
BMR_KCAL          = int(os.getenv("BMR_KCAL", "2100"))      # he corrected this himself
DAILY_INTAKE_KCAL = int(os.getenv("INTAKE_KCAL", "2400"))   # the sustainable target
KCAL_PER_LB_FAT   = 3500
KCAL_PER_KG_FAT   = 7700

# Scientist's own North Star (PRD §6). Seeded into the shared `intents`
# table on boot and used by handle_weight_timeline() for the linear-decay
# math the Scientist owns per PRD §3. The 155 kg deadlift Intent belongs
# to Matt Fraser (PRD §3 Performance) and is seeded by that agent — not here.
INTENT_TARGET_KG    = 80.0
INTENT_TARGET_LBS   = INTENT_TARGET_KG * 2.20462    # 176.4 lbs
INTENT_TARGET_DATE  = "2026-07-01"
TARGET_LBS          = float(os.getenv("TARGET_LBS", str(INTENT_TARGET_LBS)))

# Typical session burns (kcal). Used by split-target math.
TYPICAL_BURN = {
    "crossfit":      850,    # PRVN strength + WOD
    "crossfit_hiit": 550,    # pure metcon, no strength
    "z2_10k":        1100,   # ~9-10 mi at conversational pace
    "z2_walk_60":    350,
    "incline_30":    250,    # 10% / 3.0 mph treadmill
    "rest_passive":  500,    # baseline NEAT on a no-workout day
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
HRV_RED    = 30   # below = total rest, recovery only
HRV_YELLOW = 45   # below = Z2 only, no high intensity
HRV_GREEN  = 50   # at/above = normal training
HRV_ELITE  = 70   # at/above = green light for hammer

# Movements that disqualify a day for the user's CrossFit slots.
BLACKLIST = ["partner", "handstand", "muscle up", "muscle-up",
             "overhead squat", "ohs"]
STRENGTH_BLACKLIST = ["snatch"]   # only flagged in the strength portion

# Tunables for the scheduler + nudges.
Z2_RUN_KCAL_DEFAULT   = int(os.getenv("Z2_RUN_KCAL", "400"))
NONWORKOUT_BURN_FLOOR = int(os.getenv("NONWORKOUT_FLOOR", "250"))
NUDGE_MORNING_HOUR    = 8       # daily briefing
NUDGE_HOURLY_START    = 10
NUDGE_HOURLY_END      = 20
NUDGE_RECOVERY_HOUR   = 21
HAMMER_KCAL           = 1000    # any single day above this counts as a "hammer"

# Locked weekly cadence: 3 PRVN CrossFit + 1 Zone-2 10K + 3 active-rest.
# Day-type per-day targets scale with the active tier so the same plan
# expresses both "baseline" weeks and "hammer" pre-weighin weeks.
DAY_TYPE_BY_TIER: dict[str, dict[str, int]] = {
    "survival":    {"cf": 0,    "z2": 0,    "rest": 500},
    "re_entry":    {"cf": 600,  "z2": 800,  "rest": 500},
    "baseline":    {"cf": 800,  "z2": 1100, "rest": 500},
    "performance": {"cf": 850,  "z2": 1100, "rest": 500},
    "hammer":      {"cf": 1100, "z2": 1400, "rest": 600},
}
DAY_TYPE_LABEL = {"cf": "CrossFit", "z2": "Zone-2 10K", "rest": "Active rest"}
WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
                 "Fri": 4, "Sat": 5, "Sun": 6}
WEEKDAY_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
Z2_PREFERRED_WEEKDAY = 5  # Saturday

client = genai.Client(api_key=API_KEY) if API_KEY else None


def _active_model() -> str:
    if not client:
        return "gemini-1.5-flash"
    try:
        flash = [m.name for m in client.models.list() if "flash" in m.name.lower()]
        return sorted(flash)[-1] if flash else "gemini-1.5-flash"
    except Exception:
        return "gemini-1.5-flash"


MODEL_ID = _active_model()


# ───────────────────────── DB helpers ─────────────────────────
def _db():
    """Open a connection to the Intent Ledger.

    Per PRD §4.1 the ledger is a State-Machine with four logical stores:
      • observations  — raw_vitals + hrv_log + weighin_log + workout_log
      • intents       — hard-coded North Stars (80 kg, 155 kg deadlift)
      • work_orders   — pre-existing table; agents publish requested actions
      • governance_log — Bajrangi audit trail (approved / modified / vetoed)
    The Scientist's own state (recovery_tier, weekly plan, nudge throttling)
    lives alongside but is not part of the cross-agent ledger surface.
    """
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS nudge_log ("
        " kind TEXT, sent_at DATETIME DEFAULT CURRENT_TIMESTAMP, day DATE);"
        "CREATE TABLE IF NOT EXISTS hrv_log ("
        " value REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weighin_log ("
        " weight_lbs REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS workout_log ("
        " kind TEXT, kcal REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS user_state ("
        " key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_plan ("
        " week_start DATE NOT NULL, weekday INTEGER NOT NULL,"
        " day_type TEXT NOT NULL, gym_label TEXT, target_kcal REAL NOT NULL,"
        " PRIMARY KEY (week_start, weekday));"
        # Per-week overrides — auto-expire at the Sunday reset since
        # week_start is the key. JSON-encoded array columns kept as TEXT
        # for SQLite friendliness.
        "CREATE TABLE IF NOT EXISTS week_preferences ("
        " week_start DATE PRIMARY KEY,"
        " unavailable_days TEXT,"        # e.g. "[3]" for Thursday
        " forced_cf_days TEXT,"          # e.g. "[0,1,4,6]" for Mon/Tue/Fri/Sun
        " forced_z2_day INTEGER,"        # single weekday index or NULL
        " tolerated_blacklist TEXT);"    # e.g. '["muscle-up"]'
        # PRD §4.1: Intents — hard-coded North Stars.
        "CREATE TABLE IF NOT EXISTS intents ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"               # 'weight_kg' | 'deadlift_kg' | ...
        " target_value REAL NOT NULL,"
        " target_date DATE,"
        " status TEXT DEFAULT 'active',"     # active | met | abandoned
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(kind, target_date));"
        # PRD §4.1: Governance log — Bajrangi's audit trail.
        "CREATE TABLE IF NOT EXISTS governance_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
        " actor TEXT NOT NULL,"              # 'bajrangi' | 'scientist' | ...
        " subject TEXT NOT NULL,"            # what got governed (kind of nudge etc.)
        " decision TEXT NOT NULL,"           # approved | modified | vetoed
        " reason TEXT);"
    )
    # Seed only the Scientist's own intent (weight). Other agents seed theirs.
    con.execute(
        "INSERT OR IGNORE INTO intents (kind, target_value, target_date) "
        "VALUES (?, ?, ?)", ("weight_kg", INTENT_TARGET_KG, INTENT_TARGET_DATE))
    con.commit()
    return con


def get_active_intent(kind: str) -> dict | None:
    con = _db()
    try:
        row = con.execute(
            "SELECT id, kind, target_value, target_date, status "
            "FROM intents WHERE kind=? AND status='active' "
            "ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        if not row:
            return None
        return dict(zip(("id", "kind", "target_value", "target_date", "status"), row))
    finally:
        con.close()


def check_external_veto(subject: str, since_hours: int = 24) -> tuple[bool, str | None]:
    """Read-only check for an active veto from another agent (e.g. Bajrangi).

    The Scientist consumes governance signals but never writes to
    governance_log itself — that table is owned by Bajrangi per PRD §3.
    Returns (vetoed, reason) where `subject` matches the nudge kind
    ('morning_brief', 'walk_nudge', 'recovery_nudge', or '*').
    """
    con = _db()
    try:
        row = con.execute(
            "SELECT actor, reason FROM governance_log "
            "WHERE decision='vetoed' AND (subject=? OR subject='*') "
            "AND ts >= datetime('now', ?) "
            "ORDER BY ts DESC LIMIT 1",
            (subject, f"-{since_hours} hours")).fetchone()
        if not row:
            return False, None
        actor, reason = row
        return True, f"{actor}: {reason}" if reason else actor
    finally:
        con.close()


def state_get(key: str, default: str | None = None) -> str | None:
    con = _db()
    try:
        cur = con.execute("SELECT value FROM user_state WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default
    finally:
        con.close()


def state_set(key: str, value: str) -> None:
    con = _db()
    try:
        con.execute(
            "INSERT INTO user_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))
        con.commit()
    finally:
        con.close()


def burn_for_date(target: datetime) -> float:
    """Sum active kcal for one calendar day. Includes manual workout_log entries."""
    con = _db()
    try:
        d = target.strftime("%Y-%m-%d")
        # raw_vitals (Watch ingest) + workout_log (manual sync)
        a = con.execute(
            "SELECT COALESCE(SUM(value),0) FROM raw_vitals "
            "WHERE metric_type='active_calories' AND substr(timestamp,1,10)=?",
            (d,)).fetchone()[0] or 0
        b = con.execute(
            "SELECT COALESCE(SUM(kcal),0) FROM workout_log "
            "WHERE substr(ts,1,10)=?", (d,)).fetchone()[0] or 0
        return float(a) + float(b)
    finally:
        con.close()


def burn_for_range(start: datetime, end: datetime) -> float:
    """Sum active kcal between two calendar days inclusive."""
    con = _db()
    try:
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        a = con.execute(
            "SELECT COALESCE(SUM(value),0) FROM raw_vitals "
            "WHERE metric_type='active_calories' "
            "AND substr(timestamp,1,10) BETWEEN ? AND ?", (s, e)).fetchone()[0] or 0
        b = con.execute(
            "SELECT COALESCE(SUM(kcal),0) FROM workout_log "
            "WHERE substr(ts,1,10) BETWEEN ? AND ?", (s, e)).fetchone()[0] or 0
        return float(a) + float(b)
    finally:
        con.close()


def week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Mon-start..Sun-end week bounds for the week containing `now`."""
    now = now or datetime.now()
    # weekday: Mon=0..Sun=6
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59)
    return monday, sunday


def burn_this_week() -> tuple[float, datetime]:
    """Sum kcal Mon..now. Returns (kcal, week_start_monday)."""
    monday, _ = week_bounds()
    return burn_for_range(monday, datetime.now()), monday


def burn_last_week() -> tuple[float, datetime, datetime]:
    last_mon = (datetime.now() - timedelta(days=datetime.now().weekday() + 7)
                ).replace(hour=0, minute=0, second=0, microsecond=0)
    last_sun = last_mon + timedelta(days=6)
    return burn_for_range(last_mon, last_sun), last_mon, last_sun


def weekly_target() -> float:
    """Plan-aware target. The locked weekly cadence is the source of truth;
    fall back to tier ceiling, then to the most recent campaign row."""
    try:
        t = sum(d["target_kcal"] for d in current_plan())
        if t > 0:
            return float(t)
    except Exception:
        pass
    tier = state_get("recovery_tier", DEFAULT_TIER)
    if tier in TIERS:
        return float(TIERS[tier]["weekly"])
    con = _db()
    try:
        row = con.execute(
            "SELECT target_active_calories FROM weekly_campaigns "
            "ORDER BY week_start DESC LIMIT 1").fetchone()
        return float(row[0]) if row else 6000.0
    finally:
        con.close()


# ─────────────────────── Weekly plan (3 CF + 1 Z2) ────────────
def day_type_target(day_type: str, tier: str | None = None) -> int:
    tier = tier or state_get("recovery_tier", DEFAULT_TIER)
    return DAY_TYPE_BY_TIER.get(tier, DAY_TYPE_BY_TIER[DEFAULT_TIER]).get(day_type, 0)


def replan_week(monday: datetime, *, force: bool = False) -> list[dict]:
    """Build (or rebuild) the 7-day plan for the week starting Monday.

    Default cadence: 3 PRVN CrossFit + 1 Zone-2 10K + 3 active-rest.
    CF days come from the gym schedule, filtered against the movement
    blacklist (partner / handstand / muscle-up / OHS / snatch-in-strength).
    Z2 prefers Saturday, falling back through the remaining days.

    Per-week prefs (from week_preferences) layer on top:
      • unavailable_days   — days the user can't make this week; dropped
        from CF + Z2 candidate pools.
      • tolerated_blacklist — movements the user accepts scaling for this
        week; lifts those terms from the blocker check.
      • forced_cf_days     — explicit CF day list overriding the auto-picker.
      • forced_z2_day      — explicit Z2 day index, or NULL.
    Anything removed dynamically (a day becoming unavailable mid-week)
    causes the auto-picker to re-iterate the candidate list and pick the
    next best day, so the cascade is automatic.
    """
    week_key = monday.strftime("%Y-%m-%d")
    con = _db()
    try:
        if not force:
            existing = con.execute(
                "SELECT 1 FROM weekly_plan WHERE week_start=? LIMIT 1",
                (week_key,)).fetchone()
            if existing:
                return [dict(zip(("weekday","day_type","gym_label","target_kcal"), r))
                        for r in con.execute(
                            "SELECT weekday, day_type, gym_label, target_kcal "
                            "FROM weekly_plan WHERE week_start=? ORDER BY weekday",
                            (week_key,)).fetchall()]

        prefs = get_prefs(monday)
        unavailable = set(prefs["unavailable_days"])
        tolerated = {normalize_blacklist_term(t) for t in prefs["tolerated_blacklist"]}

        gym_days = parse_gym_plan()

        def is_blocked(d: GymDay) -> bool:
            """Day is blocked iff at least one of its blockers is NOT tolerated.
            Strength-portion blockers are stored as 'snatch (strength)' — strip
            the suffix before matching against tolerated terms."""
            for b in d.blockers:
                core = b.split(" (")[0]
                if normalize_blacklist_term(core) not in tolerated:
                    return True
            return False

        # Map gym schedule by weekday for label lookups.
        gym_label_by_wd: dict[int, str] = {}
        eligible_wds: list[int] = []
        for d in gym_days:
            wd = WEEKDAY_INDEX.get(d.weekday[:3])
            if wd is None:
                continue
            gym_label_by_wd[wd] = d.label
            if not is_blocked(d) and wd not in unavailable:
                eligible_wds.append(wd)

        # CF picks: explicit > auto, with backfill so the count the user
        # originally chose is preserved when one of their picks gets pulled
        # by an unavailable day. Forced picks themselves bypass the
        # eligibility check (user is explicitly committing to scale them);
        # backfill draws strictly from the blacklist-respecting eligible
        # pool so we don't quietly route the user into a blocked movement.
        forced_orig = list(prefs["forced_cf_days"])
        forced_kept = [wd for wd in forced_orig if wd not in unavailable]
        target_count = len(forced_orig) if forced_orig else 3
        if forced_orig:
            cf_wds = list(forced_kept)
            for wd in eligible_wds:
                if len(cf_wds) >= target_count:
                    break
                if wd not in cf_wds:
                    cf_wds.append(wd)
        else:
            cf_wds = list(eligible_wds[:3])
        cf_picks = [(wd, gym_label_by_wd.get(wd)) for wd in cf_wds]
        cf_weekdays = {wd for wd, _ in cf_picks}
        backfilled = [wd for wd in cf_wds
                      if forced_orig and wd not in forced_orig]

        # Z2 day: explicit > auto. Auto fallback iterates Sat → Sun → Fri →
        # Thu → Wed → Tue → Mon, skipping anything already taken or
        # unavailable. None is acceptable (e.g., user picked 4 CF days).
        forced_z2 = prefs["forced_z2_day"]
        if forced_z2 is not None and forced_z2 not in cf_weekdays \
                and forced_z2 not in unavailable:
            z2_wd = forced_z2
        else:
            z2_wd = None
            for candidate in [Z2_PREFERRED_WEEKDAY, 6, 4, 3, 2, 1, 0]:
                if candidate in cf_weekdays or candidate in unavailable:
                    continue
                z2_wd = candidate
                break

        # Materialize 7 rows. Unavailable days stay as 'rest' with target 0
        # so the daily-pace nudge doesn't nag on a day the user blocked off.
        plan: list[dict] = []
        for wd in range(7):
            if wd in cf_weekdays:
                day_type = "cf"
                gym_label = next(lbl for w, lbl in cf_picks if w == wd)
            elif wd == z2_wd:
                day_type = "z2"
                gym_label = None
            else:
                day_type = "rest"
                gym_label = None
            target = 0 if wd in unavailable else day_type_target(day_type)
            plan.append({
                "weekday": wd,
                "day_type": day_type,
                "gym_label": gym_label,
                "target_kcal": target,
            })

        if force:
            con.execute("DELETE FROM weekly_plan WHERE week_start=?", (week_key,))
        for row in plan:
            con.execute(
                "INSERT OR REPLACE INTO weekly_plan "
                "(week_start, weekday, day_type, gym_label, target_kcal) "
                "VALUES (?, ?, ?, ?, ?)",
                (week_key, row["weekday"], row["day_type"],
                 row["gym_label"], row["target_kcal"]))
        con.commit()
        return plan
    finally:
        con.close()


def current_plan(monday: datetime | None = None) -> list[dict]:
    """7-row plan for the week containing `monday` (or now). Lazy-init."""
    if monday is None:
        monday, _ = week_bounds()
    return replan_week(monday, force=False)


def today_plan() -> dict:
    return current_plan()[datetime.now().weekday()]


# ───────────────────── Per-week preference overrides ──────────
# These let the user say "I can't make it on day X this week" or "pick days
# X/Y/Z for CF instead". They auto-expire at the Sunday reset because the
# row is keyed on week_start.
import json as _json  # local alias to avoid touching the import block

_WEEKDAY_LOOKUP = {
    "mon":0, "monday":0,
    "tue":1, "tues":1, "tuesday":1,
    "wed":2, "weds":2, "wednesday":2,
    "thu":3, "thur":3, "thurs":3, "thursday":3,
    "fri":4, "friday":4,
    "sat":5, "saturday":5,
    "sun":6, "sunday":6,
}
_WEEKDAY_TOKEN_RE = re.compile(
    r"\b(mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|today|tomorrow)\b", re.I)


def parse_weekdays(text: str) -> list[int]:
    """Pull weekday indices (0=Mon..6=Sun) out of any prose. Generic — no day
    is hardcoded. 'today' and 'tomorrow' resolve relative to now()."""
    found: list[int] = []
    today = datetime.now().weekday()
    for m in _WEEKDAY_TOKEN_RE.finditer(text):
        tok = m.group(0).lower()
        if tok == "today":
            idx = today
        elif tok == "tomorrow":
            idx = (today + 1) % 7
        else:
            idx = _WEEKDAY_LOOKUP.get(tok)
        if idx is not None and idx not in found:
            found.append(idx)
    return found


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


def _empty_prefs() -> dict:
    return {"unavailable_days": [], "forced_cf_days": [],
            "forced_z2_day": None, "tolerated_blacklist": []}


def get_prefs(monday: datetime) -> dict:
    week_key = monday.strftime("%Y-%m-%d")
    con = _db()
    try:
        row = con.execute(
            "SELECT unavailable_days, forced_cf_days, forced_z2_day, "
            "tolerated_blacklist FROM week_preferences WHERE week_start=?",
            (week_key,)).fetchone()
    finally:
        con.close()
    if not row:
        return _empty_prefs()
    return {
        "unavailable_days":    _json.loads(row[0]) if row[0] else [],
        "forced_cf_days":      _json.loads(row[1]) if row[1] else [],
        "forced_z2_day":       row[2],
        "tolerated_blacklist": _json.loads(row[3]) if row[3] else [],
    }


def set_prefs(monday: datetime, **updates) -> dict:
    """Merge updates into the week's pref row and return the merged result."""
    week_key = monday.strftime("%Y-%m-%d")
    cur = get_prefs(monday)
    cur.update(updates)
    con = _db()
    try:
        con.execute(
            "INSERT INTO week_preferences "
            "(week_start, unavailable_days, forced_cf_days, "
            " forced_z2_day, tolerated_blacklist) VALUES (?,?,?,?,?) "
            "ON CONFLICT(week_start) DO UPDATE SET "
            "unavailable_days=excluded.unavailable_days, "
            "forced_cf_days=excluded.forced_cf_days, "
            "forced_z2_day=excluded.forced_z2_day, "
            "tolerated_blacklist=excluded.tolerated_blacklist",
            (week_key,
             _json.dumps(cur["unavailable_days"]),
             _json.dumps(cur["forced_cf_days"]),
             cur["forced_z2_day"],
             _json.dumps(cur["tolerated_blacklist"])))
        con.commit()
    finally:
        con.close()
    return cur


def clear_prefs(monday: datetime) -> None:
    con = _db()
    try:
        con.execute("DELETE FROM week_preferences WHERE week_start=?",
                    (monday.strftime("%Y-%m-%d"),))
        con.commit()
    finally:
        con.close()


def latest_weight() -> float:
    con = _db()
    try:
        # Prefer the dedicated weighin_log; fall back to raw_vitals.
        row = con.execute(
            "SELECT weight_lbs FROM weighin_log ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            return float(row[0])
        row = con.execute(
            "SELECT value FROM raw_vitals WHERE metric_type='weight' "
            "ORDER BY timestamp DESC LIMIT 1").fetchone()
        return float(row[0]) if row else 198.0
    finally:
        con.close()


def sync_weight(val: float) -> None:
    con = _db()
    try:
        con.execute("INSERT INTO weighin_log (weight_lbs) VALUES (?)", (val,))
        con.commit()
    finally:
        con.close()


def log_hrv(val: float) -> None:
    con = _db()
    try:
        con.execute("INSERT INTO hrv_log (value) VALUES (?)", (val,))
        con.commit()
    finally:
        con.close()


def log_workout(kind: str, kcal: float) -> None:
    con = _db()
    try:
        con.execute("INSERT INTO workout_log (kind, kcal) VALUES (?, ?)",
                    (kind, kcal))
        con.commit()
    finally:
        con.close()


def last_hammer_day() -> datetime | None:
    """Most recent calendar date where the user burned ≥ HAMMER_KCAL."""
    con = _db()
    try:
        # check the last 14 days
        for i in range(14):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            a = con.execute(
                "SELECT COALESCE(SUM(value),0) FROM raw_vitals "
                "WHERE metric_type='active_calories' AND substr(timestamp,1,10)=?",
                (d,)).fetchone()[0] or 0
            b = con.execute(
                "SELECT COALESCE(SUM(kcal),0) FROM workout_log "
                "WHERE substr(ts,1,10)=?", (d,)).fetchone()[0] or 0
            if (float(a) + float(b)) >= HAMMER_KCAL:
                return datetime.strptime(d, "%Y-%m-%d")
        return None
    finally:
        con.close()


def nudge_already_sent(kind: str, day: str) -> bool:
    con = _db()
    try:
        return con.execute(
            "SELECT 1 FROM nudge_log WHERE kind=? AND day=? LIMIT 1",
            (kind, day)).fetchone() is not None
    finally:
        con.close()


def mark_nudge(kind: str, day: str) -> None:
    con = _db()
    try:
        con.execute("INSERT INTO nudge_log (kind, day) VALUES (?, ?)", (kind, day))
        con.commit()
    finally:
        con.close()


# ───────────────────────── Gym plan ──────────────────────────
DAY_HEADER = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d+\s*$",
                        re.IGNORECASE | re.MULTILINE)


@dataclass
class GymDay:
    label: str
    weekday: str
    body: str
    strength: str
    blockers: list[str]


def parse_gym_plan(text: str | None = None) -> list[GymDay]:
    if text is None:
        if not PLAN_PATH.exists():
            return []
        text = PLAN_PATH.read_text(errors="ignore")
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

    # SugarWOD's calendar uses "0 results" as a per-workout delimiter — the
    # first chunk in a day's body is always the strength piece, the rest are
    # primer / WOD / levels / accessories. This matches both the old verbose
    # weekly_plan.txt format and the new bridge output, so the strength-only
    # blacklist (snatch in the lift, not in a metcon) is reliable across
    # sources.
    days: list[GymDay] = []
    for label, body in parts:
        weekday = label.split()[0]
        chunks = re.split(r"^0 results\s*$", body, flags=re.MULTILINE)
        strength = chunks[0] if chunks else body
        blockers = []
        body_lc = body.lower()
        for term in BLACKLIST:
            if term in body_lc:
                blockers.append(term)
        for term in STRENGTH_BLACKLIST:
            if term in strength.lower():
                blockers.append(f"{term} (strength)")
        days.append(GymDay(label, weekday, body, strength, blockers))
    return days


def eligible_cf_days(days: list[GymDay] | None = None) -> list[GymDay]:
    days = days if days is not None else parse_gym_plan()
    return [d for d in days if not d.blockers]


# ───────────────────────── Format helpers ─────────────────────
def fmt_kcal(x: float) -> str:
    return f"{x:,.0f} kcal"


def fmt_lbs(x: float) -> str:
    return f"{x:.1f} lbs"


# ───────────────────────── Handlers ──────────────────────────
def handle_daily_burn(when: datetime) -> str:
    """Bare burn answer — explicitly NO goal-burn footer per user spec."""
    kcal = burn_for_date(when)
    label = when.strftime("%a %b %-d")
    if when.date() == datetime.now().date():
        return f"Today ({label}): *{fmt_kcal(kcal)}*."
    if when.date() == (datetime.now() - timedelta(days=1)).date():
        return f"Yesterday ({label}): *{fmt_kcal(kcal)}*."
    return f"{label}: *{fmt_kcal(kcal)}*."


def handle_weekly_remaining() -> str:
    burned, _ = burn_this_week()
    target = weekly_target()
    remaining = max(target - burned, 0.0)
    now = datetime.now()
    days_left = 7 - now.weekday()  # incl today, week ends Sun
    per_day = remaining / days_left if days_left else 0
    return (
        f"Week so far: *{fmt_kcal(burned)}* of {fmt_kcal(target)}.\n"
        f"Remaining: *{fmt_kcal(remaining)}* over {days_left} day(s) "
        f"≈ {fmt_kcal(per_day)}/day."
    )


def handle_last_week() -> str:
    kcal, mon, sun = burn_last_week()
    target = weekly_target()
    pct = (kcal / target * 100) if target else 0
    return (
        f"Last week ({mon.strftime('%b %-d')}–{sun.strftime('%b %-d')}): "
        f"*{fmt_kcal(kcal)}* — {pct:.0f}% of {fmt_kcal(target)} target."
    )


def handle_split_target(workouts: int, rests: int,
                        target_override: float | None = None) -> str:
    """Distribute remaining kcal across N workout + M rest days.

    Caps workout days at the active tier's per-session ceiling; assumes rest
    days hit ~500 kcal of NEAT. Returns the per-workout target needed.
    """
    burned, _ = burn_this_week()
    target = target_override if target_override is not None else weekly_target()
    remaining = max(target - burned, 0.0)
    rest_credit = rests * TYPICAL_BURN["rest_passive"]
    workout_total = max(remaining - rest_credit, 0.0)

    if workouts <= 0:
        return (f"Need {fmt_kcal(remaining)} more this week with no workout days. "
                f"Rest days alone won't get there — consider adding a session.")
    per_workout = workout_total / workouts

    tier = state_get("recovery_tier", DEFAULT_TIER)
    cap = TIERS.get(tier, TIERS[DEFAULT_TIER])["cap"]
    realistic = per_workout <= cap

    out = [
        f"*Plan to hit {fmt_kcal(target)} this week*",
        f"Burned so far: {fmt_kcal(burned)}. Remaining: {fmt_kcal(remaining)}.",
        f"Rest days ({rests}) ≈ {fmt_kcal(rest_credit)} of NEAT.",
        f"Per workout day ({workouts}): *{fmt_kcal(per_workout)}*.",
    ]
    if not realistic:
        # Suggest a more reachable target instead of a pep talk.
        reachable = workouts * cap + rest_credit + burned
        out.append(
            f"\n⚠️ {fmt_kcal(per_workout)}/session is above your tier cap "
            f"({fmt_kcal(cap)}). Realistic week-end total: ~{fmt_kcal(reachable)}. "
            "Add a Z2 walk after each WOD to bridge the gap, or accept the lower total."
        )
    return "\n".join(out)


def handle_weighin_when() -> str:
    """Recommend when to step on the scale based on the last hammer session."""
    last = last_hammer_day()
    now = datetime.now()
    if last is None:
        return ("No hammer day in the last 14 days — your inflammation should be "
                "fully cleared. Weigh in tomorrow morning, fasted, post-bathroom.")
    hours_since = (now - last).total_seconds() / 3600
    if hours_since < 36:
        when = last + timedelta(hours=48)
        return (
            f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
            f"Inflammation peaks at 24–36h. Wait until *{when.strftime('%a %b %-d')}* "
            "morning. Tonight: low sodium, 3L water, 7/15 breathing, dinner by 7pm."
        )
    if hours_since < 60:
        return (
            f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
            "Borderline — you're past inflammation peak but not fully flushed. "
            "Weigh tomorrow morning if you must; the 'whoosh' usually shows up "
            "12–24h later."
        )
    return (
        f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
        "You're in the truth window. Weigh tomorrow morning, fasted, post-bathroom."
    )


def hrv_band(value: float) -> tuple[str, str]:
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


def handle_hrv(value: float) -> str:
    log_hrv(value)
    band, advice = hrv_band(value)
    return (
        f"HRV logged: *{value:.0f} ms* — {band}.\n"
        f"{advice}"
    )


def handle_breathing(kind: str = "7-15") -> str:
    if "box" in kind:
        return (
            "*Box breathing* (4 cycles, ~5 min):\n"
            "• Inhale 4s through the nose\n"
            "• Hold 4s (relaxed, no strain)\n"
            "• Exhale 4s through pursed lips\n"
            "• Hold 4s\n"
            "Repeat. Lower jaw, soft shoulders. Good for pre-meeting reset."
        )
    return (
        "*7/15 breathing* (10 min, lying down, legs elevated if possible):\n"
        "• Inhale 7s through the nose — belly rises, chest still\n"
        "• Exhale 15s through pursed lips, slow and steady\n"
        "• No hold — passive. Long exhale = vagal brake = HRV up.\n"
        "Cycle for 10 min. Skip if pressure builds in head/ears; "
        "drop to 5s in / 10s out instead."
    )


def handle_pre_fuel(minutes_to_workout: int = 75) -> str:
    """Pre-workout fueling ranked by how friendly they are to HRV."""
    if minutes_to_workout < 30:
        return (
            "*Too close to workout for solid food.* "
            "Sip 200ml water + electrolytes. If lightheaded, half a date or "
            "1 tsp honey for fast glucose. Save the meal for after."
        )
    return (
        f"*Pre-workout fuel ({minutes_to_workout} min out)* — easy on the gut, "
        "fast to clear, won't tank HRV:\n"
        "1. 2–3 dates (nature's gel)\n"
        "2. Half a jowar roti + thin honey\n"
        "3. Slate Ultra (30g protein, low sugar) — your 'protein bridge'\n"
        "4. Small handful of grapes + pinch of salt\n"
        "Avoid: nuts, heavy fat, big fiber load, full protein bar (digests slow).\n"
        "Hydration: 500ml water with electrolytes between now and start."
    )


def handle_post_recovery() -> str:
    return (
        "*Post-WOD recovery (15 min total)*\n"
        "• 5 min slow walk until HR < 100 bpm\n"
        "• Pigeon pose 2 min/side — releases glutes\n"
        "• Couch stretch 2 min/side — hip flexors + quads\n"
        "• Calf stretch 1 min/side — Achilles, ankle mobility\n"
        "• Foam roller thoracic 2 min — fixes the hunch\n"
        "• 5 min 7/15 breathing — vagal brake, HRV bounce\n"
        "Then: 500ml water + electrolytes, protein within 60 min."
    )


def handle_decision_run_or_wod() -> str:
    """Run vs WOD when both are on the table — favor the run for fat loss."""
    burned, _ = burn_this_week()
    target = weekly_target()
    remaining = max(target - burned, 0.0)
    return (
        f"*Run vs WOD — for fat loss, run wins.*\n"
        f"• Z2 10K: ~{fmt_kcal(TYPICAL_BURN['z2_10k'])} burn, low cortisol, "
        "low inflammation, supports HRV.\n"
        f"• CrossFit WOD: ~{fmt_kcal(TYPICAL_BURN['crossfit'])} burn, "
        "high cortisol, 24–36h water retention, 'looks heavy' on Mon scale.\n"
        f"Week status: {fmt_kcal(burned)} / {fmt_kcal(target)} "
        f"(remaining {fmt_kcal(remaining)}).\n"
        "Pick the run unless you specifically need the strength stimulus today."
    )


def handle_set_tier(tier: str) -> str:
    tier = tier.lower().strip().replace("-", "_")
    if tier not in TIERS:
        opts = ", ".join(TIERS.keys())
        return f"Unknown tier '{tier}'. Pick: {opts}."
    state_set("recovery_tier", tier)
    t = TIERS[tier]
    return (
        f"✅ Tier set to *{tier}*.\n"
        f"Weekly: {fmt_kcal(t['weekly'])} | Daily: {fmt_kcal(t['daily'])} | "
        f"Per-session cap: {fmt_kcal(t['cap'])}\n"
        f"_{t['note']}_"
    )


def handle_manual_burn(kcal: float, kind: str = "manual") -> str:
    log_workout(kind, kcal)
    today = burn_for_date(datetime.now())
    return f"✅ Logged *{fmt_kcal(kcal)}* ({kind}). Today total: {fmt_kcal(today)}."


def handle_weight_timeline(target_lbs: float | None = None,
                           by_date: datetime | None = None) -> str:
    """How long to target weight, or required deficit by date."""
    current = latest_weight()
    target = target_lbs if target_lbs is not None else TARGET_LBS
    if current <= target:
        return (f"You're at {fmt_lbs(current)} ≤ target {fmt_lbs(target)}. "
                "Goal already met — set a new one with `target 180` or similar.")
    lbs_to_lose = current - target
    # Default plan: realistic 1 lb/week
    weeks_at_1lb = lbs_to_lose / 1.0
    eta_1lb = datetime.now() + timedelta(weeks=weeks_at_1lb)

    out = [
        f"*Weight timeline*",
        f"Now: {fmt_lbs(current)} → goal: {fmt_lbs(target)} "
        f"(lose {fmt_lbs(lbs_to_lose)}).",
        f"At 1 lb/week (sustainable): ~{weeks_at_1lb:.0f} weeks → "
        f"{eta_1lb.strftime('%b %-d, %Y')}.",
    ]
    if by_date is not None:
        weeks = max((by_date - datetime.now()).days / 7, 0.1)
        rate = lbs_to_lose / weeks
        # daily deficit needed = rate * 3500 / 7
        deficit_per_day = rate * KCAL_PER_LB_FAT / 7
        # active needed = deficit + (intake - BMR)
        active_per_week = deficit_per_day * 7 + (DAILY_INTAKE_KCAL - BMR_KCAL) * 7
        out.append(
            f"\nBy {by_date.strftime('%b %-d, %Y')} ({weeks:.1f} weeks): "
            f"need {rate:.2f} lbs/week → daily deficit "
            f"*{deficit_per_day:.0f} kcal* "
            f"(intake {DAILY_INTAKE_KCAL}, BMR {BMR_KCAL} → "
            f"active *{active_per_week:.0f}*/wk)."
        )
        if rate > 1.5:
            out.append(
                f"⚠️ {rate:.1f} lb/wk is aggressive. >1.5 risks muscle loss + HRV crash."
            )
    return "\n".join(out)


def handle_weight(val: float) -> str:
    sync_weight(val)
    return f"✅ Weight logged: {fmt_lbs(val)}. (Previous reading kept in history.)"


def handle_filter() -> str:
    days = parse_gym_plan()
    if not days:
        return "No gym plan found at workspace/gym-programming/weekly_plan.txt."
    lines = ["*Gym week — eligibility for your CrossFit slots:*"]
    for d in days:
        if d.blockers:
            lines.append(f"❌ {d.label} — skip ({', '.join(sorted(set(d.blockers)))})")
        else:
            lines.append(f"✅ {d.label} — eligible")
    return "\n".join(lines)


def handle_show_plan(next_week: bool = False) -> str:
    """Render the locked weekly cadence: 3 CF + 1 Z2 + 3 active-rest.
    Set `next_week=True` to render the upcoming Mon–Sun (uses gym schedule
    eligible days for this week — see note below if you want a different
    week's gym data)."""
    monday, _ = week_bounds()
    if next_week:
        monday = monday + timedelta(days=7)
    plan = current_plan(monday)
    target = sum(d["target_kcal"] for d in plan)
    today_idx = datetime.now().weekday()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    sun = monday + timedelta(days=6)

    header = "Next week" if next_week else "This week"
    lines = [
        f"*{header} — {monday.strftime('%b %-d')} – {sun.strftime('%b %-d')}*",
        f"Tier `{tier}`, target {fmt_kcal(target)}.",
        "",
    ]
    for row in plan:
        is_today = (not next_week) and row["weekday"] == today_idx
        marker = "▶" if is_today else " "
        name = WEEKDAY_NAME[row["weekday"]]
        kind = DAY_TYPE_LABEL[row["day_type"]]
        gym = f" ({row['gym_label']})" if row["gym_label"] else ""
        if not next_week:
            actual = burn_for_date(monday + timedelta(days=row["weekday"]))
            actual_s = f" — burned {fmt_kcal(actual)}" if actual > 0 else ""
        else:
            actual_s = ""
        lines.append(
            f"{marker} {name}: {kind}{gym} → ideal "
            f"{fmt_kcal(row['target_kcal'])}{actual_s}")
    if not next_week:
        burned, _ = burn_this_week()
        lines.append(f"\nWeek so far: *{fmt_kcal(burned)}* / {fmt_kcal(target)}.")
    return "\n".join(lines)


def handle_next_week_target() -> str:
    """Active-burn target for the upcoming week, derived from the locked plan.

    Distinct from the daily *intake* target (~2400 kcal) — this is the active
    burn the Scientist owns. Computed from 3 CF + 1 Z2 + 3 active-rest.
    """
    monday, _ = week_bounds()
    next_mon = monday + timedelta(days=7)
    next_sun = next_mon + timedelta(days=6)
    plan = current_plan(next_mon)
    target = sum(d["target_kcal"] for d in plan)
    cf_n = sum(1 for d in plan if d["day_type"] == "cf")
    z2_n = sum(1 for d in plan if d["day_type"] == "z2")
    rest_n = sum(1 for d in plan if d["day_type"] == "rest")
    weight = latest_weight()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    cf_kcal = day_type_target("cf", tier)
    z2_kcal = day_type_target("z2", tier)
    rest_kcal = day_type_target("rest", tier)
    return (
        f"*Next week target — {next_mon.strftime('%b %-d')} – {next_sun.strftime('%b %-d')}*\n"
        f"Active-burn target: *{fmt_kcal(target)}*\n"
        f"  • {cf_n} × CrossFit @ {fmt_kcal(cf_kcal)} = {fmt_kcal(cf_n*cf_kcal)}\n"
        f"  • {z2_n} × Zone-2 10K @ {fmt_kcal(z2_kcal)} = {fmt_kcal(z2_n*z2_kcal)}\n"
        f"  • {rest_n} × active rest @ {fmt_kcal(rest_kcal)} = "
        f"{fmt_kcal(rest_n*rest_kcal)}\n"
        f"Tier `{tier}`. Current weight {weight:.1f} lbs → 80 kg North Star.\n"
        f"Use `show plan next week` to see day-by-day."
    )


def handle_replan() -> str:
    """Force-rebuild this week's plan from the current gym schedule."""
    monday, _ = week_bounds()
    replan_week(monday, force=True)
    return "🔄 Plan rebuilt for this week.\n\n" + handle_show_plan()


def _which_monday(next_week: bool) -> tuple[datetime, str]:
    monday, _ = week_bounds()
    if next_week:
        monday = monday + timedelta(days=7)
    return monday, ("next week" if next_week else "this week")


def handle_unavailable(weekday_text: str, next_week: bool = False) -> str:
    """Mark one or more weekdays as unavailable; replan picks the next-best
    day automatically. Generic — works for any weekday, any week."""
    monday, label = _which_monday(next_week)
    indices = parse_weekdays(weekday_text)
    if not indices:
        return ("Couldn't find a weekday in that. Try: "
                "'I can't make Wednesday' or 'skip Thursday next week'.")
    prefs = get_prefs(monday)
    merged = sorted(set(prefs["unavailable_days"]) | set(indices))
    set_prefs(monday, unavailable_days=merged)
    replan_week(monday, force=True)
    names = ", ".join(WEEKDAY_NAME[i] for i in indices)
    return (f"✅ Marked {names} unavailable {label}. Replanned.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_pick_days(weekday_text: str, next_week: bool = False) -> str:
    """Explicit day picks. If the message mentions 'run'/'z2'/'zone 2', that
    set goes to Z2; otherwise everything goes to CF. Mixed phrasing splits
    into both buckets when both keywords appear."""
    monday, label = _which_monday(next_week)
    text_lc = weekday_text.lower()
    indices = parse_weekdays(weekday_text)
    if not indices:
        return "Couldn't find any weekdays in that."

    # Split into CF vs Z2 buckets based on phrasing context.
    z2_kw = re.search(r"\b(run|z2|zone\s*2|10k|zone-2|easy run)\b", text_lc)
    cf_kw = re.search(r"\b(crossfit|cf|wod|workout|lift|class)\b", text_lc)

    cf_picks: list[int] = []
    z2_pick: int | None = None
    if z2_kw and cf_kw:
        # Mixed phrasing: split at the z2 keyword. Days before → CF, days
        # at/after → Z2. Handles "Mon Tue Fri for CF, Sun for run".
        split_at = z2_kw.start()
        cf_part = parse_weekdays(weekday_text[:split_at])
        z2_part = parse_weekdays(weekday_text[split_at:])
        cf_picks = cf_part
        z2_pick = z2_part[0] if z2_part else None
    elif z2_kw:
        # All days are Z2 candidates — first one wins.
        z2_pick = indices[0]
    elif cf_kw:
        # User explicitly said "for crossfit/CF" → all listed days are CF.
        # No auto-split into Z2 even if they listed 4+ days; respect what
        # they said.
        cf_picks = indices
    else:
        # Ambiguous (no keyword). 4+ days → last is Z2 so the cadence still
        # comes out at 3 CF + 1 Z2; 3 or fewer → all CF.
        if len(indices) >= 4:
            cf_picks = indices[:-1]
            z2_pick = indices[-1]
        else:
            cf_picks = indices[:3]

    set_prefs(monday, forced_cf_days=cf_picks, forced_z2_day=z2_pick)
    replan_week(monday, force=True)

    parts = []
    if cf_picks:
        parts.append("CF: " + ", ".join(WEEKDAY_NAME[i] for i in cf_picks))
    if z2_pick is not None:
        parts.append(f"Z2: {WEEKDAY_NAME[z2_pick]}")
    return (f"✅ Locked picks for {label} → {' | '.join(parts)}.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_tolerate(term_text: str, next_week: bool = False) -> str:
    """Add blacklist terms to this week's tolerance list (e.g., 'I'll scale
    muscle-ups this week')."""
    monday, label = _which_monday(next_week)
    matches = re.findall(
        r"\b(muscle[\s-]?ups?|muscleups?|partner|handstand(?:s)?|hspu|"
        r"overhead\s+squats?|ohs|snatch(?:es)?)\b", term_text, re.I)
    if not matches:
        return ("Couldn't find a blacklist term to tolerate. "
                "Options: muscle-up, partner, handstand, OHS, snatch.")
    norm = sorted({normalize_blacklist_term(m) for m in matches})
    prefs = get_prefs(monday)
    merged = sorted(set(prefs["tolerated_blacklist"]) | set(norm))
    set_prefs(monday, tolerated_blacklist=merged)
    replan_week(monday, force=True)
    return (f"✅ Tolerating {', '.join(norm)} for {label}. Replanned.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_clear_prefs(next_week: bool = False) -> str:
    monday, label = _which_monday(next_week)
    clear_prefs(monday)
    replan_week(monday, force=True)
    return (f"✅ Cleared all overrides for {label}. Plan reverts to "
            "auto-picker.\n\n" + handle_show_plan(next_week=next_week))


def handle_pace() -> str:
    """Today's burn vs day-type ideal, plus week-to-date vs target."""
    plan_row = today_plan()
    ideal = plan_row["target_kcal"]
    actual = burn_for_date(datetime.now())
    delta = actual - ideal
    kind = DAY_TYPE_LABEL[plan_row["day_type"]]
    gym = f" ({plan_row['gym_label']})" if plan_row["gym_label"] else ""

    if ideal == 0:
        day_status = f"Today: {kind}{gym} — no target. Burned {fmt_kcal(actual)}."
    elif delta >= 0:
        day_status = (f"Today: {kind}{gym} — *{fmt_kcal(actual)}* / "
                      f"{fmt_kcal(ideal)} ✅ (+{fmt_kcal(delta)}).")
    else:
        pct = (actual / ideal * 100) if ideal else 0
        day_status = (f"Today: {kind}{gym} — *{fmt_kcal(actual)}* / "
                      f"{fmt_kcal(ideal)} ({pct:.0f}%, "
                      f"{fmt_kcal(-delta)} short).")

    burned, _ = burn_this_week()
    target = weekly_target()
    pct_w = (burned / target * 100) if target else 0
    week_status = f"Week: *{fmt_kcal(burned)}* / {fmt_kcal(target)} ({pct_w:.0f}%)."
    return day_status + "\n" + week_status


def handle_today_target() -> str:
    row = today_plan()
    kind = DAY_TYPE_LABEL[row["day_type"]]
    gym = f" ({row['gym_label']})" if row["gym_label"] else ""
    return (f"Today is *{kind}*{gym}. Ideal active burn: "
            f"*{fmt_kcal(row['target_kcal'])}*.")


# Keep `handle_schedule` as an alias for back-compat with old chat shortcuts.
handle_schedule = handle_show_plan


# ───────────────────────── Intent router ──────────────────────
WEIGHT_RE   = re.compile(r"\b(?:weight|wt)[:\s]+(\d+\.?\d*)", re.I)
TODAY_RE    = re.compile(r"\b(today|now)\b", re.I)
YEST_RE     = re.compile(r"\byesterday\b", re.I)
LASTWK_RE   = re.compile(r"\blast\s+week\b", re.I)
# "calories this week" / "burn this week" / "how many ... this week" — bare lookup
THIS_WEEK_RE = re.compile(
    r"\b(?:(?:calories?|cal|kcal|active|burn(?:ed|t)?).*\bthis\s+week|"
    r"this\s+week.*(?:calories?|cal|kcal|active|burn(?:ed|t)?)|"
    r"how\s+many.*this\s+week)\b", re.I)
# "caloric target for next week" / "target next week" / "weekly target"
NEXT_WEEK_TARGET_RE = re.compile(
    r"\b(?:next\s+week.*(?:target|goal|burn|calories?|kcal)|"
    r"(?:target|goal|burn|calories?|kcal).*next\s+week|"
    r"calorie?ic?\s+target|weekly\s+target|"
    r"target\s+for\s+(?:the\s+)?(?:week|next\s+week))\b", re.I)
# "which days should I crossfit/rest/run zone 2" — scheduling question
PLAN_DAYS_RE = re.compile(
    r"\b(?:which\s+days?|what\s+days?|days?\s+should\s+I|"
    r"when\s+should\s+I)\b.*"
    r"\b(?:crossfit|cf|wod|run|rest|recover|zone\s*2|z2|workout)\b", re.I)
NEXT_WEEK_RE = re.compile(r"\bnext\s+week\b", re.I)
# Note: trailing \b dropped from REMAIN so "remaining" / "remainder" both match.
REMAIN_RE   = re.compile(r"\b(remain|left for the week|rest of (the )?week|week.*(target|goal))", re.I)
SCHED_RE    = re.compile(r"\b(schedule|structure|plan (my )?(week|workout)|show plan|this week.*plan|3 ?cross|crossfit.*(week|plan))\b", re.I)
FILTER_RE   = re.compile(r"\b(filter|which days|eligible|skip|partner|handstand|muscle ?up|overhead squat|snatch in)\b", re.I)
REPLAN_RE   = re.compile(r"\b(replan|rebuild plan|reset plan|new plan)\b", re.I)
PACE_RE     = re.compile(r"\b(pace|on track|how am I doing|status)\b", re.I)
# Per-week prefs. UNAVAILABLE_RE catches "can't make X" / "skip X" / "out X".
UNAVAILABLE_RE = re.compile(
    r"\b(can(?:'?t| ?not)|cannot|won'?t|skip(?:ping)?|miss(?:ing)?|"
    r"out\s+(?:on|for)|busy|unavailable|no\s+gym)\b", re.I)
# PICK_RE catches "pick X Y Z for crossfit" / "do CF on X Y Z" / "I'll do X Y Z".
PICK_RE = re.compile(
    r"\b(pick|do (?:crossfit|cf|wod) on|crossfit on|cf on|"
    r"i(?:'?ll| will| want to) do.*?(?:crossfit|cf|wod|run|z2|zone\s*2))\b", re.I)
TOLERATE_RE = re.compile(
    r"\b(scale|tolerate|fine\s+with|ok(?:ay)?\s+with|ignore|allow|"
    r"let me do|i can do|i'?ll do|i can scale|happy to (?:scale|do)|"
    r"i'?m fine|will do)\b"
    r"[\s\S]*?\b(muscle[\s-]?ups?|muscleups?|partner|handstand(?:s)?|hspu|"
    r"overhead\s+squats?|ohs|snatch(?:es)?)\b", re.I)
CLEAR_PREFS_RE = re.compile(
    r"\b(clear (?:prefs|preferences|overrides)|reset (?:prefs|preferences|week)|"
    r"use defaults|forget my (?:prefs|preferences|overrides))\b", re.I)
# Broader: "daily target", "target for today", "today's goal/ideal"
TODAY_TARGET_RE = re.compile(
    r"\b(today.?s?\s+(?:target|ideal|goal)|target\s+for\s+today|"
    r"daily\s+(?:target|ideal|goal)|ideal\s+today|day\s+target)\b", re.I)
# Word-number support so "one rest day" parses too.
_NUMS = r"(\d+|one|two|three|four|five|six|seven)"
_WORD_NUM = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7}
def _n(s: str) -> int:
    return _WORD_NUM[s.lower()] if s.lower() in _WORD_NUM else int(s)
# "N workouts ... M rest days" or "M rest days ... N workouts"
SPLIT_RE    = re.compile(
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?|days?\s+of\s+workout)"
    rf"[\s\S]{{0,80}}?{_NUMS}\s*(?:rest|recovery|off)\s*days?", re.I)
SPLIT_RE2   = re.compile(
    rf"{_NUMS}\s*(?:rest|recovery|off)\s*days?[\s\S]{{0,80}}?"
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?)", re.I)
# "I have 2 more workout days" (no rest specified → derive from days remaining in week)
WORKOUTS_ONLY_RE = re.compile(
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?)\b", re.I)
WEIGHIN_RE  = re.compile(r"\b(when.*weigh|weigh.?in.*(when|day|time)|should I weigh)\b", re.I)
HRV_RE      = re.compile(r"\bhrv\b[^0-9]{0,15}(\d{2,3})\b", re.I)
HRV_LOW_RE  = re.compile(r"\bhrv\b.*\b(low|tank|down|crash|drop)\b", re.I)
BREATH715_RE = re.compile(r"\b(7[/\-]15|7\s*15|breath(e|ing).*hrv|improve.*hrv|reset.*hrv)\b", re.I)
BOXBREATH_RE = re.compile(r"\bbox\s*breath", re.I)
PREFUEL_RE  = re.compile(r"\b(eat|fuel|snack).*(before|pre.?workout|pre.?run)|"
                         r"\b(pre.?workout|pre.?run)\b.*\b(eat|fuel|snack)", re.I)
COOLDOWN_RE = re.compile(r"\b(cool.?down|cooldown|stretch|recovery routine|"
                         r"post.?workout|post.?wod)\b", re.I)
# Only fires for explicit A-vs-B comparisons. Plain "should I run" no longer
# triggers it (Bug 3 from 05/03 testing — false-positive on scheduling questions).
DECIDE_RE   = re.compile(r"\b(run(?:ning)?\s+(?:or|vs|versus)\s+(?:crossfit|wod|workout)|"
                         r"(?:crossfit|wod)\s+(?:or|vs|versus)\s+run(?:ning)?|"
                         r"should I (?:run\s+or\s+do\s+(?:crossfit|wod)|"
                         r"do\s+(?:crossfit|wod)\s+or\s+run))\b", re.I)
TIER_RE     = re.compile(r"\btier\s+(survival|re.?entry|baseline|performance|hammer)\b", re.I)
LOG_BURN_RE = re.compile(r"\b(burn(?:ed|t)?|did)\s+(\d{2,4})\s*(?:cal|kcal|calories|active)?\b", re.I)
LOG_KIND_RE = re.compile(r"\b(crossfit|cf|wod|run|10k|z2|walk|bike|row)\s+(\d{2,4})\b", re.I)
TIMELINE_RE = re.compile(r"\b(to|target|reach|hit)\s+(\d{2,3})\s*(lbs?|kg|pounds?)?\b.*"
                         r"\bby\s+([a-z]+\s+\d+|\d+/\d+|\d+-\d+)", re.I)
TIMELINE_SHORT_RE = re.compile(r"\b(timeline|how long).*\b(\d{2,3})\s*(lbs?|kg)?", re.I)


def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    fmts = ["%b %d", "%B %d", "%b %d %Y", "%B %d %Y",
            "%m/%d", "%m/%d/%Y", "%m-%d", "%m-%d-%Y"]
    for f in fmts:
        try:
            d = datetime.strptime(s, f)
            if d.year == 1900:
                d = d.replace(year=datetime.now().year)
                if d < datetime.now():
                    d = d.replace(year=d.year + 1)
            return d
        except ValueError:
            continue
    return None


def route(msg: str) -> str:
    m = msg.strip()

    # --- mutators first (set state, log data) ---
    if (mw := WEIGHT_RE.search(m)):
        return handle_weight(float(mw.group(1)))
    if (mh := HRV_RE.search(m)):
        return handle_hrv(float(mh.group(1)))
    if (mt := TIER_RE.search(m)):
        return handle_set_tier(mt.group(1))
    if (mk := LOG_KIND_RE.search(m)):
        return handle_manual_burn(float(mk.group(2)), kind=mk.group(1).lower())
    if (mb := LOG_BURN_RE.search(m)) and TODAY_RE.search(m):
        return handle_manual_burn(float(mb.group(2)), kind="today")

    # --- specific lookups (must beat generic TODAY/REMAIN matches) ---
    # B4 fix: "daily target for today" → today's plan target, not burn-so-far.
    if TODAY_TARGET_RE.search(m):
        return handle_today_target()
    # B1 fix: "caloric target for next week" → plan-based active-burn target.
    if NEXT_WEEK_TARGET_RE.search(m):
        return handle_next_week_target()
    # B2 fix: "calories this week" → bare weekly burn vs target.
    if THIS_WEEK_RE.search(m):
        return handle_weekly_remaining()
    # B3 fix: "which days should I crossfit/rest/run zone 2 next week" → plan view.
    if PLAN_DAYS_RE.search(m):
        return handle_show_plan(next_week=bool(NEXT_WEEK_RE.search(m)))

    # --- decisions / advice ---
    if WEIGHIN_RE.search(m):
        return handle_weighin_when()
    if HRV_LOW_RE.search(m):
        return handle_breathing("7-15") + "\n\n" + handle_post_recovery()
    if BREATH715_RE.search(m):
        return handle_breathing("7-15")
    if BOXBREATH_RE.search(m):
        return handle_breathing("box")
    if PREFUEL_RE.search(m):
        return handle_pre_fuel()
    if COOLDOWN_RE.search(m):
        return handle_post_recovery()
    if DECIDE_RE.search(m):
        return handle_decision_run_or_wod()

    # --- weight timeline ---
    if (mtl := TIMELINE_RE.search(m)):
        target = float(mtl.group(2))
        unit = (mtl.group(3) or "lbs").lower()
        if "kg" in unit:
            target = target * 2.20462
        when = _parse_date(mtl.group(4))
        return handle_weight_timeline(target, when)
    if (mts := TIMELINE_SHORT_RE.search(m)):
        target = float(mts.group(2))
        unit = (mts.group(3) or "lbs").lower()
        if "kg" in unit:
            target = target * 2.20462
        return handle_weight_timeline(target)

    # --- split-target math (must run before REMAIN, since "...left" matches REMAIN too) ---
    # Optional override: "to hit 6500" / "for 6000" inside the same message.
    target_override = None
    if (mt := re.search(r"\b(?:hit|reach|target|for|to)\s+(\d{4})\b", m, re.I)):
        target_override = float(mt.group(1))
    if (ms := SPLIT_RE.search(m)):
        return handle_split_target(_n(ms.group(1)), _n(ms.group(2)), target_override)
    if (ms := SPLIT_RE2.search(m)):
        return handle_split_target(_n(ms.group(2)), _n(ms.group(1)), target_override)
    if WORKOUTS_ONLY_RE.search(m) and re.search(r"\b(hit|reach|target|goal|6\d{3}|5\d{3})\b", m, re.I):
        n = _n(WORKOUTS_ONLY_RE.search(m).group(1))
        # Days remaining in the week (Mon..Sun) minus the workouts = rest days
        days_left = 7 - datetime.now().weekday()
        rests = max(days_left - n, 0)
        return handle_split_target(n, rests, target_override)

    # --- standard lookups ---
    if YEST_RE.search(m):
        return handle_daily_burn(datetime.now() - timedelta(days=1))
    if LASTWK_RE.search(m):
        return handle_last_week()
    if REMAIN_RE.search(m):
        return handle_weekly_remaining()
    if TODAY_RE.search(m):
        return handle_daily_burn(datetime.now())
    # Per-week pref mutators — must run before REPLAN/SCHED so messages like
    # "skip Wednesday next week, replan" hit the override handler first.
    next_week_q = bool(NEXT_WEEK_RE.search(m))
    if CLEAR_PREFS_RE.search(m):
        return handle_clear_prefs(next_week=next_week_q)
    if TOLERATE_RE.search(m):
        return handle_tolerate(m, next_week=next_week_q)
    if UNAVAILABLE_RE.search(m) and parse_weekdays(m):
        return handle_unavailable(m, next_week=next_week_q)
    if PICK_RE.search(m) and parse_weekdays(m):
        return handle_pick_days(m, next_week=next_week_q)

    if REPLAN_RE.search(m):
        return handle_replan()
    if SCHED_RE.search(m):
        return handle_show_plan()
    if PACE_RE.search(m):
        return handle_pace()
    if TODAY_TARGET_RE.search(m):
        return handle_today_target()
    if FILTER_RE.search(m):
        return handle_filter()

    return llm_coach(msg)


def llm_coach(msg: str) -> str:
    if not client:
        return ("LLM not configured. Try: 'today', 'yesterday', 'last week', "
                "'remaining', 'schedule', 'filter', 'when should I weigh in', "
                "'hrv 45', 'pre-workout fuel', 'cooldown', '7/15 breathing', "
                "'tier survival', 'wod 850', 'wt 195'.")
    burned, _ = burn_this_week()
    target = weekly_target()
    remaining = max(target - burned, 0.0)
    days_left = 7 - datetime.now().weekday()
    weight = latest_weight()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    elig = ", ".join(d.label for d in eligible_cf_days()) or "none"

    prompt = (
        f"Athlete: Venkat (6'1\"). Weight {weight:.1f} lbs. Goal {TARGET_LBS} lbs.\n"
        f"Tier: {tier}. BMR {BMR_KCAL}, intake target {DAILY_INTAKE_KCAL} kcal.\n"
        f"Week burn: {burned:.0f} / {target:.0f} kcal "
        f"(remaining {remaining:.0f} over {days_left} days).\n"
        f"Eligible CF days (no partner/handstand/muscle-up/OHS/snatch-in-strength): {elig}.\n"
        f"User message: {msg}\n"
        "Rules: data-driven CrossFit + Z2 coach. Lbs only. ≤6 lines.\n"
        "If user only asked about a single day's burn, do NOT mention weekly target.\n"
        "Use 7/15 breathing for HRV recovery. Bias toward run for fat loss.\n"
        "If HRV likely low (back-to-back hammer days, sick, sleep-deprived), "
        "scale back rather than push."
    )
    try:
        res = client.models.generate_content(model=MODEL_ID, contents=prompt)
        return res.text
    except Exception as e:
        return f"❌ LLM error: {e}"


# ─────────────────────────── Nudges ───────────────────────────
def daily_target() -> float:
    """Today's ideal active burn — driven by the locked weekly plan."""
    try:
        return float(today_plan()["target_kcal"])
    except Exception:
        tier = state_get("recovery_tier", DEFAULT_TIER)
        return float(TIERS.get(tier, TIERS[DEFAULT_TIER])["daily"])


def latest_hrv() -> float | None:
    """Most recent logged HRV value (any source). Returns None if nothing logged."""
    con = _db()
    try:
        row = con.execute(
            "SELECT value FROM hrv_log ORDER BY ts DESC LIMIT 1").fetchone()
        return float(row[0]) if row else None
    finally:
        con.close()


def _internal_safety_downgrade() -> tuple[bool, str | None]:
    """Scientist-internal safety guard.

    When the Scientist's own HRV reading is in the RED band, it downgrades
    its performance messaging to recovery messaging on its own — this is
    normal coaching, not governance. A real governance veto from Bajrangi
    (when that agent ships) takes precedence and is checked separately.
    """
    hrv = latest_hrv()
    if hrv is None or hrv >= HRV_RED:
        return False, None
    return True, f"HRV {hrv:.0f} ms in RED band — downgrading to recovery"


def maybe_morning_briefing() -> str | None:
    """At 08:00, send the day's plan: type, gym pick, target, week so far.
    Suppressed if another agent has posted a veto for this nudge in
    governance_log; downgraded to recovery messaging if the Scientist's own
    HRV check is in the RED band."""
    now = datetime.now()
    if now.hour != NUDGE_MORNING_HOUR:
        return None
    today = now.strftime("%Y-%m-%d")
    if nudge_already_sent("morning_brief", today):
        return None
    row = today_plan()
    burned, _ = burn_this_week()
    target = weekly_target()
    kind = DAY_TYPE_LABEL[row["day_type"]]
    gym = f" ({row['gym_label']})" if row["gym_label"] else ""
    mark_nudge("morning_brief", today)

    # External veto from another agent (e.g. Bajrangi) — drop entirely.
    vetoed, reason = check_external_veto("morning_brief")
    if vetoed:
        return None

    # Scientist's own HRV-RED downgrade.
    downgrade, why = _internal_safety_downgrade()
    if downgrade:
        return (
            f"⚠️ *{why}*\n"
            f"{WEEKDAY_NAME[row['weekday']]} was scheduled as *{kind}*{gym}, "
            "but today is recovery only.\n"
            "Prescription: total rest, 20 min 7/15 breathing, "
            "magnesium, in bed by 10pm."
        )
    return (
        f"☀️ *Morning brief — {WEEKDAY_NAME[row['weekday']]}*\n"
        f"Today: *{kind}*{gym}. Ideal burn: {fmt_kcal(row['target_kcal'])}.\n"
        f"Week so far: {fmt_kcal(burned)} / {fmt_kcal(target)}."
    )


def maybe_recovery_nudge() -> str | None:
    """At 21:00, if today's burn is below the day-type target, recommend recovery."""
    now = datetime.now()
    if now.hour != NUDGE_RECOVERY_HOUR:
        return None
    today = now.strftime("%Y-%m-%d")
    if nudge_already_sent("recovery_21", today):
        return None
    today_burn = burn_for_date(now)
    row = today_plan()
    target = row["target_kcal"]
    if target == 0 or today_burn >= target:
        return None
    deficit = target - today_burn
    kind = DAY_TYPE_LABEL[row["day_type"]]
    mark_nudge("recovery_21", today)
    if row["day_type"] == "rest":
        prescription = ("25–35 min easy walk + 5 min mobility "
                        "(couch stretch / pigeon / thread-the-needle).")
    elif row["day_type"] == "z2":
        prescription = ("Skipped the run? Cap the loss with a 30-min brisk "
                        "walk + 10 min thoracic mobility.")
    else:
        prescription = ("Add a 20-min Zone-2 walk + 100 air squats, or "
                        "log it to workout_log if you trained off-watch.")
    return (
        f"🌙 9pm check — {kind} day. Today: {fmt_kcal(today_burn)} / "
        f"{fmt_kcal(target)} ({fmt_kcal(deficit)} short).\n"
        f"{prescription}"
    )


def maybe_walk_nudge() -> str | None:
    """During waking hours on a lagging day, nudge a walk vs the day-type pace.
    Performance-flavored; suppressed if another agent has vetoed walk nudges,
    or if the Scientist's own HRV check is in the RED band."""
    now = datetime.now()
    if not (NUDGE_HOURLY_START <= now.hour <= NUDGE_HOURLY_END):
        return None
    today = now.strftime("%Y-%m-%d")
    slot = f"walk_{now.hour:02d}"
    if nudge_already_sent(slot, today):
        return None
    vetoed, _ = check_external_veto("walk_nudge")
    if vetoed:
        mark_nudge(slot, today)   # respect the throttle so we don't re-check every minute
        return None
    downgrade, _ = _internal_safety_downgrade()
    if downgrade:
        mark_nudge(slot, today)
        return None
    row = today_plan()
    target = row["target_kcal"]
    if target == 0:
        return None
    today_burn = burn_for_date(now)
    # On scheduled training days, don't nag in the morning before they've trained.
    if row["day_type"] in ("cf", "z2") and now.hour < 14 and today_burn < target:
        return None
    # On rest days, only nudge if behind a non-trivial floor.
    if row["day_type"] == "rest" and today_burn >= NONWORKOUT_BURN_FLOOR:
        return None
    span = NUDGE_HOURLY_END - NUDGE_HOURLY_START + 1
    elapsed = max(now.hour - NUDGE_HOURLY_START + 1, 1)
    pace = target * (elapsed / span)
    if today_burn >= 0.7 * pace:
        return None
    mark_nudge(slot, today)
    if row["day_type"] == "rest":
        suggestion = ("Take a 10–15 min walk *or* a 10-min stretch/cooldown "
                      "(pigeon, couch stretch, thoracic foam roller).")
    else:
        suggestion = "Take a 10–15 min walk this hour."
    return (
        f"🚶 Pace check ({now.strftime('%-I%p')}) — "
        f"{DAY_TYPE_LABEL[row['day_type']]} day. "
        f"Today: {fmt_kcal(today_burn)} vs pace {fmt_kcal(pace)}. {suggestion}"
    )


def maybe_weekly_reset() -> str | None:
    """Sun 23:55 → recap the week ending tonight + lock in the next week's target.

    Workout week is Mon 00:00 → Sun 23:59 local time. The fresh `weekly_campaigns`
    row written here makes the new week's target queryable from anywhere
    (dashboards, future agents) without needing to re-derive it from the tier.
    Throttled once per week_key via nudge_log so a slow Sunday doesn't double-fire.
    """
    now = datetime.now()
    if now.weekday() != 6 or now.hour != 23 or now.minute < 55:
        return None
    monday, sunday_end = week_bounds(now)
    week_key = monday.strftime("%Y-%m-%d")
    if nudge_already_sent("week_reset", week_key):
        return None

    burned = burn_for_range(monday, sunday_end)
    target = weekly_target()
    pct = (burned / target * 100) if target else 0
    tier = state_get("recovery_tier", DEFAULT_TIER)
    next_mon = monday + timedelta(days=7)

    # Lock in next week's campaign row at the active tier's weekly target.
    con = _db()
    try:
        con.execute(
            "INSERT OR IGNORE INTO weekly_campaigns "
            "(week_start, target_active_calories) VALUES (?, ?)",
            (next_mon.strftime("%Y-%m-%d"), target))
        con.commit()
    finally:
        con.close()

    mark_nudge("week_reset", week_key)
    delta = burned - target
    verdict = (f"+{fmt_kcal(delta)} over"   if delta >= 0
               else f"{fmt_kcal(-delta)} short")
    return (
        f"📅 *Week ending* {monday.strftime('%b %-d')} – {sunday_end.strftime('%b %-d')}\n"
        f"Total: *{fmt_kcal(burned)}* / {fmt_kcal(target)} ({pct:.0f}%, {verdict}).\n"
        f"\n*New week starts Monday 00:00* — tier `{tier}`.\n"
        f"Target: {fmt_kcal(target)} | Daily pad: {fmt_kcal(daily_target())}.\n"
        f"Counters reset. Fresh slate."
    )


# ─────────────────────────── Loop ─────────────────────────────
def send(text: str) -> None:
    if not (TOKEN and CHAT_ID):
        print(text)
        return
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})


def start():
    if TOKEN:
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook")
    tier = state_get("recovery_tier", DEFAULT_TIER)
    print(f"🔬 Scientist live | model={MODEL_ID} | tier={tier} | db={DB_PATH}")
    last_id = 0
    last_tick_minute = -1
    while True:
        try:
            if TOKEN:
                r = requests.get(
                    f"https://api.telegram.org/bot{TOKEN}/getUpdates"
                    f"?offset={last_id+1}&timeout=10").json()
                for up in r.get("result", []):
                    last_id = up["update_id"]
                    msg = up.get("message", {}) or up.get("edited_message", {})
                    txt = msg.get("text")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not txt:
                        continue
                    print(f"[in] chat={chat_id} text={txt!r}")
                    if CHAT_ID and chat_id and chat_id != str(CHAT_ID):
                        print(f"[skip] chat_id mismatch (expected {CHAT_ID})")
                        continue
                    try:
                        reply = route(txt)
                    except Exception as e:
                        reply = f"❌ handler error: {e}"
                        print(reply)
                    send(reply)

            now = datetime.now()
            if now.minute != last_tick_minute:
                last_tick_minute = now.minute
                for nudge in (maybe_weekly_reset(),
                              maybe_recovery_nudge(),
                              maybe_walk_nudge()):
                    if nudge:
                        send(nudge)

            time.sleep(1)
        except Exception as e:
            print(f"loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    start()
