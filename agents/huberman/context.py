"""agents.huberman.context — read the day's training + recovery state.

Huberman S1 (2026-08-24). READ-ONLY over the HealthKit substrate the
bridge writes (workouts_hk, sleep_sessions, raw_vitals — see
bridges/healthkit/ingest.py). All reads go through state.db_path(),
which sandboxes under RAHAT_TEST_MODE=1.

The one behavioral rule that lives here: WHAT COUNTS AS A CROSSFIT DAY.
Owner (2026-08-23): the 9:30 PM autorun fires only when a CrossFit
workout happened today; "on days that I run, I will work with it
directly and ask". HealthKit types the sessions for us — runs/walks/
hikes are named as such ("Outdoor Run", "Walking"…) while box work
lands as "Cross Training" / "Traditional Strength Training" / "HIIT" /
"Functional …". So: any workout today whose name does NOT match the
ask-directly family (run/walk/hike/cycle/swim) is an autocool trigger.
Missing table or empty day → no trigger, silently (the no-data safety
floor, same spirit as core.huberman_bridge's all-None contract).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

from agents.huberman import state

_ASK_DIRECT_RE = re.compile(r"run|walk|hik|cycl|bike|swim", re.I)


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    try:
        cur = con.execute(sql, args)
        return cur.fetchall()
    except sqlite3.Error:
        return []


def workouts_today(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    con = state.connect()
    try:
        rows = _rows(con,
                     "SELECT name, start, end, duration_s, active_kcal, "
                     "avg_hr, max_hr, distance_km FROM workouts_hk "
                     "WHERE start LIKE ? ORDER BY start", (f"{day}%",))
    finally:
        con.close()
    out = []
    for name, start, end, dur, kcal, avg_hr, max_hr, dist in rows:
        out.append({"name": name or "Workout", "start": start, "end": end,
                    "minutes": round((dur or 0) / 60),
                    "kcal": round(kcal or 0),
                    "avg_hr": round(avg_hr) if avg_hr else None,
                    "max_hr": round(max_hr) if max_hr else None,
                    "distance_km": round(dist, 2) if dist else None,
                    "ask_direct": bool(_ASK_DIRECT_RE.search(name or ""))})
    return out


def crossfit_workouts_today(now: datetime | None = None) -> list[dict]:
    """The autocool trigger set: today's non-run/walk workouts."""
    return [w for w in workouts_today(now) if not w["ask_direct"]]


def _latest_metric(con: sqlite3.Connection, metric: str,
                   since: str) -> float | None:
    rows = _rows(con, "SELECT value FROM raw_vitals WHERE metric_type=? "
                      "AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1",
                 (metric, since))
    return rows[0][0] if rows else None


def gym_wod_today(now: datetime | None = None) -> str | None:
    """Today's PROGRAMMED WOD text (SugarWOD, via Kobe's date-aware
    lookup) — independent of whether the Watch has synced the session.
    2026-09-02 live: the 9:30 autocool said "No workout logged" on a
    day the owner did the WOD, because HAE's workout export runs on a
    morning schedule. The programming is the reliable signal of what
    today loaded; None when nothing is synced for the date."""
    now = now or datetime.now()
    try:
        from agents.the_scientist.handler import handle_gym_wod_on_date
        text = handle_gym_wod_on_date(now) or ""
    except Exception:  # noqa: BLE001 — Kobe unavailable → no WOD context
        return None
    if not text.strip() or "no WOD synced" in text:
        return None
    return text.strip()


def plan_day_type(now: datetime | None = None) -> str:
    """Kobe's weekly plan for today: 'cf' | 'z2' | 'rest' | '' (unknown)."""
    try:
        from agents.the_scientist.state import today_plan
        return str((today_plan() or {}).get("day_type") or "")
    except Exception:  # noqa: BLE001
        return ""


def gather(now: datetime | None = None) -> dict:
    """Everything the coach prompt wants, one call. Missing data stays
    None/[] — the prompt renders only what exists; the composer never
    needs any of it."""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    workouts = workouts_today(now)

    con = state.connect()
    try:
        kcal_rows = _rows(con, "SELECT SUM(value) FROM raw_vitals WHERE "
                               "metric_type='active_calories' AND "
                               "timestamp LIKE ?", (f"{day}%",))
        active_kcal = (round(kcal_rows[0][0])
                       if kcal_rows and kcal_rows[0][0] else None)
        hrv = _latest_metric(con, "hrv", week_ago)
        rhr = _latest_metric(con, "resting_heart_rate", week_ago)
        sleep_rows = _rows(con, "SELECT sleep_end, asleep_s FROM "
                                "sleep_sessions WHERE sleep_end LIKE ? "
                                "ORDER BY sleep_end DESC LIMIT 1",
                           (f"{day}%",))
    finally:
        con.close()

    sleep_hours = (round(sleep_rows[0][1] / 3600, 1)
                   if sleep_rows and sleep_rows[0][1] else None)
    wod = gym_wod_today(now)
    from agents.huberman.protocols import loaded_areas
    cf_synced = [w for w in workouts if not w["ask_direct"]]
    day_type = plan_day_type(now)
    # "Trained today" is TRUE when the Watch synced a CrossFit-family
    # session, or — the 09-02 gap — when the plan says CrossFit and a
    # WOD is programmed but the Watch export simply hasn't run yet.
    assumed = (not workouts and day_type == "cf" and wod is not None)
    return {
        "date": day,
        "workouts": workouts,
        "crossfit_today": cf_synced,
        "gym_wod_today": wod,
        "plan_day_type": day_type,
        "loaded": loaded_areas(wod),
        "trained_today": bool(cf_synced) or assumed,
        "assumed_from_plan": assumed,
        "active_kcal_today": active_kcal,
        "hrv_ms": round(hrv) if hrv else None,
        "resting_hr": round(rhr) if rhr else None,
        "sleep_hours_last_night": sleep_hours,
    }
