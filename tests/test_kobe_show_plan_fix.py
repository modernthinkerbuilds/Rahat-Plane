"""Day-9 Bug 1 contract pin: handle_show_plan never lies "no gym plan
synced" when parse_gym_plan() returns real data.

Production-incident root cause (2026-05-17)
-------------------------------------------
`replan_week()` writes a transient state flag
`user_state.plan_fallback_{week_key} = "1"` at the moment it picks CF
days, snapshotting "no gym plan synced (yet) OR too many blockers."
The flag is never re-evaluated. When the user runs the SugarWOD
bookmarklet AFTER `replan_week()`, the gym data is now there, but the
flag is still stale "1". `handle_show_plan(next_week=True)` reads the
stale flag and emits:

  "⚠️ No gym plan synced — using default Mon/Wed/Fri cadence."

This file pins the post-fix contract: `handle_show_plan` must derive
fallback status from the CURRENT `parse_gym_plan()` output, not from
the stale flag.

Every test is offline — hermetic tmp PLAN_PATH + tmp DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core import io as cio


ROOT = Path(__file__).resolve().parent.parent


def _write_synced_plan(plan_path: Path) -> None:
    """Write a known SugarWOD-shape weekly_plan.txt with 7 day blocks,
    each with a non-empty body that contains canonical CF movements
    (Bench Press / Back Squat / Deadlift — none of which are on
    Kobe's blacklist of handstand / muscle-up / OHS / snatch in
    strength / partner WOD)."""
    # Use exactly the day-label shape parse_gym_plan() expects (matches
    # the existing test fixtures elsewhere in the suite).
    days = ["Mon 18", "Tue 19", "Wed 20", "Thu 21", "Fri 22",
            "Sat 23", "Sun 24"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength", "Back squat 5x5 @ 75% of 1RM", "", "0 results",
            " WOD", "5 rounds for time: 400m run, 21 deadlifts, "
            "12 bench press", "", "0 results",
        ]))
    plan_path.write_text("\n".join(blocks) + "\n")


@pytest.fixture
def synced_kobe(tmp_path):
    """Per-test Kobe with a tmp PLAN_PATH carrying a real synced plan
    and a tmp DB that's been seeded with the stale flag — i.e., the
    exact production state where Bug 1 fired."""
    import importlib.util
    import sys

    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    _write_synced_plan(plan_path)

    # Schema for the tables handle_show_plan reads.
    con = sqlite3.connect(db_path)
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
    con.close()
    cio.DB_PATH = db_path

    spec = importlib.util.spec_from_file_location(
        "sci", ROOT / "agents" / "the_scientist" / "main.py")
    sci = importlib.util.module_from_spec(spec)
    sys.modules["sci"] = sci
    spec.loader.exec_module(sci)
    # PLAN_PATH must be rebound on BOTH main (legacy star-import) AND
    # handler (where parse_gym_plan() resolves it). Setting only sci
    # is a footgun — handler.parse_gym_plan reads handler.PLAN_PATH
    # via its own module globals and ignores the main-side rebind.
    sci.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path

    return sci, db_path


def _seed_stale_fallback_flag_for_next_week(sci) -> str:
    """Set plan_fallback_{next_monday} = '1' in user_state, matching
    the production state where replan_week ran BEFORE the user synced
    the bookmarklet. Returns the week_key string for assertions."""
    from datetime import datetime, timedelta
    today = datetime.now()
    next_monday = (today - timedelta(days=today.weekday())) \
                  + timedelta(days=7)
    week_key = next_monday.strftime("%Y-%m-%d")
    sci.state_set(f"plan_fallback_{week_key}", "1")
    return week_key


def test_handle_show_plan_does_not_lie_no_plan_synced(synced_kobe):
    """THE NAMED REGRESSION TEST for the 2026-05-17 production bug.

    Setup mirrors production state:
      - PLAN_PATH has a real synced weekly_plan.txt (parse_gym_plan
        returns 7 days with non-empty bodies)
      - user_state.plan_fallback_{next_monday} = '1' (the stale flag
        replan_week set BEFORE the sync)
    Expected:
      - handle_show_plan(next_week=True) recomputes is_fallback from
        the CURRENT gym data, sees 7 clean days, returns the normal
        plan view with NO "No gym plan synced" warning.
    """
    sci, _ = synced_kobe
    _seed_stale_fallback_flag_for_next_week(sci)

    out = sci.handle_show_plan(next_week=True)

    assert "No gym plan synced" not in out, (
        f"handle_show_plan(next_week=True) still lies 'No gym plan "
        f"synced' even though parse_gym_plan() returns real data. "
        f"Bug 1 (Day-9, 2026-05-17 production incident) regressed.\n\n"
        f"Output was:\n{out}"
    )


def test_handle_show_plan_contains_day_labels_from_file(synced_kobe):
    """The output must include the weekday names from the rendering
    loop — Mon, Tue, Wed, Thu, Fri, Sat, Sun. Pre-fix the output
    contained the same names (rendering iterates all 7 weekdays),
    so this test passes pre-AND-post-fix; it's the
    "structural-integrity didn't regress" anchor."""
    sci, _ = synced_kobe
    _seed_stale_fallback_flag_for_next_week(sci)

    out = sci.handle_show_plan(next_week=True)

    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert day in out, (
            f"weekday label {day!r} missing from handle_show_plan "
            f"output. Rendering loop or WEEKDAY_NAME order regressed.\n\n"
            f"Output was:\n{out}"
        )


def test_handle_show_plan_honors_no_sync_when_plan_path_missing(
        synced_kobe, tmp_path):
    """Inverse: if the synced file is GONE (production state before
    the user ever ran the bookmarklet), the "No gym plan synced"
    warning must still fire. Verifies the fix didn't silence a true
    fallback signal."""
    sci, _ = synced_kobe
    # Point PLAN_PATH at a non-existent file to mimic no-sync state.
    # Same footgun as in the fixture: must rebind on BOTH modules.
    missing = tmp_path / "does_not_exist.txt"
    sci.PLAN_PATH = missing
    from agents.the_scientist import handler as h
    h.PLAN_PATH = missing
    _seed_stale_fallback_flag_for_next_week(sci)

    out = sci.handle_show_plan(next_week=True)

    assert "No gym plan synced" in out, (
        "When PLAN_PATH points at a missing file, the 'No gym plan "
        "synced' warning MUST fire — that's the true-positive case "
        "the fix is supposed to preserve. The fix went too far if "
        "this assertion fails.\n\n"
        f"Output was:\n{out}"
    )


def test_handle_show_plan_works_for_current_week_too(synced_kobe):
    """The fix applies to both next_week=True and next_week=False —
    same code path. Belt-and-suspenders pin so a future refactor
    that special-cases next_week=True doesn't regress the current
    week's view."""
    sci, _ = synced_kobe
    # Set the stale flag for THIS week's monday.
    from datetime import datetime, timedelta
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday()))
    week_key = monday.strftime("%Y-%m-%d")
    sci.state_set(f"plan_fallback_{week_key}", "1")

    out = sci.handle_show_plan(next_week=False)

    assert "No gym plan synced" not in out, (
        f"handle_show_plan(next_week=False) regressed under the same "
        f"stale-flag pattern.\n\nOutput was:\n{out}"
    )
