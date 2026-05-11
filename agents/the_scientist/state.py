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
    DEFAULT_TIER,
    INTENT_INTERMEDIATE_DATE, INTENT_INTERMEDIATE_KG,
    INTENT_TARGET_DATE, INTENT_TARGET_KG,
    TIERS,
    WEEKLY_ACTIVE_TARGET_KCAL,
    week_bounds,
)

__all__ = [
    "_db",
    "burn_for_date",
    "burn_for_range",
    "burn_last_week",
    "burn_this_week",
    "check_external_veto",
    "get_active_intent",
    "state_get",
    "state_set",
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


