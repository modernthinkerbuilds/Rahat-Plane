"""Regression (2026-11-01): Kipchoge's block, halves and normalized pace
came out 1.6 times too slow, from a distance series a third short.

LIVE: once the phone's export carried each workout's own series (Nov 1),
Health Auto Export's per-minute distance series added up to 63% of the
workout's total on every workout that had one. The engine trusted the
series over the route, so a run's main block came out a sixth of its
length and its halves 1.6 times slower than the run, each record naming
"the workout's own distance series" as its source. The route's speed
came within 1% of the total.

Fix (agents/kipchoge/ingest.py `check_distance`, engine 1.0.4): a series
more than 5% off the workout's total yields to the route's speed when
the route comes within 5%, and is scaled to the total otherwise; the
distance source says which, and how short the series was. The engine
version moves, so the window is re-analyzed and the trend and the slope
drop the slow paces.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from agents.kipchoge import analyze as ca
from agents.kipchoge import ingest
from tests.kipchoge.fixtures import ZONES, load_fixture, sandbox_db, synthetic_run

START = datetime(2026, 10, 18, 7, 30)
NOW = datetime(2026, 10, 18, 12, 0)


def _short(fx: dict, factor: float = 0.63) -> dict:
    for row in fx["tables"]["workout_series"]:
        if row["kind"] == "distance":
            row["avg"] = round(row["avg"] * factor, 4)
    return fx


def _analyze(tmp_path, fx: dict):
    con = sandbox_db(tmp_path / "health.db")
    load_fixture(con, fx)
    run = ingest.load_run(con, fx["workout_id"])
    con.close()
    a = ca.analyze(run, zones=ZONES, now=NOW)
    recs = {r["id"].split(":", 2)[-1]: r for r in a["records"]}
    return run, a, recs


def _near(a: float, b: float, pct: float = 3.0) -> bool:
    return abs(a - b) / b * 100.0 <= pct


@pytest.mark.parametrize("route", [True, False])
def test_a_series_a_third_short_never_sets_the_pace(tmp_path, route):
    fx = _short(synthetic_run("SHORT", START, 70, km=10.0, hr_first=142.0, hr_second=144.0, route=route))
    run, a, recs = _analyze(tmp_path, fx)
    want = "the route's speed (" if route else "the workout's distance series, scaled to the workout's total ("
    assert run.dist_source.startswith(want) and "came to 6.30 of 10.00 km" in run.dist_source, run.dist_source
    whole = recs["pace_s_per_mi"]["value"]
    for half in ("first", "second"):
        p = recs[f"run_halves.{half}.pace_s_per_mi"]["value"]
        assert _near(p, whole), (half, p, whole)
    assert _near(recs["main_block.pace_s_per_mi"]["value"], whole, 4.0)
    assert a["sources"]["distance"] == run.dist_source


def test_a_series_that_adds_up_is_kept(tmp_path):
    run, a, recs = _analyze(tmp_path, synthetic_run("FULL", START, 70, km=10.0, hr_first=142.0, hr_second=144.0))
    assert run.dist_source == "the workout's own distance series" and run.has_distance()
    assert _near(recs["run_halves.first.pace_s_per_mi"]["value"], recs["pace_s_per_mi"]["value"])
