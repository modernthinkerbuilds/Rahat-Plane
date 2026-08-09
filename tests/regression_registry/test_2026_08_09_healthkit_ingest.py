"""Feature pin (2026-08-09) — HealthKit ingestion for Huberman's substrate.

THE GAP (owner review request): the old vitals listener ingested TWO
metrics (weight, active calories) from a fragile Shortcut. The Huberman
analyses the owner wants (minute-level HR phases, HRV series, sleep
stages, respiratory rate, NAPS, workouts) need ~10 metric families.
bridges/healthkit ingests the Health Auto Export REST payload.

THE PINS.
  * Idempotency — automations re-send overlapping windows ("Since Last
    Sync" + full-previous-day). Replaying the same payload converges;
    it never duplicates.
  * Legacy semantics preserved EXACTLY (Kobe's burn math depends on
    them): weight keeps one record ever; active_calories overrides
    per-day.
  * Heart-rate Min/Avg/Max points fan out to heart_rate /
    heart_rate_min / heart_rate_max series.
  * Sleep sessions are per-SESSION rows — two on one calendar day means
    a nap was captured (the 2026-08-03 correction: 'I napped 2h both
    days' and the analysis had missed it).
  * Phase units normalize (HAE sends hours in some versions, seconds in
    others).
  * A malformed record is skipped and counted, never rejects the batch.
  * Unknown metrics are stored under their normalized name, not dropped.
"""
from __future__ import annotations

import sqlite3

import pytest

from bridges.healthkit.ingest import ingest_payload


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _rows(con, metric):
    return con.execute(
        "SELECT timestamp, value FROM raw_vitals WHERE metric_type = ? "
        "ORDER BY timestamp", (metric,)).fetchall()


def _payload():
    return {"data": {"metrics": [
        {"name": "heart_rate", "units": "bpm", "data": [
            {"date": "2026-08-08 14:30:00 +0000", "Min": 70, "Avg": 86,
             "Max": 107},
            {"date": "2026-08-08 14:31:00 +0000", "Min": 84, "Avg": 92,
             "Max": 96},
        ]},
        {"name": "heart_rate_variability", "units": "ms", "data": [
            {"date": "2026-08-08 03:00:00 +0000", "qty": 95},
        ]},
        {"name": "respiratory_rate", "units": "count/min", "data": [
            {"date": "2026-08-08 03:00:00 +0000", "qty": 12.5},
        ]},
        {"name": "resting_heart_rate", "units": "bpm", "data": [
            {"date": "2026-08-08 00:00:00 +0000", "qty": 59},
        ]},
        {"name": "active_energy", "units": "kcal", "data": [
            {"date": "2026-08-08 23:59:00 +0000", "qty": 372},
        ]},
        {"name": "weight_body_mass", "units": "lb", "data": [
            {"date": "2026-08-08 07:00:00 +0000", "qty": 195.2},
        ]},
        {"name": "sleep_analysis", "units": "hr", "data": [
            {"sleepStart": "2026-08-07 23:55:00 +0000",
             "sleepEnd": "2026-08-08 06:15:00 +0000",
             "asleep": 6.0, "core": 3.6, "deep": 0.66, "rem": 1.71,
             "awake": 0.3, "source": "Watch"},
            # The NAP — its own session, same calendar day.
            {"sleepStart": "2026-08-08 14:26:00 +0000",
             "sleepEnd": "2026-08-08 16:08:00 +0000",
             "asleep": 1.7, "core": 1.4, "deep": 0.2, "rem": 0.1,
             "awake": 0.0, "source": "Watch"},
        ]},
    ], "workouts": [
        {"id": "w-123", "name": "Running",
         "start": "2026-08-08 10:00:00 +0000",
         "end": "2026-08-08 12:00:00 +0000", "duration": 7200,
         "activeEnergyBurned": {"qty": 980, "units": "kcal"},
         "avgHeartRate": {"qty": 144.7, "units": "bpm"},
         "maxHeartRate": {"qty": 163, "units": "bpm"},
         "distance": {"qty": 17.9, "units": "km"}},
    ]}}


def test_metric_families_land(con):
    s = ingest_payload(_payload(), con)
    assert s["skipped"] == 0
    assert _rows(con, "heart_rate") == [("2026-08-08 14:30:00", 86.0),
                                        ("2026-08-08 14:31:00", 92.0)]
    assert _rows(con, "heart_rate_min")[0][1] == 70.0
    assert _rows(con, "heart_rate_max")[1][1] == 96.0
    assert _rows(con, "hrv") == [("2026-08-08 03:00:00", 95.0)]
    assert _rows(con, "respiratory_rate")[0][1] == 12.5
    assert _rows(con, "resting_heart_rate")[0][1] == 59.0


def test_replay_is_idempotent(con):
    ingest_payload(_payload(), con)
    ingest_payload(_payload(), con)                 # the re-sent window
    assert len(_rows(con, "heart_rate")) == 2       # not 4
    assert len(_rows(con, "weight")) == 1
    assert con.execute("SELECT COUNT(*) FROM sleep_sessions").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM workouts_hk").fetchone()[0] == 1


def test_legacy_weight_single_record(con):
    ingest_payload(_payload(), con)
    later = {"data": {"metrics": [{"name": "weight", "units": "lb", "data": [
        {"date": "2026-08-09 07:00:00 +0000", "qty": 194.6}]}]}}
    ingest_payload(later, con)
    rows = _rows(con, "weight")
    assert rows == [("2026-08-09 07:00:00", 194.6)]  # ONE record, newest


def test_legacy_active_calories_day_override(con):
    ingest_payload(_payload(), con)
    revised = {"data": {"metrics": [{"name": "active_energy",
                                     "units": "kcal", "data": [
        {"date": "2026-08-08 23:59:59 +0000", "qty": 405}]}]}}
    ingest_payload(revised, con)
    rows = _rows(con, "active_calories")
    assert len(rows) == 1 and rows[0][1] == 405.0   # day replaced


def test_naps_are_separate_sessions(con):
    ingest_payload(_payload(), con)
    sessions = con.execute(
        "SELECT sleep_start, asleep_s FROM sleep_sessions "
        "ORDER BY sleep_start").fetchall()
    assert len(sessions) == 2
    night, nap = sessions
    assert night[1] == pytest.approx(6.0 * 3600)    # hours → seconds
    assert nap[0].startswith("2026-08-08 14:26")
    assert nap[1] == pytest.approx(1.7 * 3600)


def test_sleep_seconds_passthrough(con):
    """Versions that already send seconds must not be multiplied."""
    p = {"data": {"metrics": [{"name": "sleep_analysis", "data": [
        {"sleepStart": "2026-08-08 23:00:00 +0000",
         "sleepEnd": "2026-08-09 05:00:00 +0000", "asleep": 21600}]}]}}
    ingest_payload(p, con)
    v = con.execute("SELECT asleep_s FROM sleep_sessions").fetchone()[0]
    assert v == 21600


def test_workout_fields(con):
    ingest_payload(_payload(), con)
    w = con.execute("SELECT name, duration_s, active_kcal, avg_hr, "
                    "distance_km FROM workouts_hk").fetchone()
    assert w == ("Running", 7200.0, 980.0, 144.7, 17.9)


def test_malformed_records_skip_not_reject(con):
    p = _payload()
    p["data"]["metrics"][0]["data"].append({"date": None})
    p["data"]["metrics"][0]["data"].append("garbage")
    s = ingest_payload(p, con)
    assert s["skipped"] == 2
    assert len(_rows(con, "heart_rate")) == 2       # good points landed


def test_unknown_metric_is_kept_not_dropped(con):
    p = {"data": {"metrics": [{"name": "Blood Alcohol Content",
                               "units": "%", "data": [
        {"date": "2026-08-08 20:00:00 +0000", "qty": 0.0}]}]}}
    ingest_payload(p, con)
    assert len(_rows(con, "blood_alcohol_content")) == 1


def test_empty_or_junk_payloads_are_safe(con):
    assert ingest_payload({}, con)["points"] == 0
    assert ingest_payload({"data": "nope"}, con)["points"] == 0
    assert ingest_payload({"data": {"metrics": ["x", 1]}},
                          con)["skipped"] == 2
