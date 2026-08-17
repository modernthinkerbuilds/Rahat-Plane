"""Bug pin (2026-08-16, LIVE DATA LOSS) — daily metrics that ACCUMULATE
must be summed across the day, never "newest sample wins".

THE INCIDENT. Minutes after the iPhone Shortcuts → Health Auto Export
cutover, the owner asked Kobe for the day's calories and got **0**.
Kobe was right; the data was wrecked.

HAE exports at MINUTE grouping, so active_calories arrives as hundreds
of tiny per-minute INCREMENTS (0.2 kcal each). The retired Shortcut had
sent ONE aggregate row per day, so the ingest's day-override rule was
written as "newest sample of the day wins" — which, fed increments,
kept the final minute of the day (0.0 kcal) and DELETED the true
1,152 kcal total. Every day back to 2026-08-11 was flattened to a
sub-1-kcal value the same way.

Recovery was possible only because Apple Health is the source of truth:
with this fix in place, HAE's "Previous 7 Days" range re-sends the
window and the day totals rebuild.

THE PINS.
  * A day of per-minute increments SUMS to the day total (the exact
    failure: 1,152 kcal must not become 0.0).
  * Re-sending the same day is idempotent — it converges, never
    doubles (Kobe SUMs over the day, so a day must hold exactly ONE
    row).
  * A PARTIAL day payload never shrinks a complete day (these metrics
    only grow within a day).
  * A legacy single-row day total still lands unchanged — the retired
    Shortcut's shape keeps working through the grace period.
  * True once-a-day READINGS (resting_heart_rate, vo2_max) still take
    newest-wins: summing them would be nonsense (three RHR readings of
    52 are not 156 bpm).
  * The end-to-end contract: after ingest, Kobe's actual query
    (SUM(value) WHERE metric_type='active_calories' AND day) returns
    the real total.
"""
from __future__ import annotations

import sqlite3

import pytest

from bridges.healthkit.ingest import (
    DAY_SNAPSHOT, DAY_SUM, ingest_payload,
)


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    c = sqlite3.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _payload(name: str, points: list[tuple[str, float]]) -> dict:
    return {"data": {"metrics": [
        {"name": name, "units": "kcal",
         "data": [{"date": ts, "qty": v} for ts, v in points]}]}}


def _minute_increments(day: str, per_minute: float, n: int
                       ) -> list[tuple[str, float]]:
    """n per-minute samples — how HAE actually exports active energy."""
    return [(f"{day} {h:02d}:{m:02d}:00", per_minute)
            for h in range(n // 60) for m in range(60)]


def _kobe_kcal(con: sqlite3.Connection, day: str) -> float:
    """Kobe's REAL query (core/kobe_bridge.py)."""
    return con.execute(
        "SELECT COALESCE(SUM(value),0) FROM raw_vitals WHERE "
        "metric_type='active_calories' AND substr(timestamp,1,10)=?",
        (day,)).fetchone()[0]


# ─────────────── the incident: increments must sum ───────────────
def test_per_minute_increments_sum_to_the_day_total(con):
    pts = _minute_increments("2026-08-16", 2.0, 600)      # 600 × 2 = 1200
    ingest_payload(_payload("active_energy", pts), con)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(1200.0)


def test_the_exact_failure_zero_calories_cannot_recur(con):
    """Increments ending on a 0.0 minute — the live shape that produced
    'you burned 0 calories today'."""
    pts = _minute_increments("2026-08-16", 1.5, 300)       # 450 kcal
    pts.append(("2026-08-16 20:24:00", 0.0))               # last minute idle
    ingest_payload(_payload("active_energy", pts), con)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(450.0)


def test_one_row_per_day_so_kobes_sum_cannot_double_count(con):
    ingest_payload(_payload(
        "active_energy", _minute_increments("2026-08-16", 1.0, 120)), con)
    rows = con.execute(
        "SELECT COUNT(*) FROM raw_vitals WHERE metric_type='active_calories'"
        " AND substr(timestamp,1,10)='2026-08-16'").fetchone()[0]
    assert rows == 1


def test_resend_is_idempotent_not_additive(con):
    pts = _minute_increments("2026-08-16", 2.0, 300)       # 600 kcal
    for _ in range(3):                                     # 7-day re-sends
        ingest_payload(_payload("active_energy", pts), con)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(600.0)


def test_partial_day_payload_never_shrinks_a_complete_day(con):
    full = _minute_increments("2026-08-16", 2.0, 600)      # 1200 kcal
    ingest_payload(_payload("active_energy", full), con)
    partial = [("2026-08-16 21:00:00", 5.0)]               # a late sliver
    ingest_payload(_payload("active_energy", partial), con)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(1200.0)


def test_separate_days_stay_separate(con):
    pts = (_minute_increments("2026-08-15", 1.0, 120)
           + _minute_increments("2026-08-16", 3.0, 120))
    ingest_payload(_payload("active_energy", pts), con)
    assert _kobe_kcal(con, "2026-08-15") == pytest.approx(120.0)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(360.0)


# ─────────────── legacy shape still works ───────────────
def test_legacy_single_day_total_row_is_preserved(con):
    """The retired Shortcut's shape: ONE aggregate per day."""
    ingest_payload(_payload(
        "active_energy", [("2026-08-16 23:59:00", 1152.391)]), con)
    assert _kobe_kcal(con, "2026-08-16") == pytest.approx(1152.391)


# ─────────────── snapshots must NOT be summed ───────────────
def test_resting_heart_rate_takes_newest_not_sum(con):
    ingest_payload(_payload("resting_heart_rate", [
        ("2026-08-16 01:00:00", 52.0),
        ("2026-08-16 06:00:00", 54.0)]), con)
    val = con.execute(
        "SELECT value FROM raw_vitals WHERE metric_type='resting_heart_rate'"
        " AND substr(timestamp,1,10)='2026-08-16'").fetchone()[0]
    assert val == 54.0, "RHR summed — 52+54 is not a heart rate"


def test_the_two_families_are_declared_and_disjoint():
    assert "active_calories" in DAY_SUM
    assert "resting_heart_rate" in DAY_SNAPSHOT
    assert not (DAY_SUM & DAY_SNAPSHOT), "a metric cannot be both"
