"""bridges.healthkit.sleep — the night before a moment, read once for
every coach (2026-10-25, the Kipchoge S0 review, fix 4).

The Watch's staged sleep reaches the store with `asleep_s` 0 and the
time asleep in `core_s + deep_s + rem_s` (50 of 59 nights since
September); `asleep_s` carries unstaged sleep (a nap the Watch did not
stage). Huberman read `asleep_s` and its sleep line was empty on those
nights; Kipchoge needs the night before a run. One reader for both:

  * a session's hours are its stage sum when it has one, else `asleep_s`;
  * the night before `at` is the longest session STARTING within 24 h
    before `at` (an evening run's night started 20 h earlier; a night
    the Watch joined to an afternoon nap ends after a morning run and
    still counts: its stages are the night's);
  * the night before that is the longest starting 24–48 h before;
  * a session the bridge stored twice (the same stages at a shifted
    clock: the September trip) counts once.

The bridge now also fills `asleep_s` from the stages at ingest, so new
rows carry the number both ways; this reader makes the old rows right
without a live rewrite.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

NIGHT_START_WINDOW_H = 24
PRIOR_NIGHT_WINDOW_H = 48
METHOD_STAGES = "the Watch's sleep stages"
METHOD_ASLEEP = "time asleep"


def hours_of(asleep_s, core_s, deep_s, rem_s) -> tuple[float, bool]:
    """(hours, staged): the stage sum when there is one, else asleep_s."""
    stages = sum(float(x or 0.0) for x in (core_s, deep_s, rem_s))
    if stages > 0:
        return stages / 3600.0, True
    return float(asleep_s or 0.0) / 3600.0, False


def _dt(s) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def sessions_before(con: sqlite3.Connection, at: datetime, *, hours_back: float = PRIOR_NIGHT_WINDOW_H) -> list[dict]:
    """The distinct sessions starting within hours_back before `at`,
    oldest first: {start, end, hours, staged, before_h}."""
    lo = (at - timedelta(hours=hours_back)).strftime("%Y-%m-%d %H:%M:%S")
    hi = at.strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = con.execute("SELECT sleep_start, sleep_end, asleep_s, core_s, deep_s, rem_s FROM sleep_sessions "
                           "WHERE sleep_start >= ? AND sleep_start < ? ORDER BY sleep_start", (lo, hi)).fetchall()
    except sqlite3.Error:
        return []
    seen: set[tuple] = set()
    out: list[dict] = []
    for s, e, asleep, core, deep, rem in rows:
        st, en = _dt(s), _dt(e)
        if st is None or en is None:
            continue
        hours, staged = hours_of(asleep, core, deep, rem)
        if hours <= 0:
            continue
        stages = sum(float(x or 0.0) for x in (core, deep, rem))
        key = (round((en - st).total_seconds()), round(stages), round(float(asleep or 0.0)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"start": st, "end": en, "hours": hours, "staged": staged,
                    "before_h": (at - st).total_seconds() / 3600.0})
    return out


def night_before(con: sqlite3.Connection, at: datetime) -> dict:
    """The night before `at` and the one before that, in hours:
    {hours, end, start, method, prior_hours, two_night_avg_h, sessions}.
    `hours` is None when no session is on file."""
    night: dict | None = None
    prior: dict | None = None
    sessions = sessions_before(con, at)
    for ses in sessions:
        if ses["before_h"] <= NIGHT_START_WINDOW_H:
            if night is None or ses["hours"] > night["hours"]:
                night = ses
        elif prior is None or ses["hours"] > prior["hours"]:
            prior = ses
    two = (night["hours"] + prior["hours"]) / 2.0 if night and prior else None
    return {
        "hours": round(night["hours"], 2) if night else None,
        "start": night["start"].strftime("%Y-%m-%d %H:%M") if night else None,
        "end": night["end"].strftime("%Y-%m-%d %H:%M") if night else None,
        "method": (METHOD_STAGES if night["staged"] else METHOD_ASLEEP) if night else "",
        "prior_hours": round(prior["hours"], 2) if prior else None,
        "two_night_avg_h": round(two, 2) if two is not None else None,
        "sessions": len(sessions),
    }
