"""Regression: pace/walk nudges must be capped per day (2026-05-25).

Symptom (from the live transcript): on a day spent behind pace, the
walk nudge fired once every hour — 5pm, 6pm, 7pm, 8pm — because the
only throttle was a PER-HOUR slot (`walk_17`, `walk_18`, …). Each new
hour was a fresh slot, so a lagging day produced a nudge every hour
inside the 10:00–20:00 window.

Root cause: `maybe_walk_nudge()` deduped per hour but had no per-DAY
cap. See `agents/the_scientist/handler.py` and the nudge audit.

Fix: a per-day cap on SENT walk nudges (`WALK_NUDGE_DAILY_CAP`,
default 2), counted via a dedicated `walk_sent` marker in `nudge_log`
so throttle markers don't inflate the count.

This test pins:
  1. A behind-pace day that would otherwise fire every hour fires at
     most `WALK_NUDGE_DAILY_CAP` times.
  2. The cap counts only actually-sent nudges (the inverse — fewer
     than the available hours — is the whole point).
"""
from __future__ import annotations

import contextlib
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
        # intents, governance_log, week_preferences are created by
        # state._db() with their canonical schemas — don't shadow them.
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


class _FrozenDatetime(datetime):
    _frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen if cls._frozen is not None else datetime.now(tz)


@contextlib.contextmanager
def _frozen(sci_mod, when: datetime):
    _FrozenDatetime._frozen = when
    targets = [(sci_mod, getattr(sci_mod, "datetime", None))]
    for name in ("agents.the_scientist.handler",
                 "agents.the_scientist.state",
                 "agents.the_scientist.protocols"):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "datetime"):
            targets.append((m, m.datetime))
    for m, _ in targets:
        m.datetime = _FrozenDatetime
    try:
        yield
    finally:
        for m, original in targets:
            if original is not None:
                m.datetime = original
        _FrozenDatetime._frozen = None


def test_walk_nudge_capped_per_day(sci):
    """A day spent fully behind pace must fire at most
    WALK_NUDGE_DAILY_CAP walk nudges, not one per hour."""
    from agents.the_scientist import state as st
    from agents.the_scientist.protocols import WALK_NUDGE_DAILY_CAP

    # Monday 2026-05-04; force it (weekday 0) to be a CrossFit day so
    # the day has a non-zero target and the pace check engages.
    monday = datetime(2026, 5, 4)
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=5,
                 unavailable_days=[])

    fired = []
    # Afternoon/evening hours (>=14 clears the CF morning guard); burn
    # stays 0 the whole time, so every hour is "behind pace".
    for hour in (15, 16, 17, 18, 19, 20):
        with _frozen(sci, datetime(2026, 5, 4, hour, 0)):
            msg = sci.maybe_walk_nudge()
        if msg is not None:
            fired.append((hour, msg))

    assert len(fired) == WALK_NUDGE_DAILY_CAP, (
        f"expected the daily cap ({WALK_NUDGE_DAILY_CAP}) walk nudges "
        f"across 6 behind-pace hours, got {len(fired)}: "
        f"{[h for h, _ in fired]}. Per-hour throttling alone regressed "
        f"back to one-nudge-per-hour spam.")
    # Every fired message is the pace-check nudge.
    for _, msg in fired:
        assert "Pace check" in msg, msg


def test_walk_sent_counter_tracks_sends(sci):
    """The cap is driven by a dedicated 'walk_sent' counter so that
    per-hour throttle markers (walk_HH) never inflate it."""
    from agents.the_scientist import state as st
    from agents.the_scientist.protocols import WALK_NUDGE_DAILY_CAP

    monday = datetime(2026, 5, 4)
    st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=5,
                 unavailable_days=[])
    for hour in (15, 16, 17, 18, 19, 20):
        with _frozen(sci, datetime(2026, 5, 4, hour, 0)):
            sci.maybe_walk_nudge()

    assert st.nudge_count("walk_sent", "2026-05-04") == WALK_NUDGE_DAILY_CAP
