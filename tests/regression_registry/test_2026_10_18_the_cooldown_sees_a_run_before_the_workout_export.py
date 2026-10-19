"""Regression pin (2026-10-18): the 9:30 pm cooldown sees a run from the
Watch's minute data, hours before the phone's workout export lands.

LIVE 2026-10-18: a run at midday. At 21:30 the autorun said "Rest day;
attacking chronic hotspots alone." The typed session was not in
workouts_hk — the phone exports workouts on a morning schedule, so the
table still ended at the previous day — and the autorun reads only that
table. The Watch's running-form series (speed, power, ground contact
time, vertical oscillation, stride length) were on the Mac mini by then,
written at minute resolution and only inside a running workout; every
typed run on file matches their minute window.

Fix:
  * context.sessions_from_vitals reads today's running minutes, splits
    them on pauses longer than ten minutes, drops anything under five
    minutes, and returns each cluster as a run (inferred, no kcal, with
    the distance and heart rate inside the window). sessions_today merges
    them with the typed workouts; a typed session that overlaps wins.
  * Owner, 2026-10-18: a run day gets the post-run cooldown (the 08-23
    rule had kept run days silent). The tick steers the model to
    post-run, the deterministic floor opens "Post-run — N min, D km", and
    the why lines name what the run loaded. A walk-only day stays silent.
  * A programmed WOD on a CrossFit-plan day still counts as done when a
    run is also on file (assumed_from_plan reads "no CrossFit synced",
    not "nothing synced").
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

DAY = datetime(2026, 10, 18, 0, 0)
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
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "z2")
    con = sqlite3.connect(state.db_path())
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS workouts_hk (
        workout_id TEXT PRIMARY KEY, name TEXT, start TEXT, end TEXT, duration_s REAL,
        active_kcal REAL, avg_hr REAL, max_hr REAL, distance_km REAL, raw_json TEXT)""")
    con.commit()
    con.close()
    return state


def _running_minutes(state, start: datetime, minutes: int, *, km_per_min: float = 0.17, hr: float = 150.0):
    """The series the Watch writes inside a running workout, one row per
    metric per minute, plus the distance and heart-rate series beside them."""
    con = sqlite3.connect(state.db_path())
    for i in range(minutes):
        ts = (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:00")
        for m in RUN_METRICS:
            con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", (m, 3.0, ts))
        con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("distance_km", km_per_min, ts))
        con.execute("INSERT INTO raw_vitals VALUES (?,?,?)", ("heart_rate", hr, ts))
    con.commit()
    con.close()


def _typed(state, name: str, start: datetime, minutes: int, km: float | None = None):
    con = sqlite3.connect(state.db_path())
    end = start + timedelta(minutes=minutes)
    con.execute("INSERT OR REPLACE INTO workouts_hk VALUES (?,?,?,?,?,500,140,170,?,'{}')",
                (f"{name}-{start:%H%M}", name, start.strftime("%Y-%m-%d %H:%M:%S"),
                 end.strftime("%Y-%m-%d %H:%M:%S"), minutes * 60, km))
    con.commit()
    con.close()


def _no_model(monkeypatch):
    from core import llm

    def down(*a, **k):
        raise RuntimeError("no model in this test")

    monkeypatch.setattr(llm, "generate", down)


# ── the run is read from the minute data ──
def test_a_run_is_read_from_the_running_metrics(env):
    from agents.huberman import context
    _running_minutes(env, DAY.replace(hour=11, minute=3), 75)
    runs = context.sessions_from_vitals(NIGHT)
    assert len(runs) == 1
    r = runs[0]
    assert r["name"] == "Outdoor Run" and r["kind"] == "run" and r["inferred"] and r["ask_direct"]
    assert r["start"] == "2026-10-18 11:03:00" and r["end"] == "2026-10-18 12:18:00" and r["minutes"] == 75
    assert r["distance_km"] == 12.75 and r["avg_hr"] == 150 and r["kcal"] is None
    ctx = context.gather(NIGHT)
    assert ctx["workouts"] == runs and ctx["cardio_today"] == runs and ctx["crossfit_today"] == []
    assert ctx["inferred_sessions"] == runs
    assert any(areas[:2] == ("calves", "foot") for _hit, areas, _why in ctx["loaded"])


def test_a_short_blip_is_not_a_session_and_a_pause_splits_two(env):
    from agents.huberman import context
    _running_minutes(env, DAY.replace(hour=7, minute=0), 3)                 # under five minutes
    assert context.sessions_from_vitals(NIGHT) == []
    _running_minutes(env, DAY.replace(hour=9, minute=0), 20)
    _running_minutes(env, DAY.replace(hour=9, minute=40), 20)               # a 20-minute pause between
    starts = [s["start"] for s in context.sessions_from_vitals(NIGHT)]
    assert starts == ["2026-10-18 09:00:00", "2026-10-18 09:40:00"]
    _running_minutes(env, DAY.replace(hour=14, minute=0), 10)
    _running_minutes(env, DAY.replace(hour=14, minute=15), 10)              # a 5-minute pause: one run
    assert [s["minutes"] for s in context.sessions_from_vitals(NIGHT) if s["start"] >= "2026-10-18 14"] == [25]


def test_the_typed_session_wins_once_the_export_lands(env):
    from agents.huberman import context
    _running_minutes(env, DAY.replace(hour=11, minute=3), 75)
    _typed(env, "Outdoor Run", DAY.replace(hour=11, minute=2), 76, km=12.5)
    sessions = context.sessions_today(NIGHT)
    assert len(sessions) == 1 and not sessions[0]["inferred"] and sessions[0]["distance_km"] == 12.5
    assert sessions[0]["kind"] == "run"


# ── the 9:30 autorun ──
def test_the_autorun_sends_the_post_run_cooldown(env, monkeypatch):
    _no_model(monkeypatch)
    _running_minutes(env, DAY.replace(hour=11, minute=3), 75)
    from agents.huberman import handler
    out = handler.maybe_autocool(now=NIGHT)
    assert out and "_Post-run — 75 min, 12.75 km" in out, out
    assert "No workout logged" not in out and "rest day" not in out.lower()
    assert "why: outdoor run — running loads the calves" in out.lower()      # a why line names the run
    assert handler.maybe_autocool(now=NIGHT + timedelta(minutes=5)) is None      # once a night


def test_the_model_path_is_steered_to_post_run(env, monkeypatch):
    _running_minutes(env, DAY.replace(hour=11, minute=3), 75)
    from core import llm
    seen = {}

    class _Usage:
        error, text = None, "*🧘 Tonight's cooldown*\n1. Calf smash — 3 min a side"

    monkeypatch.setattr(llm, "generate", lambda *a, **k: (seen.update(k), _Usage())[1])
    from agents.huberman import handler
    out = handler.maybe_autocool(now=NIGHT)
    assert "Calf smash" in out
    prompt = seen["prompt"]
    assert "Workout: Outdoor Run, 75 min, avgHR 150, 12.75 km, (from the Watch's running metrics" in prompt
    assert "post-run" in prompt and "do not call it a rest day" in prompt
    assert "(no workout data synced)" not in prompt


def test_a_walk_only_day_still_stays_silent(env):
    _typed(env, "Outdoor Walk", DAY.replace(hour=19, minute=0), 60)
    from agents.huberman import handler
    assert handler.maybe_autocool(now=NIGHT) is None


def test_a_programmed_wod_still_counts_when_a_run_is_also_on_file(env, monkeypatch):
    _no_model(monkeypatch)
    from agents.huberman import context, handler
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "cf")
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: "*Sat*\nBack Squat 5x3\nAMRAP 12: 10 Deadlifts")
    _running_minutes(env, DAY.replace(hour=7, minute=0), 30)
    ctx = context.gather(NIGHT)
    assert ctx["assumed_from_plan"] and ctx["trained_today"]
    out = handler.maybe_autocool(now=NIGHT)
    assert "Watch not synced yet" in out and "Post-run" not in out


def test_a_steer_that_ends_in_down_regulation_does_not_pull_the_lats_in():
    from agents.huberman import coach
    assert coach._focus_from("post-run — calves, feet, hamstrings, hip flexors, ending in down-regulation") == \
        ["hip", "hamstrings", "calves", "foot"]
    assert coach._focus_from("all lats please") == ["lats"]
