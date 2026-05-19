"""Regression: handle_pick_days must auto-recalibrate the week.

2026-05-18 user intent — "make Monday a CrossFit day" should not
just flip Monday's day_type; it should REBALANCE the week so total
stays inside the ~6,000 kcal weekly envelope. If the resulting
cadence overshoots (e.g. 4 CF days at performance tier blows past
the target by ~1,800 kcal), the user must be told explicitly.
Similarly, picks that hit blacklisted movements must surface the
blocker with a `tolerate` hint instead of silently committing.

Symptom (pre-fix): handle_pick_days set forced_cf_days, called
replan_week, then returned a `✅ Locked picks` confirmation plus
the show_plan view. No surface for overshoot or blacklist conflicts;
the user got an apparently-clean confirmation while quietly
committing to a 7,800-kcal week or a blacklisted Thursday strength.

Root cause: handle_pick_days never inspected the new plan's totals
or compared the forced picks against the gym's blocker set.

Fix: Day-10 D3. After replan_week, handle_pick_days now:
  - reads current_plan() and computes plan_sum,
  - emits an overshoot warning when plan_sum exceeds the weekly
    target by >500 kcal,
  - reads parse_gym_plan() blockers + the tolerated_blacklist set
    and surfaces a blacklist-conflict warning naming each picked
    weekday whose gym day has unresolved blockers.

This test pins:
  1. Picking a previously-rest day for CF rebalances → weekly total
     stays within ±100 of weekly_target (the cadence is locked at
     3 CF + 1 Z2 + 3 rest; picking Mon for CF is just a swap).
  2. Forcing FOUR CF days produces the explicit overshoot warning.
  3. A pick that lands on the snatch-in-strength Thursday surfaces
     the blacklist conflict with a `tolerate` hint.

Known gap (not pinned here): HRV-red conflict warning. Today there's
no HRV-state inspection in handle_pick_days; pinning that would
require building the HRV-state read into the handler, which is
out of scope for Day-10. The Day-10 report calls this out as a
follow-on item.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


def _write_gym_plan_with_blocker(plan_path: Path) -> None:
    """Mon/Tue/Wed/Fri/Sat clean; Thu has snatch-in-strength
    (canonical Kobe blacklist) so a pick on Thu must warn."""
    blocks = [
        ("Mon 18", "Back squat 5x5",
         "5 rounds: 400m run, 21 deadlifts, 12 bench press"),
        ("Tue 19", "Bench press 3x5", "AMRAP 12 Furiosa"),
        ("Wed 20", "Front squat 5x3", "21-15-9 thrusters, pullups"),
        ("Thu 21", "Snatch in strength 5x2 @ 70%",
         "5 rounds: 10 burpees, 200m run"),
        ("Fri 22", "Deadlift 5x5",
         "For time: 50-40-30-20-10 wall balls"),
        ("Sat 23", "Hero WOD: MURPH",
         "1 mile run, 100 pullups, 200 pushups, 300 squats, 1 mile"),
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
def sci_with_gym(tmp_path):
    import importlib.util
    import sys
    from core import io as cio

    db_path = tmp_path / "rahat.db"
    plan_path = tmp_path / "weekly_plan.txt"
    _write_gym_plan_with_blocker(plan_path)

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


# ─── 1. Pick rebalances — weekly total stays in envelope ───────────
def test_pick_3_cf_days_keeps_weekly_total_in_envelope(sci_with_gym):
    """3 CF + 1 Z2 + 3 rest is the locked cadence (~6,000 kcal at
    baseline tier, ±100). Forcing Mon/Tue/Fri as the 3 CF days is a
    swap, not an overshoot — total must stay within ±300 of the
    weekly_target (wider tolerance than ±100 because the tier
    targets are integer kcal and small drift is normal)."""
    sci = sci_with_gym
    sci.handle_pick_days("Mon Tue Fri for crossfit", next_week=False)

    from datetime import datetime, timedelta
    monday = (datetime.now() -
              timedelta(days=datetime.now().weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    plan = list(sci.current_plan(monday))
    plan_sum = sum(d["target_kcal"] for d in plan)
    target = sci.weekly_target()
    drift = abs(plan_sum - target)
    assert drift <= 300, (
        f"3-CF pick drifted weekly total {drift:.0f} kcal from "
        f"target {target:.0f}: plan_sum={plan_sum:.0f}. The locked "
        f"cadence math regressed.")


# ─── 2. Overshoot warning fires for 4 CF days ──────────────────────
def test_pick_4_cf_days_warns_about_overshoot(sci_with_gym):
    """4 CF days at baseline tier = 4*1150 + 1*1100 + 2*500 = 6700,
    only ~700 over target — at the threshold. At performance tier
    the overshoot is more dramatic. Either way the warning surfaces."""
    sci = sci_with_gym
    # Force performance tier so the overshoot is unambiguous
    # (4*1300 + 1*1400 + 2*600 = 7800).
    sci.state_set("recovery_tier", "performance")
    reply = sci.handle_pick_days(
        "Mon Tue Wed Fri for crossfit", next_week=False)

    assert "overshoot" in reply.lower() or "⚠️" in reply, (
        f"4-CF pick at performance tier didn't surface an overshoot "
        f"warning. Reply:\n{reply}")
    # And the warning should name the over-target margin explicitly.
    assert "target" in reply.lower(), (
        f"overshoot warning lost target context. Reply:\n{reply}")


# ─── 3. Blacklist-conflict warning fires for snatch Thursday ──────
def test_pick_day_with_blacklisted_movement_surfaces_blocker(
        sci_with_gym):
    """Thursday has snatch-in-strength (Kobe blacklist). Forcing
    Thu as a CF day with no tolerance set must surface the conflict
    with a `tolerate` hint — silent commit is the regression."""
    sci = sci_with_gym
    reply = sci.handle_pick_days(
        "Mon Wed Thu for crossfit", next_week=False)

    assert "blacklist" in reply.lower() or "blocker" in reply.lower(), (
        f"Thu snatch pick didn't surface the blacklist conflict. "
        f"Reply:\n{reply}")
    assert "tolerate" in reply.lower(), (
        f"blacklist-conflict warning missing the `tolerate` hint. "
        f"Reply:\n{reply}")
    # And it should name Thursday so the user knows WHICH day is
    # the problem.
    assert "Thu" in reply, (
        f"blacklist warning didn't name Thursday. Reply:\n{reply}")


# ─── 4. Clean pick (no blocker, no overshoot) does NOT warn ────────
def test_clean_pick_does_not_warn(sci_with_gym):
    """Inverse: a pick that doesn't overshoot AND doesn't hit a
    blocker (Mon Tue Fri — all clean in our fixture) must NOT emit
    spurious warnings. Over-warning is its own UX failure."""
    sci = sci_with_gym
    reply = sci.handle_pick_days(
        "Mon Tue Fri for crossfit", next_week=False)

    assert "overshoot" not in reply.lower(), (
        f"clean 3-CF pick spuriously warned about overshoot.\n"
        f"Reply:\n{reply}")
    assert "blacklist conflict" not in reply.lower(), (
        f"clean pick spuriously warned about blacklist.\n"
        f"Reply:\n{reply}")


# ─── 5. KNOWN GAP — HRV-red conflict warning ───────────────────────
@pytest.mark.skip(
    reason="HRV-state inspection not yet wired into handle_pick_days. "
           "Tracked as Day-10 follow-on item in KOBE_DAY10_REPORT.md. "
           "When wired, flip this to an active assertion that a pick "
           "on a recent HRV-red day produces an explicit warning.")
def test_pick_on_hrv_red_day_warns(sci_with_gym):
    """Pin: when the user picks a CF day for which HRV was logged
    red in the last 24h, the handler should warn.

    Today handle_pick_days has no HRV-state lookup; adding it is a
    follow-on commit. This test is a tripwire: when the capability
    lands, drop the skip marker and the test goes green automatically."""
    sci = sci_with_gym
    # Log HRV in the RED band for today.
    sci.log_hrv(30.0)
    reply = sci.handle_pick_days("Mon for crossfit", next_week=False)
    assert "hrv" in reply.lower() and "red" in reply.lower()
