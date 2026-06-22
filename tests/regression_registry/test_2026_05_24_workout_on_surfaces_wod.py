"""Regression: 'workout for <day>' must surface the WOD, not punt to the app.

2026-05-24 live Telegram transcript:
    "What is the workout for Tuesday?"
        -> "Tuesday ka workout hai CrossFit, target ~1,300 kcal.
            WOD details ke liye SugarWOD app check kar lo."   (check the app)
    "What is the WOD for Tuesday?"
        -> full Clean-Complex + 'Don't Speak' EMOM WOD.        (the data IS there)

Root cause: handle_workout_on resolves the WOD by matching the cadence
row's `gym_label` against parse_gym_plan(). When the loaded SugarWOD
pull is for a DIFFERENT week than the cadence row (the next-week case
in the transcript), the label match misses and the handler falls back
to "_(WOD details not available — check SugarWOD app)_". Meanwhile
handle_gym_wod_on reads the same plan by WEEKDAY token and succeeds — so
"WOD for Tuesday" works while "workout for Tuesday" punts. ADR-011: do
not send the user to the app when we already hold the data.

Fix: when the gym_label match yields no summary, handle_workout_on now
falls back to a weekday-token read (the same source handle_gym_wod_on
uses) before defaulting to the app message.

This test pins the divergence: a CF day whose gym_label does NOT match
the loaded plan, with a real WOD present for that weekday, must surface
the WOD.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


def _write_synced_plan(plan_path: Path) -> None:
    blocks = [
        ("Mon 25", "Back squat 5x5", "5 rounds: 400m run, 21 deadlifts"),
        ("Tue 26", "Clean Complex Every 1:30 x 8",
         "\"Don't Speak\" 16:00 EMOM: 15 GHD sit-ups, 10 power cleans"),
        ("Wed 27", "Front squat 5x3", "21-15-9 thrusters, pullups"),
        ("Thu 28", "Bench press 3x5", "AMRAP 12 Furiosa"),
        ("Fri 29", "Deadlift 5x5", "For time: 50-40-30-20-10 wall balls"),
        ("Sat 30", "Hero WOD: MURPH", "1 mile run, 100 pullups"),
    ]
    out = []
    for label, strength, wod in blocks:
        out.append("\n".join([
            label, "", "", "0",
            " Strength", strength, "", "0 results",
            " WOD", wod, "", "0 results",
        ]))
    plan_path.write_text("\n".join(out) + "\n")


@pytest.fixture
def synced_kobe(tmp_path):
    import importlib.util
    import sys
    from core import io as cio

    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    _write_synced_plan(plan_path)

    con = sqlite3.connect(db_path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS user_state ("
        " key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE IF NOT EXISTS weekly_plan ("
        " week_start DATE, weekday INTEGER, day_type TEXT, "
        " gym_label TEXT, target_kcal REAL);"
    )
    con.commit()
    con.close()
    cio.DB_PATH = db_path

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    sci.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path
    return sci


def test_workout_on_surfaces_wod_when_label_misses(synced_kobe, monkeypatch):
    """Tuesday is CF in cadence, but its stored gym_label ('Tue 99')
    doesn't match the loaded plan (which has 'Tue 26'). The handler must
    still surface the WOD by weekday — not say 'check SugarWOD app'."""
    from agents.the_scientist import handler as h

    fake_plan = [
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
        {"day_type": "cf", "target_kcal": 1300, "gym_label": "Tue 99"},
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
        {"day_type": "rest", "target_kcal": 600, "gym_label": None},
    ]
    monkeypatch.setattr(h, "current_plan", lambda *a, **k: fake_plan)

    out = h.handle_workout_on(1)  # 1 = Tuesday
    assert "check SugarWOD app" not in out, (
        f"handle_workout_on punted to the app despite the WOD being "
        f"present for Tuesday. Got:\n{out!r}")
    assert ("clean" in out.lower() or "ghd" in out.lower()
            or "emom" in out.lower() or "don't speak" in out.lower()), (
        f"the Tuesday WOD content didn't surface. Got:\n{out!r}")


def test_workout_on_still_app_message_when_no_gym_entry(synced_kobe,
                                                        monkeypatch):
    """Inverse guard: a CF day with no gym programming for that weekday
    at all (Sunday — omitted from the pull) still gets the honest
    'check app' message. The fallback must not fabricate a WOD."""
    from agents.the_scientist import handler as h

    fake_plan = [{"day_type": "rest", "target_kcal": 600,
                  "gym_label": None} for _ in range(6)]
    fake_plan.append({"day_type": "cf", "target_kcal": 1300,
                      "gym_label": "Sun 99"})   # Sunday CF, no gym entry
    monkeypatch.setattr(h, "current_plan", lambda *a, **k: fake_plan)

    out = h.handle_workout_on(6)  # 6 = Sunday (no gym entry in fixture)
    assert "check SugarWOD app" in out, (
        f"with no gym entry for the weekday, the honest app message must "
        f"remain. Got:\n{out!r}")
