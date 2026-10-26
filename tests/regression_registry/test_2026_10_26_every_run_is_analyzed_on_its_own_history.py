"""2026-10-26 — after an engine release the running coach's trend counted
6 runs where the store held 7: the trend tool filled the store only when
it held no analyses at all, so it counted whichever runs a message's
own tools had analyzed first. And a run's slope was fitted on every
saved analysis, later runs included, so its normalized pace depended on
what happened to be saved when it was analyzed. Now every primary run
in the window is analyzed oldest first before any one is read, and a
run's slope comes from the runs before it."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.kipchoge import engine as cengine  # noqa: E402
from agents.kipchoge import state  # noqa: E402
from agents.kipchoge import tools as ktools  # noqa: E402
from agents.kipchoge.records import ENGINE_VERSION  # noqa: E402
from tests.kipchoge.test_tenant import world  # noqa: E402, F401

NOW = datetime(2026, 10, 24, 9, 30)


def _trend_runs(tenant, db) -> int:
    ctx = tenant.build_ctx("t", NOW, "", db_path=db, cassette_dir=None, trace_id="t")
    try:
        out = ktools.build_registry(ctx).execute("trend", {}, ctx)
        assert out.ok, out.payload()
        return next(r["value"] for r in out.payload()["result"]["records"] if r["id"].endswith(":n_runs"))
    finally:
        ctx["con"].close()


def test_a_partial_store_after_a_release_still_trends_every_run(world):  # noqa: F811
    tenant, db = world["tenant"], world["db"]
    con = state.connect(db)
    try:
        # what a release leaves after one message: the latest run analyzed, the others not yet
        cengine.analyze_workout(con, "R-C", now=NOW, zones=tenant.build_ctx("t", NOW, "", db_path=db, cassette_dir=None, trace_id="t")["zones"], save=True)
        assert {a["workout_id"] for a in state.analyses(con, ENGINE_VERSION)} == {"R-C"}
    finally:
        con.close()
    n = _trend_runs(tenant, db)
    con = state.connect(db)
    try:
        analyzed = state.analyses(con, ENGINE_VERSION)
        assert {a["workout_id"] for a in analyzed} == {"R-A", "R-B", "R-C"}            # the window, filled
        expected = sum(1 for a in analyzed if a.get("fitness_data_point") and ((a.get("normalized") or {}).get("full") or {}).get("pace_s_per_mi"))
    finally:
        con.close()
    assert n == expected and n >= 1


def test_a_runs_slope_comes_from_the_runs_before_it(tmp_path):
    con = state.connect(str(tmp_path / "s.db"))
    try:
        for i, day in enumerate(("2026-09-01", "2026-09-08", "2026-09-15", "2026-10-20", "2026-10-21", "2026-10-22")):
            state.save_analysis(con, f"W{i}", ENGINE_VERSION, {"workout_id": f"W{i}", "day": day, "grade": "A", "fitness_data_point": True,
                                                              "normalized": {"basis": {"hr": 140 + i, "pace_s_per_mi": 760 - 7 * i, "what": "main block"}}},
                                input_sha256=f"h{i}")
        before = cengine.slope_from_store(con, before="2026-10-01")
        everything = cengine.slope_from_store(con)
        assert before.n_runs == 3 and everything.n_runs == 6
        assert cengine.slope_from_store(con, before="2026-09-01").n_runs == 0
    finally:
        con.close()
