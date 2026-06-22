"""Regression: handle_show_plan surfaces an explicit "train these days &
why" summary above the day grid (2026-06-18, owner output slice).

The capability that dropped in the migration: the plan should TELL the
user which days to train and WHY, and surface blocked gym days + the
one-word fix — rather than silently under-scheduling or printing a
blocked WOD on a rest row with no context.

Pins (substring, render-shape tolerant):
  1. With gym synced, the plan leads with "Train these days: <days>".
  2. A blocked gym day on a non-CF row is reframed as NOT assigned
     (so a rest row no longer reads like it has an assigned WOD) while
     still keeping the 'blocked' + 'tolerate' affordances the
     2026-05-18 sub-line tests pin.
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
    )
    con.commit()


@pytest.fixture
def synced(tmp_path, monkeypatch):
    """A week with a synced gym plan: a few clean days + at least one
    blacklist-blocked day, so both summary branches exercise."""
    from core import io as cio
    db_path = tmp_path / "rahat.db"
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
    return mod


def _synthetic_gym_days():
    """Mon/Wed/Fri clean; Tue blocked by snatch — a realistic week."""
    from agents.the_scientist.handler import GymDay  # type: ignore
    return [
        GymDay(label="Mon", weekday="MON", body="Back squat 5x5\n'Helen'",
               strength="Back squat 5x5", blockers=[]),
        GymDay(label="Tue", weekday="TUE", body="Snatch 3x3\n'Isabel'",
               strength="Snatch 3x3", blockers=["snatch"]),
        GymDay(label="Wed", weekday="WED", body="Deadlift 5x5\n'Fran'",
               strength="Deadlift 5x5", blockers=[]),
        GymDay(label="Fri", weekday="FRI", body="Bench 5x5\n'Cindy'",
               strength="Bench 5x5", blockers=[]),
    ]


def test_show_plan_leads_with_train_these_days(synced, monkeypatch):
    from agents.the_scientist import handler as h, state as st
    monkeypatch.setattr(h, "parse_gym_plan", _synthetic_gym_days)
    st.state_set("recovery_tier", "hammer")
    monday = datetime(2026, 5, 25)
    st.replan_week(monday, force=True)
    out = h.handle_show_plan()
    assert "Train these days:" in out, (
        "the plan must explicitly tell the user which days to train.\n"
        f"Output:\n{out}")


def test_blocked_day_reframed_not_assigned(synced, monkeypatch):
    from agents.the_scientist import handler as h, state as st
    monkeypatch.setattr(h, "parse_gym_plan", _synthetic_gym_days)
    st.state_set("recovery_tier", "hammer")
    monday = datetime(2026, 5, 25)
    st.replan_week(monday, force=True)
    out = h.handle_show_plan()
    # The blocked movement + tolerate affordance still surface (2026-05-18
    # sub-line contract), AND the reframe makes clear it isn't assigned.
    assert "blocked" in out and "tolerate" in out.lower()
    assert "not assigned" in out, (
        "a blocked gym WOD on a rest row must read as NOT assigned so the "
        f"rest day no longer looks like it has an assigned workout.\n{out}")
