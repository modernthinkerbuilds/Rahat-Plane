"""Regression pin (2026-10-19): the 9:30 pm cooldown sees a gym session in
the Watch's heart-rate trace, before the phone's workout export names it.

LIVE 2026-10-19: a CrossFit-plan day, an hour at the box from 16:20. At
21:30 the autorun said "Plan says CrossFit today, but nothing has synced
from the Watch or the gym, so this is the rest-day version." The owner:
"The sync from my phone ran and I got an alert on the phone."

It had. The phone's workout export (Health Auto Export, "Previous 7
Days": the seven full days before today) sent six workouts at 20:44,
all of them from the week before; a session shows up the morning after,
every day (the Oct 6, 12, 18 and 19 exports all show it). The run-day
fix of Oct 18 read runs from the running-form series, which a gym
session does not write; and the gym's WOD was not synced either. So the
tick could not tell, and asked.

What every Watch workout does write is the heart rate every few seconds
(ten readings a minute), where the rest of the day has one every few
minutes. A stretch of minutes with four or more readings is a workout
from its first minute to its last: checked on seven weeks of typed
sessions, each starts within three minutes of its dense stretch. Steps
and heart rate name it: a walk is 64-93 steps a minute at an easy heart
rate; a box session 22-53 steps with the heart rate up; a cool-down a
handful of steps under 100 bpm.

Fix:
  * context.dense_hr_sessions reads the day's dense stretches (pauses
    up to ten minutes, twenty minutes or more); sessions_from_vitals adds
    each one a run does not already cover as a Gym session (training),
    an Outdoor Walk or a Light session; a typed session still wins.
  * A traced gym session counts as training like a typed one, so the
    autorun sends the post-workout cooldown; the coach says where it was
    read from ("the Watch shows a 70-minute workout at 4:20 pm"), and the
    model prompt is steered the same way.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

DAY = datetime(2026, 10, 19, 0, 0)
NIGHT = DAY.replace(hour=21, minute=35)
RUN_METRICS = ("running_speed", "running_power", "running_ground_contact_time",
               "running_vertical_oscillation", "running_stride_length")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for k in ("HUBERMAN_AUTOCOOL", "HUBERMAN_PROFILE_JSON", "HUBERMAN_STORE_JSON", "RAHAT_HUBERMAN_DB"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "vault").mkdir()
    from agents.huberman import context, state
    state.profile_path().write_text(json.dumps({
        "default_minutes": 15,
        "hotspots": [{"area_tag": "neck", "label": "neck"}, {"area_tag": "foot", "label": "foot arch"}],
        "equipment": ["peanut ball", "lacrosse ball", "foam roller", "band"],
    }))
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: None)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "cf")
    con = sqlite3.connect(state.db_path())
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS workouts_hk (
        workout_id TEXT PRIMARY KEY, name TEXT, start TEXT, end TEXT, duration_s REAL,
        active_kcal REAL, avg_hr REAL, max_hr REAL, distance_km REAL, raw_json TEXT)""")
    # the rest of the day: a reading every five minutes, at rest
    for i in range(0, 24 * 60, 5):
        ts = (DAY + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:30")
        con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("heart_rate", 72.0, ts))
    con.commit()
    con.close()
    return state


def _dense(state, start: datetime, minutes: int, *, hr: float, steps_per_min: float, per_min: int = 10,
           run: bool = False):
    """A Watch workout as the trace shows it: `per_min` heart-rate readings
    a minute, the steps and distance beside them, the running series only
    when it is a run."""
    con = sqlite3.connect(state.db_path())
    for i in range(minutes):
        minute = start + timedelta(minutes=i)
        for k in range(per_min):
            ts = (minute + timedelta(seconds=k * (60 // per_min))).strftime("%Y-%m-%d %H:%M:%S")
            con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("heart_rate", hr + (k % 3) * 4 + (15 if i == minutes // 2 else 0), ts))
        ts = minute.strftime("%Y-%m-%d %H:%M:00")
        con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("steps", steps_per_min, ts))
        con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("distance_km", steps_per_min * 0.0007, ts))
        if run:
            for m in RUN_METRICS:
                con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", (m, 3.0, ts))
    con.commit()
    con.close()


def _typed(state, name: str, start: datetime, minutes: int):
    con = sqlite3.connect(state.db_path())
    con.execute("INSERT INTO workouts_hk VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"{name}-{start:%H%M}", name, start.strftime("%Y-%m-%d %H:%M:%S"),
                 (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"), minutes * 60.0,
                 420.0, 118.0, 162.0, 0.7, "{}"))
    con.commit()
    con.close()


def _no_model(monkeypatch):
    from core import llm

    def down(*a, **k):
        raise RuntimeError("no model in this test")
    monkeypatch.setattr(llm, "generate", down)


BOX = dict(hr=108.0, steps_per_min=25.0)          # tonight's session: 25 steps a minute, heart rate up


# ── the session is read from the trace ──
def test_a_gym_session_is_read_from_the_dense_heart_rate(env):
    from agents.huberman import context
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    sessions = context.sessions_from_vitals(NIGHT)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["name"] == "Gym session" and s["inferred"] and not s["ask_direct"] and s["kind"] is None
    assert s["start"] == "2026-10-19 16:20:00" and s["end"] == "2026-10-19 17:29:00" and s["minutes"] == 69
    assert s["avg_hr"] and 108 <= s["avg_hr"] <= 116 and s["max_hr"] >= 123 and s["kcal"] is None
    assert "heart-rate trace" in s["source"]
    ctx = context.gather(NIGHT)
    assert ctx["crossfit_today"] == sessions and ctx["trained_today"] and ctx["traced_gym_session"] == s
    assert not ctx["plan_unsynced"] and not ctx["assumed_from_plan"] and ctx["cardio_today"] == []


def test_the_rest_of_the_day_is_not_a_session(env):
    from agents.huberman import context
    assert context.sessions_from_vitals(NIGHT) == []
    _dense(env, DAY.replace(hour=12, minute=0), 12, **BOX)             # a warm-up, a breathing session
    assert context.sessions_from_vitals(NIGHT) == []


def test_a_pause_up_to_ten_minutes_keeps_one_session(env):
    from agents.huberman import context
    _dense(env, DAY.replace(hour=16, minute=20), 30, **BOX)
    _dense(env, DAY.replace(hour=16, minute=58), 30, **BOX)             # eight minutes of nothing between
    assert [(s["start"][11:16], s["minutes"]) for s in context.sessions_from_vitals(NIGHT)] == [("16:20", 68)]


# ── what it was ──
def test_a_walk_and_a_cool_down_are_not_training(env):
    from agents.huberman import context, handler
    _dense(env, DAY.replace(hour=19, minute=30), 60, hr=94.0, steps_per_min=85.0)
    _dense(env, DAY.replace(hour=12, minute=0), 40, hr=88.0, steps_per_min=3.0)
    names = {s["name"]: s for s in context.sessions_from_vitals(NIGHT)}
    assert set(names) == {"Outdoor Walk", "Light session"}
    assert names["Outdoor Walk"]["ask_direct"] and names["Outdoor Walk"]["distance_km"] == 3.57
    assert names["Light session"]["ask_direct"] and names["Light session"]["distance_km"] is None
    ctx = context.gather(NIGHT)
    assert ctx["crossfit_today"] == [] and not ctx["trained_today"]
    assert handler.maybe_autocool(now=NIGHT) is None, "a walk-only day stays silent (the 08-23 rule)"


def test_a_wod_with_running_in_it_is_still_the_gym(env):
    from agents.huberman import context
    _dense(env, DAY.replace(hour=17, minute=19), 67, hr=122.0, steps_per_min=50.0)   # 400 m repeats
    assert [s["name"] for s in context.sessions_from_vitals(NIGHT)] == ["Gym session"]


def test_a_run_is_a_run_and_not_also_a_gym_session(env):
    from agents.huberman import context
    _dense(env, DAY.replace(hour=11, minute=3), 75, hr=150.0, steps_per_min=155.0, run=True)
    sessions = context.sessions_from_vitals(NIGHT)
    assert [s["name"] for s in sessions] == ["Outdoor Run"] and sessions[0]["kind"] == "run"


def test_the_typed_session_wins_once_the_export_lands(env):
    from agents.huberman import context
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    _typed(env, "Cross Training", DAY.replace(hour=16, minute=20), 65)
    sessions = context.sessions_today(NIGHT)
    assert len(sessions) == 1 and sessions[0]["name"] == "Cross Training" and not sessions[0]["inferred"]
    assert context.gather(NIGHT)["traced_gym_session"] is None


# ── the 9:30 autorun ──
def test_the_autorun_sends_the_post_session_cooldown(env, monkeypatch):
    _no_model(monkeypatch)
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    from agents.huberman import coach, handler
    out = handler.maybe_autocool(now=NIGHT)
    assert out and "_Post-session — the Watch shows a 69-minute workout at 4:20 pm (the export hasn't named it)" in out, out
    assert coach.UNSURE_LINE not in out and coach.OFFER_LINE not in out
    assert "nothing has synced" not in out.lower() and "rest day" not in out.lower() and "No workout logged" not in out
    assert handler.maybe_autocool(now=NIGHT + timedelta(minutes=5)) is None      # once a night


def test_the_model_path_is_steered_to_the_post_wod_cooldown(env, monkeypatch):
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    from core import llm
    seen = {}

    class _Usage:
        error, text = None, "*🧘 Tonight's cooldown*\n1. Couch stretch — 2 min a side"

    monkeypatch.setattr(llm, "generate", lambda *a, **k: (seen.update(k), _Usage())[1])
    from agents.huberman import handler
    out = handler.maybe_autocool(now=NIGHT)
    assert "Couch stretch" in out
    prompt = seen["prompt"]
    assert "Workout: Gym session, 69 min, avgHR" in prompt and "(from the Watch's heart-rate trace" in prompt
    assert "69-minute workout at 4:20 pm that no workout export has named yet" in prompt
    assert "the plan says CrossFit, so coach it as the post-WOD cooldown" in prompt
    assert "(no workout data synced)" not in prompt and "cannot tell whether" not in prompt


def test_with_the_wod_synced_the_floor_leads_with_what_it_loaded(env, monkeypatch):
    _no_model(monkeypatch)
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    from agents.huberman import context, handler
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: "*Mon*\nBack Squat 5x3\nAMRAP 12: 10 Deadlifts, 10 Box Jumps")
    out = handler.maybe_autocool(now=NIGHT)
    assert "_Today loaded:" in out and "the export hasn't named it" not in out


def test_a_direct_ask_tonight_sees_the_session_too(env, monkeypatch):
    _no_model(monkeypatch)
    _dense(env, DAY.replace(hour=16, minute=20), 69, **BOX)
    from agents.huberman import coach
    out = coach.generate_cooldown(now=NIGHT.replace(minute=50))
    assert "the Watch shows a 69-minute workout at 4:20 pm" in out


def test_the_clock_reads_the_household_way():
    from agents.huberman.coach import _clock
    assert _clock("2026-10-19 16:20:00") == "4:20 pm" and _clock("2026-10-19 07:05:00") == "7:05 am"
    assert _clock("2026-10-19 00:10:00") == "12:10 am" and _clock("2026-10-19 12:00:00") == "12:00 pm"
    assert _clock(None) == ""
