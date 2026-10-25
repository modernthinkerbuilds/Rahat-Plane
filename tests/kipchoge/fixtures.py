"""Fixture loading for the Kipchoge tests: a fixture (a dict of table →
rows, as scripts/kipchoge_fixture.py writes it, or as a test builds it)
goes into a sandbox SQLite file and the engine reads it through its
real reads. Synthetic builders for the public tests live here too; the
private fixtures live in vault/kipchoge/fixtures/ and never in the
repo."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from bridges.healthkit import workouts as hkw
from bridges.healthkit.ingest import _ensure_schema


def sandbox_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    _ensure_schema(cur)
    hkw.ensure_schema(cur)
    con.commit()
    return con


def load_fixture(con: sqlite3.Connection, fixture: dict) -> str:
    """Insert every table's rows; returns the workout id."""
    for table, rows in (fixture.get("tables") or {}).items():
        for row in rows:
            cols = list(row.keys())
            con.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                        [row[c] for c in cols])
    con.commit()
    return fixture.get("workout_id") or fixture["tables"]["workouts_hk"][0]["workout_id"]


def load_fixture_file(con: sqlite3.Connection, path: Path) -> str:
    return load_fixture(con, json.loads(Path(path).read_text(encoding="utf-8")))


# ── synthetic runs (public tests) ────────────────────────────────────
def synthetic_run(uid: str, start: datetime, minutes: int, *, km: float, hr_first: float, hr_second: float,
                  ramp_min: float = 9.0, pauses: list[tuple[float, float]] | None = None, offset_min: int = -420,
                  route: bool = True, series: bool = True, strides: int = 0, climb_m: float = 0.0,
                  sleep_h: float | None = 7.0, name: str = "Outdoor Run", kcal: float | None = None,
                  temperature_f: float | None = None, humidity: float | None = None) -> dict:
    """A run as the store holds it: the summary row, a 5-s heart-rate
    series that ramps to hr_first, holds it for the first half and
    hr_second for the second, a distance series at a steady pace, a
    route along a straight line (with an optional climb in the middle),
    pauses as gaps in every series, strides after the main block, and
    the night's sleep. All in UTC + offset, like the bridge writes."""
    pauses = pauses or []
    total_s = minutes * 60
    end = start + timedelta(seconds=total_s)
    base_utc = start - timedelta(minutes=offset_min)

    def utc(t: float) -> str:
        return (base_utc + timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S")

    def local(t: float) -> str:
        return (start + timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S")

    def in_pause(t: float) -> bool:
        return any(a <= t < b for a, b in pauses)

    paused_total = sum(b - a for a, b in pauses)
    moving_s = total_s - paused_total
    speed_mps = km * 1000.0 / moving_s
    stride_start = total_s - 60 * (strides * 1) - 60 if strides else None
    rows: dict[str, list[dict]] = {"workouts_hk": [{
        "workout_id": uid, "name": name, "start": local(0), "end": local(total_s), "duration_s": float(total_s),
        "active_kcal": kcal if kcal is not None else round(90.0 * km, 1), "avg_hr": (hr_first + hr_second) / 2.0,
        "max_hr": max(hr_first, hr_second) + 8.0, "distance_km": km, "raw_json": "{}"}],
        "workout_series": [], "workout_route": [], "workout_meta": [], "raw_vitals": [], "sleep_sessions": []}
    # heart rate every 5 s
    for t in range(0, total_s, 5):
        if in_pause(t):
            continue
        if t < ramp_min * 60:
            hr = 100.0 + (hr_first - 100.0) * (t / (ramp_min * 60.0))
        elif t < total_s / 2:
            hr = hr_first
        else:
            hr = hr_second
        if stride_start is not None and t >= stride_start:
            hr = hr_second + 12.0
        hr += 1.5 * math.sin(t / 37.0)
        if series:
            rows["workout_series"].append({"workout_id": uid, "kind": "hr", "ts_utc": utc(t), "offset_min": offset_min,
                                           "min": round(hr - 1, 1), "avg": round(hr, 1), "max": round(hr + 1, 1), "units": "bpm"})
        else:
            rows["raw_vitals"].append({"metric_type": "heart_rate", "value": round(hr, 1), "timestamp": local(t)})
    # distance per minute and a route point every 5 s
    dist = 0.0
    lat, lon = 36.65, -120.65
    for t in range(0, total_s, 5):
        if in_pause(t):
            if route:
                rows["workout_route"].append({"workout_id": uid, "seq": len(rows["workout_route"]), "ts_utc": utc(t),
                                              "offset_min": offset_min, "lat": lat, "lon": lon, "alt_m": 30.0,
                                              "speed_mps": 0.0, "h_acc_m": 4.0, "v_acc_m": 6.0})
            continue
        v = speed_mps * (1.35 if (stride_start is not None and t >= stride_start and ((t - stride_start) // 60) % 2 == 0
                                  and (t - stride_start) % 60 < 20) else 1.0)
        dist += v * 5.0 / 1000.0
        lat += v * 5.0 / 111_320.0
        frac = t / total_s
        alt = 30.0 + (climb_m * math.sin(math.pi * frac) if climb_m else 0.0)
        if route:
            rows["workout_route"].append({"workout_id": uid, "seq": len(rows["workout_route"]), "ts_utc": utc(t),
                                          "offset_min": offset_min, "lat": round(lat, 6), "lon": lon, "alt_m": round(alt, 2),
                                          "speed_mps": round(v, 3), "h_acc_m": 4.0, "v_acc_m": 6.0})
        if series and t % 60 == 0:
            rows["workout_series"].append({"workout_id": uid, "kind": "distance", "ts_utc": utc(t), "offset_min": offset_min,
                                           "min": None, "avg": round(v * 60.0 / 1000.0, 4), "max": None, "units": "km"})
        if t % 60 == 0:
            rows["raw_vitals"].append({"metric_type": "steps", "value": 165.0, "timestamp": local(t)})
    if temperature_f is not None:
        rows["workout_meta"].append({"workout_id": uid, "key": "temperature", "value": temperature_f, "text": None, "units": "degF"})
    if humidity is not None:
        rows["workout_meta"].append({"workout_id": uid, "key": "humidity", "value": humidity, "text": None, "units": "%"})
    if sleep_h is not None:
        night_end = start.replace(hour=6, minute=30, second=0)
        if night_end > start:
            night_end -= timedelta(days=1)
        rows["sleep_sessions"].append({"sleep_start": (night_end - timedelta(hours=sleep_h + 0.4)).strftime("%Y-%m-%d %H:%M:%S"),
                                       "sleep_end": night_end.strftime("%Y-%m-%d %H:%M:%S"), "asleep_s": sleep_h * 3600.0,
                                       "in_bed_s": (sleep_h + 0.4) * 3600.0, "core_s": None, "deep_s": None, "rem_s": None,
                                       "awake_s": 0.4 * 3600.0, "source": "Watch"})
    return {"workout_id": uid, "tables": rows}


ZONES = {"max": 190.0, "resting": 58.0, "lt1_lo": 150.0, "lt1_hi": 152.0, "zone2_lo": 140.0, "zone2_hi": 145.0,
         "sweet_lo": 142.0, "sweet_hi": 145.0, "cap": 145.0, "reference": 144.0}
"""An invented athlete for the public tests (a Zone 2 band of 140–145,
cap 145, reference 144): the shape of a zones dict, nobody's zones."""
