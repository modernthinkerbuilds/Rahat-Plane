"""Regression/feature: inverse goal projection (2026-05-25).

Symptom (from the live transcript): the user asked "if I burn 6000/wk
and eat 2250/day, when can I get to 197?" and "when will I get to 196?".
The bot correctly said it had no tool for this — `compute_goal_plan`
only goes (target + date) → (required intake/burn), never the inverse.
So a legitimate question had no home and the bot parroted the committed
date instead of projecting.

Fix: `project_goal_eta(target, daily_intake_kcal, weekly_active_kcal)`
projects the DATE from a fixed intake + burn. Sign-aware: a target below
current weight needs a deficit; above needs a surplus; if the numbers
don't move weight the right way it returns no ETA and says why.

This test pins the deterministic math (no LLM):
  1. A real cut (199→197 at a deficit) returns a future ETA + positive
     lb/wk rate, direction 'lose'.
  2. Wanting to lose while eating at a surplus returns NO eta and names
     the missing deficit (no fantasy date).
  3. Missing inputs return a structured error, never a crash.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
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
def tools(tmp_path):
    from core import io as cio
    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    plan_path.write_text("")

    con = sqlite3.connect(db_path)
    _schema(con)
    # Current weight 199.0 lbs (matches the transcript).
    con.execute("INSERT INTO weighin_log (weight_lbs, ts) VALUES (?,?)",
                (199.0, "2026-05-25 08:00:00"))
    con.commit()
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
    from agents.the_scientist import tools as T
    return T


def test_eta_projected_for_a_real_cut(tools):
    r = tools.project_goal_eta(target_lbs=197,
                               daily_intake_kcal=2250,
                               weekly_active_kcal=6000)
    assert "error" not in r, r
    assert r["direction"] == "lose"
    assert r["rate_lb_per_wk"] > 0, r
    assert r["eta_date_iso"] is not None, r
    assert r["weeks_to_target"] is not None and r["weeks_to_target"] > 0
    # 199 → 197 at a real deficit should land within a couple of months.
    assert r["weeks_to_target"] < 20, r


def test_unreachable_cut_returns_no_eta(tools):
    """Want to lose, but eating at a surplus → no ETA, names the deficit."""
    r = tools.project_goal_eta(target_lbs=197,
                               daily_intake_kcal=5000,
                               weekly_active_kcal=0)
    assert "error" not in r, r
    assert r["eta_date_iso"] is None, r
    assert "DEFICIT" in r["summary"], r["summary"]


def test_missing_inputs_is_structured_error(tools):
    assert "error" in tools.project_goal_eta(target_lbs=197)
    assert "error" in tools.project_goal_eta(daily_intake_kcal=2250,
                                             weekly_active_kcal=6000)


def test_registered_in_schema_and_registry(tools):
    names = {s["name"] for s in tools.SCHEMAS}
    assert "project_goal_eta" in names
    assert "project_goal_eta" in tools._DISPATCH
    # And it dispatches end-to-end through the reasoner entry point.
    out = tools.dispatch("project_goal_eta",
                         {"target_lbs": 197, "daily_intake_kcal": 2250,
                          "weekly_active_kcal": 6000})
    assert out.get("eta_date_iso") is not None, out
