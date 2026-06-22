"""Feature: weekly burn target derives DYNAMICALLY from the active goal (2026-05-25).

The user's ask: the weekly target shouldn't be a static tier constant or
a number you type — it should follow your goal ("X lbs by Y date") and
the plan should adjust. This closes the goal → weekly-burn → daily-plan
loop:

  active committed goal (target lbs + date, current weight)
    → required weekly burn  (compute_goal_plan, "hold intake, flex burn")
    → weekly_target()       (this layer)
    → daily ideals          (replan_week rescale, Bug B)

Policy (user-chosen): hold intake, flex the burn, capped at a safe
ceiling (hammer-tier weekly) so an aggressive goal never auto-prescribes
overtraining. Gated behind RAHAT_GOAL_DRIVEN_TARGET (default OFF) so
tier/commitment behavior — and its tests — are unchanged until enabled.

Pins:
  1. Flag ON + committed goal → weekly_target == the goal-derived burn
     (compute_goal_plan's "hold intake" option, clamped). Dynamic: it
     reads current weight via compute_goal_plan.
  2. Flag ON → the daily plan SUMS to that goal-derived target.
  3. Flag OFF (default) → weekly_target falls back to the tier constant.
  4. An explicit weekly commitment OUTRANKS the goal math.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FUTURE_DATE = "2026-08-01"   # comfortably future relative to the test clock


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
def env(tmp_path):
    from core import io as cio
    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    plan_path.write_text("")

    con = sqlite3.connect(db_path)
    _schema(con)
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
    return mod, db_path


def _commit_goal(target_lbs=190):
    from core import memory as mem
    mem.put_entity("scientist", "goal",
                   {"target_lbs": target_lbs, "target_date_iso": FUTURE_DATE})


def _expected_goal_burn(tools, target_lbs=190):
    from agents.the_scientist.protocols import TIERS
    plan = tools.compute_goal_plan(target_lbs=target_lbs,
                                   target_date=FUTURE_DATE)
    opt = next(o for o in plan["options"]
               if "hold intake" in o["name"].lower())
    ceiling = float(TIERS["hammer"]["weekly"])
    return min(float(opt["weekly_active_kcal"]), ceiling)


def test_flag_on_weekly_target_tracks_goal(env, monkeypatch):
    mod, _db = env
    from agents.the_scientist import state as st, tools as T
    monkeypatch.setenv("RAHAT_GOAL_DRIVEN_TARGET", "1")
    st.state_set("recovery_tier", "hammer")
    _commit_goal(190)

    expected = _expected_goal_burn(T, 190)
    assert st.weekly_target() == expected, (
        f"weekly target should track the goal-derived burn {expected}, "
        f"got {st.weekly_target()}")


def test_flag_on_daily_plan_stays_fixed_goal_drives_pace_not_rescale(env, monkeypatch):
    """B model (2026-06-18, owner): the goal drives weekly_target() for PACE
    FEEDBACK, but it NO LONGER rescales the per-day ideals — a CF/Z2 day keeps
    its fixed template burn. (Reverses the 2026-05-25 daily-rescale: that
    produced the 1,225/1,350/550 nonsense by squashing the template onto a
    goal-derived weekly number.) The gap between the fixed plan sum and
    weekly_target() is precisely the make-up signal the pace path surfaces."""
    mod, _db = env
    from agents.the_scientist import state as st
    from agents.the_scientist.protocols import DAY_TYPE_BY_TIER
    monkeypatch.setenv("RAHAT_GOAL_DRIVEN_TARGET", "1")
    st.state_set("recovery_tier", "hammer")
    _commit_goal(190)

    from datetime import datetime
    monday = datetime(2026, 5, 25)   # the week of the test weigh-in
    plan = st.replan_week(monday, force=True)
    tmpl = DAY_TYPE_BY_TIER["hammer"]
    # Every active day stays at its FIXED template burn — no goal rescale.
    for r in plan:
        if r["target_kcal"] > 0:
            assert r["target_kcal"] == tmpl[r["day_type"]], (
                f"{r['day_type']} day was rescaled to {r['target_kcal']}; "
                f"under B it must stay at the fixed template {tmpl[r['day_type']]}")
    # weekly_target() still tracks the goal (pace feedback) — it is now
    # DECOUPLED from the per-day plan rather than forcing it.
    assert st.weekly_target() > 0


def test_flag_off_falls_back_to_tier(env, monkeypatch):
    mod, _db = env
    from agents.the_scientist import state as st
    from agents.the_scientist.protocols import TIERS
    monkeypatch.delenv("RAHAT_GOAL_DRIVEN_TARGET", raising=False)
    st.state_set("recovery_tier", "hammer")
    _commit_goal(190)
    assert st.weekly_target() == float(TIERS["hammer"]["weekly"]), (
        "with the flag off, a committed goal must NOT change the weekly "
        "target — tier default stands.")


def test_explicit_commitment_outranks_goal(env, monkeypatch):
    mod, _db = env
    from core import memory as mem
    from agents.the_scientist import state as st
    monkeypatch.setenv("RAHAT_GOAL_DRIVEN_TARGET", "1")
    st.state_set("recovery_tier", "hammer")
    _commit_goal(190)
    # User states their capacity explicitly — this must win over the math.
    mem.put_entity("scientist", "commitment",
                   {"kind": "weekly_target", "value": 6000})
    assert st.weekly_target() == 6000.0
