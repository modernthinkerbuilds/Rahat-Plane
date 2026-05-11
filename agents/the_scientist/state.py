"""state — Scientist's stateful (DB-backed) data layer.

Extracted from main.py per Phase 4d (R1) Step 1a. These functions own
the I/O boundary: every call here opens/closes a sqlite3 connection
against the Scientist's intent ledger.

DB path comes from `core.io.DB_PATH` (centralized in Step 0a, commit
11317c9). RAHAT_TEST_MODE=1 redirects writes to a per-process tempfile,
RAHAT_DB_PATH lets ops point at a custom DB, and tests patch
`cio.DB_PATH = X` to sandbox individual cases.

What's in here:
    • `_db()` — connection factory + auto-migration of every owned table
    • `state_get` / `state_set` — user_state KV
    • `burn_for_date` / `burn_for_range` / `burn_this_week` / `burn_last_week`
    • `weekly_target` — layered: memory commitment > active tier > legacy
    • `get_active_intent` / `check_external_veto` — cross-agent signals

What's NOT here (stays in main.py for now):
    • Per-week preferences + weight/HRV log helpers — Phase 4d Step 1b
    • Handlers, router, nudges, loop — Phase 4d Step 2
    • Pure math + constants — protocols.py

Importing rule:
    from agents.the_scientist.state import _db, state_get, ...

The legacy `sci._db()` / `sci.state_get()` patterns still resolve because
main.py does `from agents.the_scientist.state import *` and state.py's
__all__ exports every public + underscored name explicitly.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from core import io as cio

from agents.the_scientist.protocols import (
    KCAL_PER_LB_FAT,
    KCAL_PER_KG_FAT,
    WEEKLY_ACTIVE_TARGET_KCAL,
    DAILY_INTAKE_KCAL,
    INTENT_INTERMEDIATE_KG,
    INTENT_INTERMEDIATE_LBS,
    INTENT_TARGET_KG,
    INTENT_TARGET_LBS,
    INTENT_INTERMEDIATE_DATE,
    INTENT_TARGET_DATE,
    TARGET_LBS,
    EASY_LOSS_LB_PER_WEEK,
    MAX_LOSS_LB_PER_WEEK,
    LOCKED_LOSS_LB_PER_WEEK,
    TYPICAL_BURN,
    TIERS,
    DEFAULT_TIER,
    HRV_RED,
    HRV_YELLOW,
    HRV_GREEN,
    HRV_ELITE,
    BLACKLIST,
    STRENGTH_BLACKLIST,
    Z2_RUN_KCAL_DEFAULT,
    NONWORKOUT_BURN_FLOOR,
    NUDGE_MORNING_HOUR,
    NUDGE_HOURLY_START,
    NUDGE_HOURLY_END,
    NUDGE_RECOVERY_HOUR,
    HAMMER_KCAL,
    MISSED_WORKOUT_THRESHOLD_KCAL,
    DAY_TYPE_BY_TIER,
    DAY_TYPE_LABEL,
    WEEKDAY_INDEX,
    WEEKDAY_NAME,
    Z2_PREFERRED_WEEKDAY,
    week_bounds,
    hrv_band,
    fmt_kcal,
    fmt_lbs,
    _empty_prefs,
    _eta_at_locked_rate,
    _locked_intake,
    _WEEKDAY_LOOKUP,
    _WEEKDAY_TOKEN_RE,
    parse_weekdays,
    _BLACKLIST_NORMALIZE,
    normalize_blacklist_term,
    DAY_HEADER,
    GymDay,
    parse_gym_plan,
    eligible_cf_days,
)

# ─── private aliases inherited from main.py Section 6 ───
import json as _json  # local alias to avoid touching the import block

__all__ = [
    "_db",
    "burn_for_date",
    "burn_for_range",
    "burn_last_week",
    "burn_this_week",
    "check_external_veto",
    "clear_prefs",
    "compute_week_recalibration",
    "current_plan",
    "day_type_target",
    "detect_missed_workouts",
    "get_active_intent",
    "get_prefs",
    "last_hammer_day",
    "latest_weight",
    "log_hrv",
    "log_workout",
    "mark_nudge",
    "nudge_already_sent",
    "recalibrate_intents",
    "replan_week",
    "set_prefs",
    "state_get",
    "state_set",
    "sync_weight",
    "today_plan",
    "weekly_target",
]


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
    con = sqlite3.connect(str(cio.DB_PATH))
    con.executescript(
        # Watch / ingestor tables — owned upstream but auto-migrated
        # here so a freshly-initialized DB doesn't fail when tests
        # (or first-boot Scientist) reads from them.
        "CREATE TABLE IF NOT EXISTS raw_vitals ("
        " metric_type TEXT, value REAL, timestamp TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_campaigns ("
        " week_start DATE PRIMARY KEY,"
        " target_active_calories REAL NOT NULL,"
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP);"
        # Scientist-owned state below.
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
    # Seed both Scientist intents only if no row of that kind exists yet.
    # Plain INSERT OR IGNORE doesn't help here because the table's UNIQUE
    # constraint is on (kind, target_date) — and recalibrate_intents() shifts
    # target_date over time, which would otherwise let the seed create
    # duplicate rows for the same kind.
    for kind, value, date in [
        ("weight_intermediate_kg", INTENT_INTERMEDIATE_KG, INTENT_INTERMEDIATE_DATE),
        ("weight_kg",              INTENT_TARGET_KG,       INTENT_TARGET_DATE),
    ]:
        exists = con.execute(
            "SELECT 1 FROM intents WHERE kind=? LIMIT 1", (kind,)).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO intents (kind, target_value, target_date) "
                "VALUES (?, ?, ?)", (kind, value, date))
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
    """User's currently-active weekly active-burn target. Layered:

      1. **Active commitment in memory substrate** (highest priority):
         If the user has committed to a custom weekly_target ("I'll do
         7,000 kcal/wk for 2 weeks" → memory_entities row of type
         'commitment' with kind='weekly_target' and a future
         valid_until), use THAT. This is the v2.0 model-first path.
      2. **Tier table:** otherwise the active tier's `weekly` value
         (performance=6000, baseline=5500, hammer=6500, etc.).
      3. **Legacy weekly_campaigns table:** last-resort fallback for
         databases that predate the tier system.

    Tick nudges (pace check, morning brief) and `propose_replan` all
    funnel through this single function so the user's active commitment
    is respected by every calc-side path.
    """
    # ─── (1) Active commitment in memory substrate ───
    try:
        from core import memory as _mem
        for ent in _mem.list_entities("scientist", type="commitment"):
            payload = ent.get("payload") or {}
            if payload.get("kind") == "weekly_target":
                v = payload.get("value")
                if isinstance(v, (int, float)) and v > 0:
                    return float(v)
    except Exception:
        # Memory substrate unavailable (test env, fresh DB, etc.).
        # Fall through silently to tier-based default.
        pass

    # ─── (2) Active tier ───
    tier = state_get("recovery_tier", DEFAULT_TIER)
    if tier in TIERS:
        return float(TIERS[tier]["weekly"])

    # ─── (3) Legacy weekly_campaigns ───
    con = _db()
    try:
        row = con.execute(
            "SELECT target_active_calories FROM weekly_campaigns "
            "ORDER BY week_start DESC LIMIT 1").fetchone()
        return float(row[0]) if row else float(WEEKLY_ACTIVE_TARGET_KCAL)
    finally:
        con.close()




# ─── per-week preferences + log helpers (Phase 4d R1 Step 1b) ───
# Section 6 of agents/the_scientist/main.py prior to the god-file
# split. Same DB-I/O substrate as Step 1a functions.

# These let the user say "I can't make it on day X this week" or "pick days
# X/Y/Z for CF instead". They auto-expire at the Sunday reset because the
# row is keyed on week_start.
# _WEEKDAY_LOOKUP, _WEEKDAY_TOKEN_RE, parse_weekdays,
# _BLACKLIST_NORMALIZE, normalize_blacklist_term, _empty_prefs
# all imported from protocols.py (top of file).

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
    """Authoritative weight read.

    Apple Watch (raw_vitals via vitals_listener) is the source of truth.
    Manual `wt:` entries (weighin_log) are stop-gaps for when the watch
    hasn't synced. The vitals_listener writes date-only timestamps
    ('2026-05-04') while weighin_log writes full datetimes
    ('2026-05-04 20:14:21'); naive lexical comparison would make the
    longer string win, so manual would always shadow same-day watch
    syncs. We compare on the date prefix and prefer watch when its
    date is on or after the manual entry's date.
    """
    con = _db()
    try:
        watch = con.execute("""
            SELECT value, substr(timestamp, 1, 10) AS day, timestamp
            FROM raw_vitals
            WHERE metric_type='weight'
            ORDER BY day DESC, timestamp DESC, rowid DESC
            LIMIT 1
        """).fetchone()
        manual = con.execute("""
            SELECT weight_lbs, substr(ts, 1, 10) AS day, ts
            FROM weighin_log
            ORDER BY day DESC, ts DESC, rowid DESC
            LIMIT 1
        """).fetchone()
        if watch and manual:
            # Watch wins ties — it's the authoritative source.
            return float(watch[0]) if watch[1] >= manual[1] else float(manual[0])
        if watch:
            return float(watch[0])
        if manual:
            return float(manual[0])
        return 198.0
    finally:
        con.close()


def sync_weight(val: float) -> None:
    """Log a new weight and trigger intent recalibration so the projected
    target dates always reflect the latest reading."""
    con = _db()
    try:
        con.execute("INSERT INTO weighin_log (weight_lbs) VALUES (?)", (val,))
        con.commit()
    finally:
        con.close()
    recalibrate_intents()


def recalibrate_intents() -> dict[str, str]:
    """Project new target_date for both weight intents from current weight
    at the locked sustainable rate. Returns a {kind: new_date} mapping for
    callers that want to surface the change. Marks an intent 'met' if the
    user is already at or below the target."""
    current = latest_weight()
    today = datetime.now()
    updates: dict[str, str] = {}
    spec = [
        ("weight_intermediate_kg", INTENT_INTERMEDIATE_LBS),
        ("weight_kg",              INTENT_TARGET_LBS),
    ]
    con = _db()
    try:
        for kind, target_lbs in spec:
            if current <= target_lbs:
                new_date = today.strftime("%Y-%m-%d")
                status = "met"
            else:
                weeks = (current - target_lbs) / LOCKED_LOSS_LB_PER_WEEK
                new_date = (today + timedelta(weeks=weeks)).strftime("%Y-%m-%d")
                status = "active"
            con.execute(
                "UPDATE intents SET target_date=?, status=? WHERE kind=?",
                (new_date, status, kind))
            updates[kind] = new_date
        con.commit()
    finally:
        con.close()
    return updates


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




# ─── planning + recalibration helpers (Phase 4d R1 Step 2a) ───
# Sections 4 + 5 of agents/the_scientist/main.py prior to the
# god-file split. They compute schedules and weekly recalibrations
# over the state owned by this module's earlier helpers.
# handle_recalibrate stays in main.py — it's a handler that calls
# send(), not pure compute. It moves with the other handlers in
# Step 2b.

def day_type_target(day_type: str, tier: str | None = None) -> int:
    """DB-coupled: reads `recovery_tier` from user_state if no tier given.
    Pure-math equivalent in protocols.DAY_TYPE_BY_TIER."""
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
        # Track whether we had to fall back to a default cadence because no
        # gym plan was synced for this week. handle_show_plan reads this
        # via the user_state key 'plan_fallback' to surface a warning.
        plan_fallback = False
        if forced_orig:
            cf_wds = list(forced_kept)
            for wd in eligible_wds:
                if len(cf_wds) >= target_count:
                    break
                if wd not in cf_wds:
                    cf_wds.append(wd)
        elif len(eligible_wds) >= 3:
            # Normal path: gym plan synced + has 3+ CF-eligible days.
            cf_wds = list(eligible_wds[:3])
        else:
            # Either no gym plan synced, OR the gym programming has too
            # many blacklisted movements this week (handstand, OH squat,
            # snatch-in-strength, partner WODs) leaving fewer than 3
            # CF-eligible days. Production bug 2026-05: this branch
            # used to leave the user with only 1 CF day for the week.
            # Now we ALWAYS pick 3 CF days: take the eligible days
            # first (gym-aligned), then backfill from the standard
            # Mon/Wed/Fri default to reach the locked cadence.
            cf_wds = list(eligible_wds)
            # User-configurable default cadence: if the user has set a
            # `default_cf_pattern` (via "remember this pattern" or by
            # picking the same days repeatedly), use that as the fallback
            # instead of the standard Mon/Wed/Fri. This means a user
            # who prefers Mon/Tue/Fri/Sun doesn't have to re-pick every
            # week. Falls back to Mon/Wed/Fri if no pattern set.
            default_pattern_str = state_get("default_cf_pattern", "")
            if default_pattern_str:
                try:
                    DEFAULT_CF_FALLBACK = [int(x) for x in
                                           default_pattern_str.split(",")
                                           if x.strip().isdigit()]
                except Exception:
                    DEFAULT_CF_FALLBACK = [0, 2, 4]
            else:
                DEFAULT_CF_FALLBACK = [0, 2, 4]  # Mon, Wed, Fri
            for wd in DEFAULT_CF_FALLBACK:
                if len(cf_wds) >= 3:
                    break
                if wd in cf_wds or wd in unavailable or wd == 5:
                    continue
                cf_wds.append(wd)
            # If still <3 (Mon/Wed/Fri all unavailable or already used),
            # slide to Tue/Thu/Sun.
            if len(cf_wds) < 3:
                for wd in [1, 3, 6]:
                    if len(cf_wds) >= 3:
                        break
                    if wd in cf_wds or wd in unavailable or wd == 5:
                        continue
                    cf_wds.append(wd)
            # plan_fallback flag: True if we had to supplement at least
            # one default day (i.e., not every CF day came from a clean
            # gym pick). handle_show_plan surfaces a sync/scale warning.
            plan_fallback = (len(eligible_wds) < 3)
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
        # Record whether this week's plan came from the gym schedule or
        # the fallback default cadence. handle_show_plan reads this to
        # surface a "sync the bookmarklet" warning when relevant.
        fallback_key = f"plan_fallback_{week_key}"
        con.execute(
            "INSERT INTO user_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (fallback_key, "1" if plan_fallback else "0"))
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


def detect_missed_workouts(plan: list[dict], today_idx: int,
                           monday: datetime) -> list[dict]:
    """Find past CF/Z2 days where active burn is below the
    "no workout happened" threshold (default 700 kcal). Per the
    user's spec: at <700 kcal you're nowhere near the CF target
    (850) or Z2 target (1100), so the workout almost certainly
    didn't happen — and the plan should reflect that.

    Critically: today is NEVER counted as missed (still in progress).
    Only past days are evaluated.

    Returns a list of dicts:
        {weekday, weekday_name, day_type, target_kcal, actual_burn,
         shortfall (target - actual)}
    """
    missed: list[dict] = []
    for row in plan:
        wd = row["weekday"]
        if wd >= today_idx:
            continue   # today and future are never "missed"
        if row["day_type"] not in ("cf", "z2"):
            continue   # rest days are not "missed" by definition
        actual = burn_for_date(monday + timedelta(days=wd))
        if actual >= MISSED_WORKOUT_THRESHOLD_KCAL:
            continue
        missed.append({
            "weekday": wd,
            "weekday_name": WEEKDAY_NAME[wd],
            "day_type": row["day_type"],
            "day_type_label": DAY_TYPE_LABEL[row["day_type"]],
            "target_kcal": row["target_kcal"],
            "actual_burn": actual,
            "shortfall": max(row["target_kcal"] - actual, 0),
        })
    return missed


def compute_week_recalibration(now: datetime | None = None) -> dict:
    """Daily review: am I on track to hit the weekly active-burn target?

    Returns a dict with the gap analysis and a redistribution proposal:
        {
          "today_idx":          0-6,
          "burned_so_far":      kcal already in raw_vitals/workout_log,
          "weekly_target":      from active tier,
          "remaining_to_goal":  weekly_target - burned_so_far,
          "remaining_planned":  sum of target_kcal for today + future days,
          "gap":                remaining_to_goal - remaining_planned
                                (positive = behind; negative = ahead),
          "on_track":           True if abs(gap) < tolerance,
          "proposal":           list of dicts {weekday, from, to, reason}
                                describing the suggested redistribution,
          "summary":            human-readable description.
        }

    Redistribution algorithm (when behind by `gap` kcal):
      1. List remaining REST days (excluding today if today is past noon).
      2. Each rest → CF conversion adds ~350 kcal (cf 850 - rest 500).
      3. Convert as many rest days to CF as needed, in order:
         Mon → Wed → Fri → Tue → Thu → Sun (skip Sat which is Z2 default).
      4. If still behind after converting all rest days, suggest
         extending the Z2 day (or adding a second Z2 if the user has
         capacity).

    The function is read-only — it doesn't mutate the plan. It just
    *proposes*. The user applies via existing commands ("pick X Y for cf",
    "swap X for Y").
    """
    now = now or datetime.now()
    today_idx = now.weekday()
    monday, _ = week_bounds(now)
    plan = current_plan(monday)

    # What's already happened this week.
    burned_so_far = burn_for_range(monday, now)
    weekly_t = weekly_target()
    remaining_to_goal = max(weekly_t - burned_so_far, 0.0)

    # What's still planned (today + future). For "today", count the
    # ideal even if user has burned some already — they may still hit it.
    remaining_planned = sum(
        d["target_kcal"] for d in plan if d["weekday"] >= today_idx
    )

    # Tolerance: ±10% of weekly target is "on track" — small gaps don't
    # warrant a redistribution suggestion.
    tolerance = weekly_t * 0.10
    gap = remaining_to_goal - remaining_planned
    on_track = abs(gap) <= tolerance

    proposal: list[dict] = []
    if not on_track and gap > 0:
        # Behind. Convert future rest days to CF until the gap closes —
        # but ONLY days the user can actually do without scaling
        # blacklisted movements. We re-run the gym blacklist filter
        # against the user's tolerated_blacklist for this week.
        kcal_per_conversion = max(
            day_type_target("cf") - day_type_target("rest"), 1)
        needed = int(gap // kcal_per_conversion) + (
            1 if gap % kcal_per_conversion else 0)

        # Get gym-eligible days for the week (after blacklist + tolerance
        # filter). Same logic as replan_week's eligibility check.
        monday_for_week, _ = week_bounds(now)
        prefs = get_prefs(monday_for_week)
        unavailable = set(prefs["unavailable_days"])
        tolerated = {
            normalize_blacklist_term(t)
            for t in prefs["tolerated_blacklist"]
        }
        gym_days = parse_gym_plan()
        gym_eligible: set[int] = set()
        for d in gym_days:
            wd = WEEKDAY_INDEX.get(d.weekday[:3])
            if wd is None:
                continue
            blocked = False
            for b in d.blockers:
                core = b.split(" (")[0]
                if normalize_blacklist_term(core) not in tolerated:
                    blocked = True
                    break
            if not blocked and wd not in unavailable:
                gym_eligible.add(wd)

        rest_priority = [0, 2, 4, 1, 3, 6]   # Mon, Wed, Fri, Tue, Thu, Sun
        future_rest_wds = [
            wd for wd in rest_priority
            if wd >= today_idx
            and any(p["weekday"] == wd and p["day_type"] == "rest"
                    for p in plan)
        ]
        # Sort future rest days: gym-eligible first (no blacklist), then
        # the rest. Gym-eligible days mean the user can do the CF without
        # scaling, so they're strictly preferred.
        future_rest_wds.sort(
            key=lambda wd: (0 if wd in gym_eligible else 1,
                            rest_priority.index(wd)))
        for wd in future_rest_wds[:needed]:
            is_gym_clean = wd in gym_eligible
            proposal.append({
                "weekday": wd,
                "weekday_name": WEEKDAY_NAME[wd],
                "from": "rest",
                "to": "cf",
                "delta_kcal": kcal_per_conversion,
                "gym_clean": is_gym_clean,
                "reason": ("gym programming clean"
                           if is_gym_clean else
                           "scale blacklisted movements"),
            })

    # Build a human summary.
    if on_track and gap >= 0:
        summary = (
            f"On track. Burned {fmt_kcal(burned_so_far)} of "
            f"{fmt_kcal(weekly_t)}; planned {fmt_kcal(remaining_planned)} "
            f"more covers the gap.")
    elif on_track and gap < 0:
        summary = (
            f"Ahead of pace. Burned {fmt_kcal(burned_so_far)} vs "
            f"{fmt_kcal(weekly_t)} target — comfortable buffer.")
    elif gap > 0 and proposal:
        added = ", ".join(p["weekday_name"] for p in proposal)
        summary = (
            f"Behind by {fmt_kcal(gap)}. To catch up, convert "
            f"{added} from rest → CrossFit. "
            f"Apply with: `pick "
            f"{' '.join(WEEKDAY_NAME[p['weekday']] for p in proposal)} "
            f"for crossfit`.")
    elif gap > 0:
        # Behind but no rest days available to convert.
        summary = (
            f"Behind by {fmt_kcal(gap)}. No rest days left to convert "
            f"in the remaining schedule. Consider extending Saturday's "
            f"Z2 run or adding a second Z2 if HRV allows.")
    else:
        # gap <= 0: ahead of pace but outside the on-track tolerance
        # band. fmt_kcal of a negative number reads "−1,685 kcal", which
        # is correct but parses awkwardly in "Behind by …" framing —
        # so flip the sign and frame as "ahead of plan".
        summary = (
            f"Ahead of plan by {fmt_kcal(-gap)} — the remaining schedule "
            f"already over-covers your weekly target. You can take a "
            f"rest day if HRV is low without losing the goal.")

    # Also detect any missed workouts so callers can surface them.
    missed_workouts = detect_missed_workouts(plan, today_idx, monday)

    return {
        "today_idx":         today_idx,
        "burned_so_far":     burned_so_far,
        "weekly_target":     weekly_t,
        "remaining_to_goal": remaining_to_goal,
        "remaining_planned": remaining_planned,
        "gap":               gap,
        "on_track":          on_track,
        "proposal":          proposal,
        "summary":           summary,
        "missed":            missed_workouts,
    }
