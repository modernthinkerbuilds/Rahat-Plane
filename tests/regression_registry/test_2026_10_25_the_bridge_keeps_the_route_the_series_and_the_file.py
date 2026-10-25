"""Feature pin (2026-10-25, Kipchoge S0): the bridge keeps what it dropped.

THE GAP (PRD v0.3 §6.1): the bridge stored one summary row per workout
and 4,000 characters of its JSON, dropping the route and the heart-rate
series at ingest (ingest.py line 325 of the 2026-08-09 shape). No
elevation, no grade-adjusted pace, no route matching, no per-split
metadata, no recovery curve: the coach the thread wanted could not be
built on it.

THE PINS (bridges/healthkit/workouts.py):
  * The whole workout object lands gzipped at `<files_dir>/<uuid>.json.gz`
    with an index row (bytes, sha256); an identical re-send writes
    nothing and keeps the first received_at.
  * The route lands in `workout_route`, one row per point, in UTC with
    the sample's own offset; v2 and v1 entry names both read.
  * The series land in `workout_series` (hr with min/avg/max, recovery,
    distance, energy, step count); the scalars in `workout_meta`.
  * Replays converge: the same payload twice leaves the same rows.
  * Keys are the HealthKit UUID, so two runs on one day never collide.
  * A second source's copy of the same session (start within 60 s,
    distance within 2%) is linked `duplicate_of` the richer record.
  * A session split by a stop (a run resumed under 15 minutes later,
    starting where the first part ended) is linked `merged_into` the
    first part; both UUIDs stay; a third sync re-derives the same link.
  * A run across the November 1 DST change keeps its instants in order.
  * The summary row and its 4,000-character column stay for the readers
    that exist; a distance sent in miles is stored in km.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from bridges.healthkit import workouts as hkw
from bridges.healthkit.ingest import ingest_payload

NOW = datetime(2026, 10, 25, 20, 0)


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _stamp(dt: datetime, offset: str = "-0700") -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S ") + offset


def _run(uid: str, start: datetime, minutes: int, *, km: float = 10.0, lat0: float = 36.65, lon0: float = -120.65,
         offset: str = "-0700", with_route: bool = True, with_series: bool = True, name: str = "Outdoor Run") -> dict:
    """A synthetic HAE v2 workout: a point every 5 s on the route, a heart-
    rate entry every 5 s, distance and energy every minute."""
    end = start + timedelta(minutes=minutes)
    w = {"id": uid, "name": name, "start": _stamp(start, offset), "end": _stamp(end, offset),
         "duration": minutes * 60.0, "distance": {"qty": km, "units": "km"},
         "activeEnergyBurned": {"qty": 90.0 * km, "units": "kcal"},
         "avgHeartRate": {"qty": 146.0, "units": "bpm"}, "maxHeartRate": {"qty": 160.0, "units": "bpm"},
         "temperature": {"qty": 61.0, "units": "degF"}, "humidity": {"qty": 55.0, "units": "%"},
         "elevationUp": {"qty": 40.0, "units": "m"}, "elevationDown": {"qty": 41.0, "units": "m"},
         "intensity": {"qty": 9.5, "units": "MET"}, "location": "Outdoor", "isIndoor": False,
         "metadata": {"HKWeatherHumidity": "55 %"}}
    n = minutes * 12
    if with_route:
        w["route"] = [{"latitude": lat0 + 0.00002 * i, "longitude": lon0 + 0.00001 * i, "altitude": 30.0 + (i % 40),
                       "course": 90.0, "courseAccuracy": 5.0, "horizontalAccuracy": 4.0, "verticalAccuracy": 6.0,
                       "timestamp": _stamp(start + timedelta(seconds=5 * i), offset), "speed": 2.3, "speedAccuracy": 0.3}
                      for i in range(n)]
    if with_series:
        w["heartRateData"] = [{"date": _stamp(start + timedelta(seconds=5 * i), offset), "Min": 140 + (i % 5),
                               "Avg": 142 + (i % 5), "Max": 144 + (i % 5), "units": "bpm"} for i in range(n)]
        w["heartRateRecovery"] = [{"date": _stamp(end + timedelta(seconds=k), offset), "Min": 150 - 10 * k,
                                   "Avg": 152 - 10 * k, "Max": 154 - 10 * k, "units": "bpm"} for k in range(3)]
        w["walkingAndRunningDistance"] = [{"date": _stamp(start + timedelta(minutes=m), offset), "qty": km / minutes,
                                           "units": "km", "source": "Watch"} for m in range(minutes)]
        w["activeEnergy"] = [{"date": _stamp(start + timedelta(minutes=m), offset), "qty": 90.0 * km / minutes,
                              "units": "kcal"} for m in range(minutes)]
        w["stepCount"] = [{"date": _stamp(start + timedelta(minutes=m), offset), "qty": 165, "units": "count"}
                          for m in range(minutes)]
    return w


def _payload(*ws: dict) -> dict:
    return {"data": {"metrics": [], "workouts": list(ws)}}


def _count(con, table: str, wid: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table} WHERE workout_id = ?", (wid,)).fetchone()[0]


# ── the file, the route, the series ──────────────────────────────────
def test_the_whole_object_lands_gzipped_and_a_resend_writes_nothing(con, tmp_path):
    w = _run("UUID-A", datetime(2026, 10, 18, 11, 3, 35), 73)
    s1 = ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW)
    assert s1["workouts"] == 1 and s1["files"] == 1
    row = con.execute("SELECT path, bytes, sha256, received_at FROM hk_workout_file WHERE workout_id = 'UUID-A'").fetchone()
    assert row[0].endswith("UUID-A.json.gz") and row[1] > 10_000 and len(row[2]) == 64
    assert row[3] == "2026-10-25 20:00:00"
    with gzip.open(row[0], "rb") as fh:
        back = json.loads(fh.read())
    assert back["id"] == "UUID-A" and len(back["route"]) == 73 * 12 and "heartRateData" in back
    s2 = ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW + timedelta(hours=1))
    assert s2["files"] == 0, "the same content writes nothing"
    assert con.execute("SELECT received_at FROM hk_workout_file WHERE workout_id = 'UUID-A'").fetchone()[0] == "2026-10-25 20:00:00"
    assert hkw.load_file(row[0])["duration"] == 73 * 60.0


def test_the_route_and_the_series_land_in_utc_with_the_offset(con, tmp_path):
    w = _run("UUID-A", datetime(2026, 10, 18, 11, 3, 35), 73)
    s = ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW)
    assert s["route_points"] == 73 * 12 and s["series_rows"] == 73 * 12 + 3 + 73 * 3
    first = con.execute("SELECT seq, ts_utc, offset_min, lat, lon, alt_m, speed_mps, h_acc_m FROM workout_route "
                        "WHERE workout_id = 'UUID-A' ORDER BY seq LIMIT 1").fetchone()
    assert first == (0, "2026-10-18 18:03:35", -420, 36.65, -120.65, 30.0, 2.3, 4.0)
    assert hkw.local_clock(first[1], first[2]) == datetime(2026, 10, 18, 11, 3, 35)
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM workout_series WHERE workout_id = 'UUID-A' GROUP BY kind"))
    assert kinds == {"hr": 876, "hr_recovery": 3, "distance": 73, "energy": 73, "step_count": 73}
    hr = con.execute("SELECT ts_utc, min, avg, max, units FROM workout_series WHERE workout_id='UUID-A' AND kind='hr' "
                     "ORDER BY ts_utc LIMIT 1").fetchone()
    assert hr == ("2026-10-18 18:03:35", 140.0, 142.0, 144.0, "bpm")
    meta = dict(con.execute("SELECT key, value FROM workout_meta WHERE workout_id = 'UUID-A'"))
    assert meta["temperature"] == 61.0 and meta["humidity"] == 55.0 and meta["elevation_up"] == 40.0
    assert meta["is_indoor"] == 0.0 and meta["intensity"] == 9.5
    assert con.execute("SELECT text FROM workout_meta WHERE workout_id='UUID-A' AND key='location'").fetchone()[0] == "Outdoor"
    # the summary row and its 4,000-character column are as they were
    summary = con.execute("SELECT name, distance_km, length(raw_json) FROM workouts_hk WHERE workout_id='UUID-A'").fetchone()
    assert summary[0] == "Outdoor Run" and summary[1] == 10.0 and summary[2] <= 4000


def test_replays_converge_and_two_runs_on_one_day_never_collide(con, tmp_path):
    a = _run("UUID-A", datetime(2026, 10, 18, 7, 0), 30, km=4.0)
    b = _run("UUID-B", datetime(2026, 10, 18, 18, 0), 40, km=6.0, lat0=37.30)
    for _ in range(3):
        ingest_payload(_payload(a, b), con, files_dir=tmp_path, now=NOW)
    assert _count(con, "workout_route", "UUID-A") == 30 * 12 and _count(con, "workout_route", "UUID-B") == 40 * 12
    assert _count(con, "workout_series", "UUID-A") == 30 * 12 + 3 + 90
    assert con.execute("SELECT COUNT(*) FROM workouts_hk").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM workout_link").fetchone()[0] == 0
    assert hkw.primary_ids(con.cursor()) == {"UUID-A", "UUID-B"}


def test_v1_route_names_and_miles_are_read(con, tmp_path):
    w = _run("UUID-V1", datetime(2026, 9, 6, 8, 0), 20, km=3.0, with_route=False)
    w["distance"] = {"qty": 1.864, "units": "mi"}
    w["route"] = [{"lat": 37.25, "lon": -121.95, "altitude": 31.0, "timestamp": "2026-09-06 08:00:00 -0700"},
                  {"lat": 37.2501, "lon": -121.9501, "altitude": 32.0, "timestamp": "2026-09-06 08:00:05 -0700"}]
    ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW)
    assert _count(con, "workout_route", "UUID-V1") == 2
    assert abs(con.execute("SELECT distance_km FROM workouts_hk WHERE workout_id='UUID-V1'").fetchone()[0] - 3.0) < 0.01


def test_a_malformed_route_point_or_series_entry_is_skipped_not_fatal(con, tmp_path):
    w = _run("UUID-M", datetime(2026, 10, 18, 11, 0), 10)
    w["route"].append("garbage")
    w["route"].append({"latitude": "x", "longitude": -120.6, "timestamp": "2026-10-18 11:10:00 -0700"})
    w["heartRateData"].append({"date": None, "Avg": 150})
    s = ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW)
    assert s["workouts"] == 1 and _count(con, "workout_route", "UUID-M") == 120
    assert _count(con, "workout_series", "UUID-M") == 120 + 3 + 30


# ── links ─────────────────────────────────────────────────────────────
def test_a_second_sources_copy_is_a_duplicate_of_the_richer_record(con, tmp_path):
    watch = _run("UUID-W", datetime(2026, 10, 18, 11, 3, 35), 73, km=10.15)
    other = _run("UUID-S", datetime(2026, 10, 18, 11, 4, 5), 73, km=10.0, with_series=False, with_route=False)
    s = ingest_payload(_payload(watch, other), con, files_dir=tmp_path, now=NOW)
    assert s["links"] == 1
    assert dict(con.execute("SELECT workout_id, other_id FROM workout_link WHERE kind='duplicate_of'")) == {"UUID-S": "UUID-W"}
    assert hkw.primary_ids(con.cursor()) == {"UUID-W"}
    ingest_payload(_payload(other, watch), con, files_dir=tmp_path, now=NOW + timedelta(days=1))
    assert con.execute("SELECT COUNT(*) FROM workout_link").fetchone()[0] == 1, "order and replay change nothing"
    # the same start but a different distance is another workout, not a duplicate
    far = _run("UUID-F", datetime(2026, 10, 18, 11, 4, 0), 30, km=4.0, with_route=False, with_series=False)
    ingest_payload(_payload(far), con, files_dir=tmp_path, now=NOW)
    assert "UUID-F" in hkw.primary_ids(con.cursor())


def test_a_run_split_by_a_stop_is_merged_into_its_first_part(con, tmp_path):
    first = _run("UUID-P1", datetime(2026, 10, 18, 11, 0), 30, km=4.2)
    end_lat = 37.25 + 0.00002 * (30 * 12 - 1)
    end_lon = -121.95 + 0.00001 * (30 * 12 - 1)
    second = _run("UUID-P2", datetime(2026, 10, 18, 11, 42), 40, km=5.6, lat0=end_lat + 0.0005, lon0=end_lon)
    s = ingest_payload(_payload(first, second), con, files_dir=tmp_path, now=NOW)
    assert s["links"] == 1
    assert dict(con.execute("SELECT workout_id, other_id FROM workout_link WHERE kind='merged_into'")) == {"UUID-P2": "UUID-P1"}
    assert con.execute("SELECT COUNT(*) FROM workouts_hk").fetchone()[0] == 2, "both UUIDs stay"
    assert hkw.primary_ids(con.cursor()) == {"UUID-P1"}
    before = con.execute("SELECT at FROM workout_link WHERE workout_id='UUID-P2'").fetchone()[0]
    ingest_payload(_payload(second), con, files_dir=tmp_path, now=NOW + timedelta(days=2))   # a re-sync
    assert con.execute("SELECT at, other_id FROM workout_link WHERE workout_id='UUID-P2'").fetchone() == (before, "UUID-P1")


def test_a_walk_after_a_run_and_a_run_from_elsewhere_are_not_merged(con, tmp_path):
    run = _run("UUID-R", datetime(2026, 10, 18, 11, 0), 30)
    walk = _run("UUID-K", datetime(2026, 10, 18, 11, 35), 20, name="Outdoor Walk", lat0=37.25 + 0.00002 * 359)
    elsewhere = _run("UUID-E", datetime(2026, 10, 18, 11, 40), 20, lat0=37.40)        # 16 km away
    ingest_payload(_payload(run, walk, elsewhere), con, files_dir=tmp_path, now=NOW)
    assert con.execute("SELECT COUNT(*) FROM workout_link").fetchone()[0] == 0


def test_two_runs_with_no_routes_merge_on_time_and_kind_alone(con, tmp_path):
    first = _run("UUID-N1", datetime(2026, 9, 6, 8, 0), 30, with_route=False)
    second = _run("UUID-N2", datetime(2026, 9, 6, 8, 38), 30, with_route=False)
    ingest_payload(_payload(first, second), con, files_dir=tmp_path, now=NOW)
    assert dict(con.execute("SELECT workout_id, other_id FROM workout_link")) == {"UUID-N2": "UUID-N1"}


# ── time ──────────────────────────────────────────────────────────────
def test_the_dst_night_keeps_its_instants_in_order():
    """Nov 1, 2026: 01:30 PDT (-0700) is followed by 01:15 PST (-0800). A
    run across it has strictly increasing UTC instants, and each sample
    keeps its own wall clock."""
    a = hkw.parse_hae_ts("2026-11-01 01:30:00 -0700")
    b = hkw.parse_hae_ts("2026-11-01 01:15:00 -0800")
    assert a == ("2026-11-01 08:30:00", -420, "2026-11-01 01:30:00")
    assert b == ("2026-11-01 09:15:00", -480, "2026-11-01 01:15:00")
    assert a[0] < b[0]
    assert hkw.local_clock(*a[:2]) == datetime(2026, 11, 1, 1, 30) and hkw.local_clock(*b[:2]) == datetime(2026, 11, 1, 1, 15)
    assert hkw.parse_hae_ts("2026-10-18T11:03:35Z") == ("2026-10-18 11:03:35", 0, "2026-10-18 11:03:35")
    assert hkw.parse_hae_ts("2026-10-18 11:03") == ("2026-10-18 11:03:00", 0, "2026-10-18 11:03:00")
    assert hkw.parse_hae_ts("not a time") is None and hkw.parse_hae_ts(None) is None


def test_a_run_across_the_dst_change_stores_both_offsets(con, tmp_path):
    w = _run("UUID-DST", datetime(2026, 11, 1, 1, 45), 30, with_series=False)
    # the Watch's clock falls back at 02:00 PDT: the second half of the route is stamped -0800
    for i, p in enumerate(w["route"]):
        t = datetime(2026, 11, 1, 1, 45) + timedelta(seconds=5 * i)
        if t >= datetime(2026, 11, 1, 2, 0):
            p["timestamp"] = _stamp(t - timedelta(hours=1), "-0800")
    ingest_payload(_payload(w), con, files_dir=tmp_path, now=NOW)
    rows = con.execute("SELECT ts_utc, offset_min FROM workout_route WHERE workout_id='UUID-DST' ORDER BY seq").fetchall()
    assert [r[0] for r in rows] == sorted(r[0] for r in rows) and len(set(r[0] for r in rows)) == len(rows)
    assert {r[1] for r in rows} == {-420, -480}
