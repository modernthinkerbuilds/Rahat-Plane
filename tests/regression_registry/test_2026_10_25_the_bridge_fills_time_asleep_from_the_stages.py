"""Regression pin (2026-10-25, Kipchoge S0 review fix 4a): the bridge
fills `asleep_s` from the stages.

THE BUG: Health Auto Export sends the Watch's staged nights with
`asleep` 0 (or absent) and the time in `core`, `deep` and `rem`; the
bridge stored exactly that, so 50 of 59 nights since September carried
`asleep_s` 0 and every reader of that column (Huberman's sleep line)
saw no sleep.

THE PIN: a staged session lands with `asleep_s` = core + deep + rem; a
session that reports its own time asleep keeps it; a session with
neither stays 0; the stage columns are untouched either way.
"""
from __future__ import annotations

import os
import sqlite3

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from bridges.healthkit.ingest import ingest_payload  # noqa: E402


def _payload(rows: list[dict]) -> dict:
    return {"data": {"metrics": [{"name": "sleep_analysis", "units": "hr", "data": rows}], "workouts": []}}


def test_a_staged_night_gets_its_time_asleep_from_the_stages():
    con = sqlite3.connect(":memory:")
    staged = {"sleepStart": "2026-10-20 23:10:00 -0700", "sleepEnd": "2026-10-21 06:40:00 -0700", "asleep": 0,
              "core": 4.0, "deep": 0.8, "rem": 1.7, "awake": 0.5, "inBed": 7.5, "source": "Watch"}
    own = {"sleepStart": "2026-10-21 23:00:00 -0700", "sleepEnd": "2026-10-22 06:00:00 -0700", "asleep": 6.5,
           "core": 4.5, "deep": 0.7, "rem": 1.3, "awake": 0.3, "source": "Watch"}
    bare = {"sleepStart": "2026-10-22 23:00:00 -0700", "sleepEnd": "2026-10-23 05:00:00 -0700", "source": "an app"}
    s = ingest_payload(_payload([staged, own, bare]), con)
    assert s["sleep_sessions"] == 3
    rows = {r[0][:10]: r for r in con.execute("SELECT sleep_start, asleep_s, core_s, deep_s, rem_s FROM sleep_sessions").fetchall()}
    assert rows["2026-10-20"][1] == (4.0 + 0.8 + 1.7) * 3600.0, "the stages fill the time asleep"
    assert rows["2026-10-20"][2:] == (4.0 * 3600, 0.8 * 3600, 1.7 * 3600), "the stage columns stay"
    assert rows["2026-10-21"][1] == 6.5 * 3600.0, "a session's own time asleep is kept"
    assert not rows["2026-10-22"][1], "nothing to fill from"
