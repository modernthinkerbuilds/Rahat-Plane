"""Regression: one pace verdict; behind-pace is never called "ahead" (2026-05-25).

Symptom (from the live transcript): the hourly pace check said the user
was ~515 kcal BEHIND, while the Tuesday morning brief said "Ahead of
pace … comfortable buffer" for the same week (only 482 of 6,000 burned,
with Monday's CrossFit missed).

Root cause: the brief's verdict came from `gap = remaining_to_goal -
remaining_planned` (a forward-CAPACITY check — "does the remaining plan
cover the goal"), a different question from the hourly pace-to-date
check. A plan can over-cover the goal (gap < 0) while the user is still
behind pace-to-date, especially after a missed day (Bug G).

Fix: `compute_week_recalibration` now also computes `behind_pace` from
the SAME prorated week formula the `/week` view uses
(`expected_week_burn_to_date`). When the user is behind pace-to-date the
"ahead of pace / comfortable buffer / on track" verdicts are replaced
with honest "behind pace-to-date" framing.

This test pins:
  1. The two week-pace formulas are literally the same function now.
  2. After a missed Monday with low week-to-date burn, the brief reports
     `behind_pace=True` and never says "ahead"/"comfortable buffer".
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _schema(con: sqlite3.Connection) -> None:
    con.executescript(
        "CREATE TABLE IF NOT EXISTS raw_vitals ("
        " metric_type TEXT, value REAL, timestamp TEXT);"
        "CREATE TABLE IF NOT EXISTS workout_log ("
        " kind TEXT, kcal REAL, ts DATETIME);"
        "CREATE TABLE IF NOT EXISTS user_state ("
        " key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_plan ("
        " week_start DATE, weekday INTEGER, day_type TEXT, "
        " gym_label TEXT, target_kcal REAL);"
        "CREATE TABLE IF NOT EXISTS nudge_log ("
        " kind TEXT, sent_at DATETIME DEFAULT CURRENT_TIMESTAMP, day DATE);"
        "CREATE TABLE IF NOT EXISTS hrv_log ("
        " value REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weighin_log ("
        " weight_lbs REAL, ts DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE IF NOT EXISTS weekly_campaigns ("
        " week_start DATE PRIMARY KEY,"
        " target_active_calories REAL NOT NULL,"
        " created_at DATETIME DEFAULT CURRENT_TIMESTAMP);"
    )
    con.commit()


@pytest.fixture
def sci(tmp_path):
    from core import io as cio
    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    plan_path.write_text("")

    con = sqlite3.connect(db_path)
    _schema(con)
    con.close()
    cio.DB_PATH = db_path

    if "sci" in sys.modules:
        del sys.modules["sci"]
    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = mod
    spec.loader.exec_module(mod)
    mod.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path
    return mod, db_path


def test_week_pace_formula_is_unified(sci):
    """handler._prorated_week_target must be the exact same calculation
    as state.expected_week_burn_to_date — one formula, no drift."""
    _mod, _db = sci
    from agents.the_scientist import handler as h
    from agents.the_scientist import state as st
    for when in (datetime(2026, 5, 4, 0, 0, 1),
                 datetime(2026, 5, 6, 12, 0),
                 datetime(2026, 5, 10, 23, 59, 59)):
        assert h._prorated_week_target(6000, now=when) == \
            st.expected_week_burn_to_date(now=when, weekly_t=6000)


def test_behind_pace_after_missed_monday_is_not_called_ahead(sci):
    """Reproduce the transcript: a missed Monday + low week-to-date burn,
    checked midweek. The brief must report behind-pace, never 'ahead of
    pace' or 'comfortable buffer'."""
    mod, db = sci
    from agents.the_scientist import state as st

    monday = datetime(2026, 5, 4)
    now = datetime(2026, 5, 6, 12, 0)   # Wednesday noon

    st.state_set("recovery_tier", "hammer")
    # Force a full CF week so the remaining plan over-covers the goal
    # (gap < 0) — the exact shape that produced the false "ahead".
    st.set_prefs(monday, forced_cf_days=[0, 1, 2, 3, 4, 5, 6],
                 forced_z2_day=None, unavailable_days=[])

    # Only 160 kcal burned all week (Monday's CF was missed).
    con = sqlite3.connect(db)
    con.execute("INSERT INTO workout_log (kind, kcal, ts) VALUES (?,?,?)",
                ("cf", 160.0, "2026-05-04 10:00:00"))
    con.commit()
    con.close()

    r = mod.compute_week_recalibration(now=now)

    assert "expected_to_date" in r and "behind_pace" in r
    assert r["expected_to_date"] > r["burned_so_far"]
    assert r["behind_pace"] is True, (
        f"160 kcal by Wednesday must read as behind pace-to-date: {r}")
    assert "comfortable buffer" not in r["summary"], r["summary"]
    assert "Ahead of pace" not in r["summary"], r["summary"]
    assert "Ahead of plan" not in r["summary"], r["summary"]
    assert "Behind" in r["summary"], r["summary"]
