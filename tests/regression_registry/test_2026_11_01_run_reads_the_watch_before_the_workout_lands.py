"""Regression (2026-11-01): `/run` answered with a run four days old while
that morning's run sat in the Watch's minute data.

LIVE: on Sunday Nov 1, after a morning run, the athlete sent Kipchoge
`/run` and got Wednesday's analysis. The phone's workouts export was on
"Previous 7 Days", which sends finished days only, so the run's workout
file could not land before midnight; the minute export had already
brought the run in (the running-form series the Watch writes only
inside a run). `/run` read only the workouts table, so it fell back to the
newest run there, without a word about the newer one. The provisional
analysis from the minute data (heart rate and time only) existed, but
only the clock in "all" mode used it, and the owner runs "weekend".

Fix (new_plane/kipchoge_runner/bot.py, agents/kipchoge/pipeline.py):
`/run`, `/run today` and `/run yesterday` look at the minute data for a
run that is over and has no workout in the store yet. The newest such
run, if newer than the last landed one, answers as the provisional
analysis, says where pace and splits will come from, and points to the
last full analysis. It is marked sent, so the clock never repeats it.
Once the workout lands, `/run` is the full analysis again.

The provisional template had never rendered a number: its records are
`prov:<start utc>:<path>` and it looked them up as `run:<id>:<path>`,
and the id held a space, which no `{{r:...}}` placeholder can carry.
The id is now `prov:20261101T171700` (agents/kipchoge/ingest.py) and the
template reads the right prefix (agents/kipchoge/render.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from new_plane.kipchoge_runner import bot
from tests.kipchoge.fixtures import load_fixture, synthetic_run
from tests.kipchoge.test_bot import ME, NOW, world  # noqa: F401  (the fixture: three landed runs, the last on Oct 18)

TODAY = NOW.strftime("%Y-%m-%d")                     # Saturday Oct 24, 09:30
YESTERDAY = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")


def _watch(start: datetime, minutes: int, hr: float = 143.0) -> dict:
    """The Watch's minute series for a run, in the store with no workout file;
    the session Huberman's detector reads from it."""
    from agents.kipchoge import state
    con = state.connect()
    con.executemany("INSERT INTO raw_vitals (timestamp, metric_type, value) VALUES (?, 'heart_rate', ?)",
                    [((start + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S"), hr) for m in range(minutes)])
    con.commit()
    con.close()
    end = start + timedelta(minutes=minutes)
    return {"name": "Outdoor Run", "start": start.strftime("%Y-%m-%d %H:%M:%S"), "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "minutes": minutes, "avg_hr": hr, "max_hr": hr + 6, "kind": "run", "inferred": True}


def _sessions(monkeypatch, by_day: dict[str, list[dict]]) -> None:
    monkeypatch.setattr(bot, "_vitals_sessions", lambda now: by_day.get(now.strftime("%Y-%m-%d"), []))


def test_run_answers_with_this_mornings_run_before_its_workout_lands(world, monkeypatch):
    _sessions(monkeypatch, {TODAY: [_watch(datetime(2026, 10, 24, 7, 0), 70)]})
    out = bot.process_message(ME, "/run", NOW)
    assert out.startswith("Provisional (the Watch's minute data; the workout file hasn't landed)"), out
    assert bot.UNLANDED_NOTE in out and "The last full analysis: /run 2026-10-18." in out, out
    # the analysis itself: the type, the grade and the heart rate, filled from the run's records
    body = out.split("\n", 1)[1]
    assert "grade" in body and "bpm" in body and "{{" not in body, out
    assert bot.process_message(ME, "/run today", NOW).startswith("Provisional"), "today's run, not 'No run on'"


def test_once_the_workout_lands_run_is_the_full_analysis(world, monkeypatch):
    _sessions(monkeypatch, {TODAY: [_watch(datetime(2026, 10, 24, 7, 0), 70)]})
    from agents.kipchoge import state
    con = state.connect()
    load_fixture(con, synthetic_run("R-D", datetime(2026, 10, 24, 7, 0, 30), 70, km=9.6, hr_first=142.0, hr_second=144.0))
    con.close()
    out = bot.process_message(ME, "/run", NOW)
    assert not out.startswith("Provisional") and "2026-10-24" in out, out


def test_the_clock_never_repeats_what_run_already_showed(world, monkeypatch):
    monkeypatch.setenv("KIPCHOGE_PROACTIVE", "1")
    _sessions(monkeypatch, {TODAY: [_watch(datetime(2026, 10, 24, 7, 0), 70)]})
    assert bot.process_message(ME, "/run", NOW).startswith("Provisional")
    sent: list[str] = []
    out = bot.tick_once(NOW + timedelta(minutes=1), send=sent.append, db_path=None)
    assert not [e for e in out if e.get("stage") == "provisional" and e.get("sent")], out
    assert not any(s.startswith("Provisional") for s in sent), sent


def test_yesterdays_unlanded_run_answers_when_nothing_newer_landed(world, monkeypatch):
    _sessions(monkeypatch, {YESTERDAY: [_watch(datetime(2026, 10, 23, 17, 0), 45)]})
    assert bot.process_message(ME, "/run", NOW).startswith("Provisional")
    assert bot.process_message(ME, "/run yesterday", NOW).startswith("Provisional")


@pytest.mark.parametrize("ask,expect", [("/run", "2026-10-18"), ("/run tomorrow", "No run on tomorrow in the store.")])
def test_a_run_still_going_or_no_minute_data_falls_back_to_the_store(world, monkeypatch, ask, expect):
    _sessions(monkeypatch, {TODAY: [_watch(datetime(2026, 10, 24, 8, 30), 55)]})    # ends 09:25: five minutes ago
    assert expect in bot.process_message(ME, ask, NOW)

    def unreadable(now):
        raise RuntimeError("minute data unreadable")
    monkeypatch.setattr(bot, "_vitals_sessions", unreadable)
    assert expect in bot.process_message(ME, ask, NOW)
