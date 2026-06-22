"""Regression: a committed weekly target must reshape the daily plan (2026-05-25).

Symptom (from the live transcript): the user said "replan … assuming I
can only burn 6000 calories a week." The plan header changed to
"target 6,000 kcal" but the per-day ideals stayed on the hammer-tier
template (Mon 1,300 / Tue 1,300 / Wed 600 / Thu 1,300 / Fri 600 /
Sat 1,400 / Sun 600 = 7,100). The weekly number and the daily
distribution came from two independent sources and never reconciled,
so pace checks and the displayed ideals disagreed.

Root cause: `replan_week` set each day's `target_kcal` from
`DAY_TYPE_BY_TIER` (a tier constant) and never scaled the distribution
to the active weekly target.

Fix: when an explicit weekly-target commitment is active, `replan_week`
rescales the non-zero days proportionally so they SUM to the commitment
(snapped to a 25-kcal grid, remainder absorbed on the largest day).
Weeks with NO commitment are left on the tier template untouched.

This test pins:
  1. With a 6,000/wk commitment, the daily plan sums to exactly 6,000.
  2. With NO commitment, the daily ideals stay on the tier template
     (no spurious rescale — protects the documented tier behavior).
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
    plan_path.write_text("")  # no synced gym plan; cadence drives the week

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
    return mod


MONDAY = datetime(2026, 5, 4)


def test_committed_weekly_target_rescales_daily_plan(sci):
    from core import memory as mem
    from agents.the_scientist import state as st

    st.state_set("recovery_tier", "hammer")
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 6000})

    plan = st.replan_week(MONDAY, force=True)
    total = sum(r["target_kcal"] for r in plan)
    assert total == 6000, (
        f"daily ideals must sum to the committed 6,000 kcal/wk, got "
        f"{total}. The weekly target is not flowing into the plan "
        f"distribution (Bug B regressed).")
    # And the header the user sees (sum of daily) now equals the target.
    assert int(total) == int(st.weekly_target())


def test_no_commitment_keeps_tier_template(sci):
    """Inverse: without an explicit commitment, the daily ideals must
    stay on the documented tier template — no surprise rescale."""
    from agents.the_scientist import state as st
    from agents.the_scientist.protocols import DAY_TYPE_BY_TIER

    st.state_set("recovery_tier", "hammer")
    plan = st.replan_week(MONDAY, force=True)
    cf_days = [r for r in plan if r["day_type"] == "cf"]
    assert cf_days, "expected CF days in the fallback cadence"
    assert all(r["target_kcal"] == DAY_TYPE_BY_TIER["hammer"]["cf"]
               for r in cf_days), (
        "tier-default week was rescaled even with no commitment — the "
        "fix must be commitment-gated to protect the tier template.")
