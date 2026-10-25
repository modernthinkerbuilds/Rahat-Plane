"""Regression pin (2026-10-25, Kipchoge S0 review fix 4b): one reader
of the night before, for Huberman and Kipchoge.

THE BUG: Huberman read `asleep_s` of the session ending today and
Kipchoge's first cut read `asleep_s` too; on the Watch's staged nights
that column is 0 (the bridge now fills it, pin 4a, but the old rows
stay as they were). The September trip also stored several nights
twice at a three-hour shifted clock, and a night the Watch joined to an
afternoon nap ends after a late-morning run.

THE PIN (bridges/healthkit/sleep.py): `night_before(con, at)` gives the
hours from the stages when the session has them, else `asleep_s`; the
longest session starting within 24 h before `at` (an evening moment's
night started 20 h earlier); a shifted duplicate counts once; a
session ending after `at` still counts. Huberman's context carries
`sleep_hours_last_night` on a staged night; Kipchoge's run carries the
same number.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from bridges.healthkit import sleep as hk_sleep  # noqa: E402

ROWS = [
    # (start, end, asleep_s, core_s, deep_s, rem_s)
    ("2026-10-20 23:10:00", "2026-10-21 06:40:00", 0.0, 14400.0, 2880.0, 6120.0),          # staged: 6.5 h
    ("2026-10-21 02:10:00", "2026-10-21 09:40:00", 0.0, 14400.0, 2880.0, 6120.0),          # the same night, shifted 3 h
    ("2026-10-21 23:30:00", "2026-10-22 16:50:00", 3600.0, 12600.0, 2700.0, 4500.0),       # a night joined to a nap: 5.5 h staged
    ("2026-10-22 13:00:00", "2026-10-22 13:40:00", 2400.0, None, None, None),              # a nap of its own, unstaged
]


def _store(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sleep_sessions (sleep_start TEXT PRIMARY KEY, sleep_end TEXT, asleep_s REAL, in_bed_s REAL, "
                "core_s REAL, deep_s REAL, rem_s REAL, awake_s REAL, source TEXT, raw_json TEXT)")
    for s, e, a, c, d, r in ROWS:
        con.execute("INSERT INTO sleep_sessions VALUES (?,?,?,?,?,?,?,?,?,?)", (s, e, a, 0.0, c, d, r, 0.0, "Watch", "{}"))
    con.commit()
    return con


def test_the_reader_takes_the_stages_dedupes_the_shift_and_keeps_a_night_that_ends_after_the_moment(tmp_path):
    con = _store(tmp_path / "h.db")
    late_morning = hk_sleep.night_before(con, datetime(2026, 10, 22, 10, 40))
    assert late_morning["hours"] == pytest.approx(5.5) and late_morning["method"] == hk_sleep.METHOD_STAGES
    assert late_morning["end"] == "2026-10-22 16:50", "the night the Watch joined to a nap ends after the run and still counts"
    assert late_morning["prior_hours"] == pytest.approx(6.5) and late_morning["two_night_avg_h"] == pytest.approx(6.0)
    assert late_morning["sessions"] == 2, "the shifted copy of the earlier night counts once"
    evening = hk_sleep.night_before(con, datetime(2026, 10, 22, 19, 30))
    assert evening["hours"] == pytest.approx(5.5), "an evening moment's night started 20 h earlier; the nap does not outrank it"
    nothing = hk_sleep.night_before(con, datetime(2026, 11, 5, 8, 0))
    assert nothing["hours"] is None and nothing["method"] == ""


def test_huberman_s_context_carries_the_night_on_a_staged_row(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    for k in ("HUBERMAN_AUTOCOOL", "HUBERMAN_PROFILE_JSON", "HUBERMAN_STORE_JSON", "RAHAT_HUBERMAN_DB"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "vault").mkdir()
    from agents.huberman import context, state
    state.profile_path().write_text(json.dumps({"default_minutes": 15, "hotspots": [], "equipment": []}))
    monkeypatch.setattr(context, "gym_wod_today", lambda now=None: None)
    monkeypatch.setattr(context, "plan_day_type", lambda now=None: "rest")
    con = _store(state.db_path())
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS workouts_hk (workout_id TEXT PRIMARY KEY, name TEXT, start TEXT, end TEXT,
        duration_s REAL, active_kcal REAL, avg_hr REAL, max_hr REAL, distance_km REAL, raw_json TEXT)""")
    con.commit()
    con.close()
    ctx = context.gather(now=datetime(2026, 10, 21, 21, 30))
    assert ctx["sleep_hours_last_night"] == 6.5, "the 9:30 pm cooldown sees the staged night (it read asleep_s 0 before)"


def test_kipchoge_reads_the_same_night(tmp_path):
    from agents.kipchoge import ingest
    con = _store(tmp_path / "h.db")
    got = ingest.sleep_before(con, datetime(2026, 10, 22, 10, 40))
    assert got["night_before_h"] == pytest.approx(5.5) and got["two_night_avg_h"] == pytest.approx(6.0)
    assert got["method"] == hk_sleep.METHOD_STAGES and got["sessions"] == 2
