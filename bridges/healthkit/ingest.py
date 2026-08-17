"""HealthKit ingestion — pure parsing + idempotent writes.

Payload contract (Health Auto Export "API Export JSON Format"):

    {"data": {"metrics": [{"name": "...", "units": "...",
                           "data": [{"date": "...", "qty": 1.0}
                                    | {"date": "...", "Min":, "Avg":, "Max":}
                                    | {sleep-analysis fields}]}],
              "workouts": [{"id":, "name":, "start":, "end":, ...}]}}

Design rules:
  * IDEMPOTENT — automations re-send overlapping windows ("Since Last
    Sync", full-previous-day). Every write is delete-then-insert on its
    natural key, so replays converge instead of duplicating.
  * NEVER raises on a bad record — skip and count. One malformed point
    must not reject a 10MB batch.
  * Legacy semantics preserved exactly: `weight` keeps a single record
    (global override); `active_calories` overrides per-day. Kobe's burn
    math depends on both.
  * Sleep sessions (incl. NAPS — each session is its own row, which the
    2026-08-03 "I napped 2h both days" correction showed matters) land
    in their own table, phases in seconds.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# HAE metric name (normalized) → our canonical raw_vitals metric_type.
# HAE names drift between "Heart Rate" and "heart_rate" across versions;
# normalize to snake_case first.
METRIC_MAP = {
    "heart_rate": "heart_rate",                       # Min/Avg/Max points
    "heart_rate_variability": "hrv",
    "resting_heart_rate": "resting_heart_rate",
    "respiratory_rate": "respiratory_rate",
    "step_count": "steps",
    "steps": "steps",
    "active_energy": "active_calories",               # day-override
    "active_energy_burned": "active_calories",
    "basal_energy_burned": "basal_calories",
    "weight_body_mass": "weight",                     # single-record
    "weight": "weight",
    "body_mass": "weight",
    "blood_oxygen_saturation": "spo2",
    "oxygen_saturation": "spo2",
    "vo2_max": "vo2_max",
    "walking_heart_rate_average": "walking_heart_rate",
    "heart_rate_recovery_one_minute": "hr_recovery_1min",
    "apple_sleeping_wrist_temperature": "wrist_temperature",
    "wrist_temperature": "wrist_temperature",
    "time_in_daylight": "daylight_minutes",
    "mindful_minutes": "mindful_minutes",
    "stand_hours": "stand_hours",
    "exercise_time": "exercise_minutes",
    "apple_exercise_time": "exercise_minutes",
    "flights_climbed": "flights_climbed",
    "walking_running_distance": "distance_km",
    "sleep_analysis": "__sleep__",                    # special-cased
}

# Legacy write semantics (must match the old vitals_listener exactly).
SINGLE_RECORD = {"weight"}          # keep ONE row ever

# ── Daily metrics: exactly ONE row per day holding the day's TOTAL (or
# reading). Kobe's kcal model runs SUM(value) over a day, so a day must
# never hold both a total row and its component samples — hence
# delete-then-insert per day.
#
# TWO SHAPES hide in here, and conflating them destroyed real data
# (live, 2026-08-16): Health Auto Export exports at MINUTE grouping, so
# active_calories arrives as hundreds of tiny per-minute INCREMENTS
# (0.2 kcal each) — NOT as the single day total the retired iPhone
# Shortcut used to send. The old "newest sample wins" rule then kept the
# final minute of the day (0.0 kcal) and deleted the true 1,152 kcal
# total. Kobe then answered "0 calories today" — correctly, from wrecked
# data. Accumulating metrics must be SUMMED across the day; only true
# once-a-day readings may take newest-wins.
DAY_SUM = {                          # accumulates through the day → SUM
    "active_calories", "basal_calories", "stand_hours",
    "exercise_minutes", "flights_climbed", "daylight_minutes",
    "mindful_minutes",
}
DAY_SNAPSHOT = {                     # one reading per day → newest wins
    "resting_heart_rate", "vo2_max", "wrist_temperature",
}
DAY_OVERRIDE = DAY_SUM | DAY_SNAPSHOT


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> str | None:
    """Normalize HAE timestamps ('2026-01-21 14:30:00 +0000' or ISO) to
    'YYYY-MM-DD HH:MM:SS' local-naive strings, matching what the rest of
    the codebase stores in raw_vitals."""
    if not isinstance(v, str) or len(v) < 10:
        return None
    s = v.strip().replace("T", " ")
    s = re.sub(r"\s*[+-]\d{2}:?\d{2}$", "", s).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s + " 00:00:00"
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", s):
        return s[:19] if len(s) >= 19 else s + ":00"
    return None


def _ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS raw_vitals (
        metric_type TEXT, value REAL, timestamp TEXT)""")
    # Query-path index (2026-08-17). raw_vitals had NO index — every
    # consumer (Kobe's kcal SUM, weight lookups, Scientist reads, the
    # future Huberman trend queries) was a full table scan. Fine at
    # 37K rows; a drag at HAE's ~5K rows/day growth (~1.8M rows/yr).
    # (metric_type, timestamp) serves both filter shapes in use:
    # equality on metric_type always; timestamp range/prefix when the
    # query uses one. IF NOT EXISTS + running inside every ingest means
    # the live DB indexes itself on the next HAE sync — no manual
    # migration, idempotent forever.
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_raw_vitals_metric_ts
        ON raw_vitals (metric_type, timestamp)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS sleep_sessions (
        sleep_start TEXT PRIMARY KEY,
        sleep_end TEXT,
        asleep_s REAL, in_bed_s REAL,
        core_s REAL, deep_s REAL, rem_s REAL, awake_s REAL,
        source TEXT, raw_json TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS workouts_hk (
        workout_id TEXT PRIMARY KEY,
        name TEXT, start TEXT, end TEXT, duration_s REAL,
        active_kcal REAL, avg_hr REAL, max_hr REAL,
        distance_km REAL, raw_json TEXT)""")


def _write_series(cur: sqlite3.Cursor, metric: str,
                  points: list[tuple[str, float]]) -> int:
    """Idempotent scalar series write honoring legacy semantics."""
    if not points:
        return 0
    if metric in SINGLE_RECORD:
        ts, val = max(points)                       # newest wins
        cur.execute("DELETE FROM raw_vitals WHERE metric_type = ?", (metric,))
        cur.execute("INSERT INTO raw_vitals (metric_type, value, timestamp)"
                    " VALUES (?, ?, ?)", (metric, val, ts))
        return 1
    if metric in DAY_OVERRIDE:
        # One row per day. SUM metrics accumulate the day's samples;
        # SNAPSHOT metrics take the day's newest reading.
        by_day: dict[str, tuple[str, float]] = {}
        if metric in DAY_SUM:
            totals: dict[str, float] = {}
            last_ts: dict[str, str] = {}
            for ts, val in sorted(points):
                day = ts[:10]
                totals[day] = totals.get(day, 0.0) + val
                last_ts[day] = ts
            by_day = {d: (last_ts[d], t) for d, t in totals.items()}
        else:
            for ts, val in sorted(points):
                by_day[ts[:10]] = (ts, val)
        for day, (ts, val) in by_day.items():
            if metric in DAY_SUM:
                # Guard against a PARTIAL day payload ("since last sync"
                # sends only the newest samples) silently overwriting a
                # complete day with a smaller number. These metrics only
                # grow within a day, so the larger value is the truer
                # one; a full re-send still converges upward.
                prior = cur.execute(
                    "SELECT MAX(value) FROM raw_vitals WHERE "
                    "metric_type = ? AND timestamp LIKE ?",
                    (metric, f"{day}%")).fetchone()[0]
                if prior is not None and prior > val:
                    val = prior
            cur.execute("DELETE FROM raw_vitals WHERE metric_type = ? "
                        "AND timestamp LIKE ?", (metric, f"{day}%"))
            cur.execute("INSERT INTO raw_vitals (metric_type, value, "
                        "timestamp) VALUES (?, ?, ?)", (metric, val, ts))
        return len(by_day)
    # Point series (HR, HRV, respiratory, steps…): replace-by-timestamp.
    n = 0
    for ts, val in points:
        cur.execute("DELETE FROM raw_vitals WHERE metric_type = ? "
                    "AND timestamp = ?", (metric, ts))
        cur.execute("INSERT INTO raw_vitals (metric_type, value, timestamp)"
                    " VALUES (?, ?, ?)", (metric, val, ts))
        n += 1
    return n


def _sleep_seconds(rec: dict, *keys: str) -> float | None:
    """HAE reports sleep phases in HOURS in some versions and SECONDS in
    others. Normalize to seconds: values ≤ 24 are read as hours."""
    for k in keys:
        v = _num(rec.get(k))
        if v is None:
            continue
        return v * 3600.0 if v <= 24 else v
    return None


def ingest_payload(payload: dict, con: sqlite3.Connection) -> dict:
    """Ingest one HAE payload. Returns a summary dict (counts + skips).
    Commits on success; never raises on bad records."""
    cur = con.cursor()
    _ensure_schema(cur)
    summary = {"points": 0, "sleep_sessions": 0, "workouts": 0,
               "skipped": 0, "metrics_seen": []}

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return summary

    for metric in data.get("metrics") or []:
        if not isinstance(metric, dict):
            summary["skipped"] += 1
            continue
        name = _norm_name(str(metric.get("name", "")))
        canonical = METRIC_MAP.get(name)
        rows = metric.get("data") or []

        if canonical == "__sleep__":
            for rec in rows:
                if not isinstance(rec, dict):
                    summary["skipped"] += 1
                    continue
                start = _ts(rec.get("sleepStart") or rec.get("startDate")
                            or rec.get("date"))
                end = _ts(rec.get("sleepEnd") or rec.get("endDate"))
                if not start:
                    summary["skipped"] += 1
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO sleep_sessions (sleep_start, "
                    "sleep_end, asleep_s, in_bed_s, core_s, deep_s, rem_s, "
                    "awake_s, source, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (start, end,
                     _sleep_seconds(rec, "asleep", "totalSleep"),
                     _sleep_seconds(rec, "inBed"),
                     _sleep_seconds(rec, "core"),
                     _sleep_seconds(rec, "deep"),
                     _sleep_seconds(rec, "rem"),
                     _sleep_seconds(rec, "awake"),
                     str(rec.get("source", ""))[:60],
                     json.dumps(rec)[:4000]))
                summary["sleep_sessions"] += 1
            summary["metrics_seen"].append("sleep_analysis")
            continue

        if canonical is None:
            # Unknown metric: store under its normalized HAE name rather
            # than dropping data on the floor — Huberman may want it.
            canonical = name or "unknown"

        points: list[tuple[str, float]] = []
        hr_min: list[tuple[str, float]] = []
        hr_max: list[tuple[str, float]] = []
        for rec in rows:
            if not isinstance(rec, dict):
                summary["skipped"] += 1
                continue
            ts = _ts(rec.get("date"))
            if ts is None:
                summary["skipped"] += 1
                continue
            if "Avg" in rec or "Min" in rec or "Max" in rec:
                avg, mn, mx = (_num(rec.get("Avg")), _num(rec.get("Min")),
                               _num(rec.get("Max")))
                if avg is not None:
                    points.append((ts, avg))
                if mn is not None:
                    hr_min.append((ts, mn))
                if mx is not None:
                    hr_max.append((ts, mx))
            else:
                qty = _num(rec.get("qty"))
                if qty is None:
                    summary["skipped"] += 1
                    continue
                points.append((ts, qty))

        summary["points"] += _write_series(cur, canonical, points)
        if hr_min:
            summary["points"] += _write_series(cur, f"{canonical}_min", hr_min)
        if hr_max:
            summary["points"] += _write_series(cur, f"{canonical}_max", hr_max)
        summary["metrics_seen"].append(canonical)

    for w in data.get("workouts") or []:
        if not isinstance(w, dict):
            summary["skipped"] += 1
            continue
        start = _ts(w.get("start"))
        if not start:
            summary["skipped"] += 1
            continue
        wid = str(w.get("id") or f"{w.get('name', 'workout')}-{start}")

        def _qty(field: str) -> float | None:
            v = w.get(field)
            if isinstance(v, dict):
                return _num(v.get("qty"))
            return _num(v)

        cur.execute(
            "INSERT OR REPLACE INTO workouts_hk (workout_id, name, start, "
            "end, duration_s, active_kcal, avg_hr, max_hr, distance_km, "
            "raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (wid, str(w.get("name", ""))[:60], start, _ts(w.get("end")),
             _num(w.get("duration")), _qty("activeEnergyBurned"),
             _qty("avgHeartRate"), _qty("maxHeartRate"), _qty("distance"),
             json.dumps({k: v for k, v in w.items()
                         if k not in ("route", "heartRateData")})[:4000]))
        summary["workouts"] += 1

    con.commit()
    return summary
