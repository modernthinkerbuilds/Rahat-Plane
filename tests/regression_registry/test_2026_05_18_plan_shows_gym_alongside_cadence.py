"""Regression: /plan must show gym programming alongside cadence.

2026-05-18 user feedback — "I want to see BOTH at a glance. If
Monday is a rest day in my cadence but the gym posted Bench Press
1RM + a named WOD, /plan should surface that so I know what I'm
skipping (and can swap it in)."

Symptom (pre-fix): handle_show_plan rendered only the cadence — one
line per day showing day_type + target. The gym's programming was
invisible unless the day was already a CF day (in which case the
gym_label appeared in parens). Result: the user couldn't see what
they were missing on rest/Z2 days and had to ask separately.

Root cause: the rendering loop only used the cadence row's
gym_label, which is set ONLY for CF days. Non-CF days dropped the
gym data on the floor.

Fix: Day-10 D2. The rendering loop now also builds a weekday→GymDay
lookup from parse_gym_plan() and emits a sub-line on every day where
the gym programmed something AND that programming isn't already
visible in the main line. Sub-line shape:

  Mon: Active rest → ideal 600 kcal
     ⤷ gym today: Back squat 5x5 + "Furiosa"
       (skip per your plan, or `pick Mon for CrossFit` to swap)

This test pins:
  1. Non-CF day with synced gym programming shows the ⤷ sub-line.
  2. The sub-line includes the override hint for non-CF days.
  3. The sub-line surfaces blockers when present.
  4. CF days where cadence picked the gym day DON'T duplicate the
     sub-line (collapsed — main line already shows the gym_label).
  5. Days with no gym programming have NO sub-line.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


def _write_synced_plan(plan_path: Path) -> None:
    """Mon = clean (becomes rest in default cadence), Thu has the
    snatch blocker, Sun omitted to pin the no-sub-line case.

    Day-of-month labels are derived from the SAME week the renderer
    shows (week_bounds()'s Monday), not hardcoded. The /plan render is
    now date-aware (2026-06-21): it maps each rendered weekday to the
    GymDay whose 'MON 22'-style header date matches that day. A static
    'Mon 18' fixture would only line up during the week of the 18th, so
    we generate the labels from the rendered week to keep the test's
    intent — a synced week aligned to the plan — stable over time.
    """
    from agents.the_scientist.protocols import week_bounds
    monday, _ = week_bounds()
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    strengths_wods = [
        ("Back squat 5x5 @ 75% 1RM",
         "5 rounds for time: 400m run, 21 deadlifts, 12 bench press"),
        ("Bench press 3x5", "AMRAP 12 minutes Furiosa"),
        ("Front squat 5x3", "21-15-9 thrusters, pullups"),
        ("Snatch in strength 5x2 @ 70%",
         "5 rounds: 10 burpees, 200m run"),
        ("Deadlift 5x5 @ 80%",
         "For time: 50-40-30-20-10 wall balls"),
        ("Hero WOD: MURPH",
         "1 mile run, 100 pullups, 200 pushups, 300 squats, 1 mile run"),
    ]
    blocks = []
    for i, (name, (strength, wod)) in enumerate(zip(days, strengths_wods)):
        from datetime import timedelta
        label = f"{name} {(monday + timedelta(days=i)).day}"
        blocks.append((label, strength, wod))
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
    sci.PLAN_PATH = plan_path
    from agents.the_scientist import handler as h
    h.PLAN_PATH = plan_path
    return sci


# ─── 1. Plan output includes the gym sub-line marker ─────────────
def test_show_plan_contains_gym_sub_line_marker(synced_kobe):
    sci = synced_kobe
    out = sci.handle_show_plan(next_week=False)
    assert "⤷ gym today:" in out, (
        f"/plan rendering missing the gym sub-line marker. Day-10 "
        f"D2 regressed.\nOutput:\n{out}")


def test_show_plan_surfaces_gym_strength_in_sub_line(synced_kobe):
    """The sub-line composition uses _one_line_gym_summary which
    extracts strength header + WOD title. For our Monday fixture
    (Back squat strength), 'squat' must appear in the rendering."""
    sci = synced_kobe
    out = sci.handle_show_plan(next_week=False).lower()
    # The Mon strength is back squat; Tue is bench press; either
    # must be visible inline so the sub-line is meaningful.
    assert "squat" in out or "bench" in out, (
        f"sub-line lost gym strength signal. Output:\n{out}")


def test_show_plan_includes_override_hint_for_non_cf_days(synced_kobe):
    """The user needs the hint to know HOW to swap cadence into the
    gym pick — the literal `pick X for CrossFit` cue."""
    sci = synced_kobe
    out = sci.handle_show_plan(next_week=False)
    assert "pick " in out and "for CrossFit" in out, (
        f"override hint missing — user has no path to swap. "
        f"Output:\n{out}")


def test_show_plan_surfaces_blockers_in_sub_line(synced_kobe):
    """Thursday's snatch-in-strength is on Kobe's blacklist. The
    sub-line must surface 'blocked' + the tolerate hint so the user
    knows which day has a scaling decision waiting."""
    sci = synced_kobe
    out = sci.handle_show_plan(next_week=False).lower()
    assert "blocked" in out, (
        f"blocker surface lost in /plan sub-line. Output:\n{out}")
    assert "tolerate" in out, (
        f"tolerate hint lost in /plan sub-line. Output:\n{out}")


# ─── 2. Aligned-day collapse — no duplication ────────────────────
# F6 (2026-06-22): HARD PIN. The Kobe plan-render dup-line fix has landed
# (this was a stale xfail that had been XPASSing). On a CF-aligned day the
# gym_label is already in the main line, so the ⤷ sub-line collapses.
def test_aligned_cf_day_does_not_get_duplicate_sub_line(
        synced_kobe, monkeypatch):
    """When cadence is CF AND the cadence's gym_label matches the
    gym day's label, the gym_label is already in the main line — the
    sub-line collapses to avoid duplication.

    To pin this we force a CF pick on Monday, which makes cadence
    align with the gym pick. The Monday line should show
    `(Mon 18)` in the main line but NOT a `⤷ gym today: ...` sub-line.
    """
    sci = synced_kobe
    sci.handle_pick_days("Mon Tue Fri for crossfit", next_week=False)
    out = sci.handle_show_plan(next_week=False)
    # Locate Monday's stanza by its day HEADER, not "Mon: CrossFit" — when
    # Monday is already in the past it renders struck-through as
    # "Mon: ~~CrossFit~~ ⚠️ missed", which is STILL a CF cadence day and the
    # sub-line must still collapse. (Date-robust: 2026-06-23.)
    mon_idx = out.find("Mon:")
    assert mon_idx >= 0, f"Monday row missing. Output:\n{out}"
    tue_idx = out.find("Tue:", mon_idx + 1)
    assert tue_idx > mon_idx, (
        f"couldn't find Tue stanza after Mon. Output:\n{out}")
    mon_block = out[mon_idx:tue_idx]
    assert "CrossFit" in mon_block, (
        f"Monday wasn't picked as CF. Block:\n{mon_block}")
    assert "⤷ gym today:" not in mon_block, (
        f"aligned CF day rendered a sub-line — CF cadence days "
        f"collapse the sub-line because either the gym_label is "
        f"already in the main line, or state.py dropped the "
        f"gym_label and the alternative ('different gym day') hint "
        f"would lie. Block:\n{mon_block}")


# ─── 3. Days with no gym programming have NO sub-line ────────────
def test_no_sub_line_when_gym_has_no_entry(synced_kobe):
    """Sunday is missing from our fixture's pull. Sun's line should
    NOT have a `⤷ gym today:` follow-up."""
    sci = synced_kobe
    out = sci.handle_show_plan(next_week=False)
    sun_idx = out.find("Sun: ")
    if sun_idx < 0:
        pytest.skip("Sun row didn't render — fixture issue, not D2")
    # 200-char window after Sun: ; should NOT include the marker.
    sun_block = out[sun_idx:sun_idx + 200]
    assert "⤷ gym today:" not in sun_block, (
        f"Sun line emitted a gym sub-line despite no SugarWOD entry. "
        f"Block:\n{sun_block}")


# ─── 4. _one_line_gym_summary unit tests ─────────────────────────
class _FakeGymDay:
    """Minimal shape mirroring agents.the_scientist.protocols.GymDay."""
    def __init__(self, label, weekday, body, strength, blockers=None):
        self.label = label
        self.weekday = weekday
        self.body = body
        self.strength = strength
        self.blockers = blockers or []


def test_one_line_gym_summary_combines_strength_and_wod():
    from agents.the_scientist.handler import _one_line_gym_summary
    day = _FakeGymDay(
        label="Mon 18", weekday="MON",
        strength=" Strength\nBack squat 5x5 @ 75% 1RM\n",
        body=("Strength\nBack squat 5x5 @ 75% 1RM\n0 results\n"
              "WOD\nFuriosa AMRAP 12\n0 results\n"))
    out = _one_line_gym_summary(day)
    assert "Back squat" in out
    assert "Furiosa" in out
    assert " + " in out, f"composition glue missing in {out!r}"


def test_one_line_gym_summary_handles_strength_only():
    from agents.the_scientist.handler import _one_line_gym_summary
    day = _FakeGymDay(
        label="Tue 19", weekday="TUE",
        strength=" Strength\nDeadlift 5x5\n",
        body="Strength\nDeadlift 5x5\n0 results\n")
    out = _one_line_gym_summary(day)
    assert "Deadlift" in out


def test_one_line_gym_summary_handles_empty_day_gracefully():
    """Defensive — None / empty must return empty string, not crash."""
    from agents.the_scientist.handler import _one_line_gym_summary
    assert _one_line_gym_summary(None) == ""
    empty = _FakeGymDay(label="", weekday="", body="", strength="")
    assert _one_line_gym_summary(empty) == ""
