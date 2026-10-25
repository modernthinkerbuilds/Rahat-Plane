"""agents.kipchoge.engine — the engine on the store: a stored run in, its
analysis saved, the trend over every graded run.

`analyze_workout` is the one call the pipelines and the goldens share:
load the run (`ingest.load_run`), look the conditions up (the Watch's
own reading first, then Open-Meteo at the route's start rounded to
0.02°; nothing without a route), match the route against the library
(S1; today the library is empty, so every run is "no route to match"),
fit the HR–pace slope on the graded runs already analyzed, run
`analyze`, and save the result under (workout id, engine version) with
an input hash, so a re-sync with no new data writes nothing and a
re-sync with the distance series or the route re-analyzes.

    python -m agents.kipchoge.engine --since 2026-08-01            # the store, saving
    python -m agents.kipchoge.engine --db COPY --no-save --json    # a read-only copy, printing

Under RAHAT_TEST_MODE=1 the store is the sandbox, like every other
store (`state.db_path`), and no wire is touched without an `http` seam.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

from agents.kipchoge import analyze as ca
from agents.kipchoge import ingest
from agents.kipchoge import model as cmodel
from agents.kipchoge import state
from agents.kipchoge import terrain as ct
from agents.kipchoge.conditions import Conditions, conditions_for_run
from agents.kipchoge.records import ENGINE_VERSION


def slope_from_store(con: sqlite3.Connection, *, exclude: str | None = None,
                     prior: tuple[float, float] | None = None) -> cmodel.Slope:
    """The HR–pace slope fitted on the saved A and B analyses whose
    normalization basis is a steady block (a distance series or a route
    behind it; a whole-run basis carries the ramp and the cooldown and
    is left out), shrunk toward the prior; the prior alone until three
    such runs exist."""
    points: list[tuple[float, float]] = []
    for a in state.analyses(con, ENGINE_VERSION):
        if a.get("workout_id") == exclude or not a.get("fitness_data_point") or a.get("grade") not in ("A", "B"):
            continue
        basis = (a.get("normalized") or {}).get("basis") or {}
        if basis.get("hr") and basis.get("pace_s_per_mi") and "block" in str(basis.get("what", "")) \
                and not str(basis.get("what", "")).startswith("whole-run"):
            points.append((basis["hr"], basis["pace_s_per_mi"]))
    if prior is None:
        return cmodel.fit_slope(points)
    return cmodel.fit_slope(points, prior=prior[0], prior_sd=prior[1])


def match_route(run: ingest.RunData, library: dict | None) -> dict | None:
    """The route library's entry whose fingerprint cells the run shares
    (S1 fills the library; an empty one matches nothing)."""
    if not run.has_route() or not library:
        return None
    cells = ct.fingerprint_cells(run.route)
    for name, entry in (library.get("routes") or {}).items():
        if ct.same_route(cells, entry.get("cells") or []):
            return {"name": name, "benchmark": bool(entry.get("benchmark")), "flat": entry.get("flat"),
                    "exposed": bool(entry.get("exposed"))}
    return None


def input_hash_for(run: ingest.RunData, zones: dict, conditions: Conditions | None) -> str:
    return state.input_hash(
        run.workout_id, ENGINE_VERSION, zones,
        [(round(s.t, 1), s.v) for s in run.hr], [(round(s.t, 1), s.v) for s in run.dist],
        [(round(p.t, 1), p.lat, p.lon, p.alt) for p in run.route], run.sleep, run.meta, run.merged_parts,
        run.duration_s, run.elapsed_s, run.distance_km, conditions.as_json() if conditions else None)


def analyze_workout(con: sqlite3.Connection, workout_id: str, *, now: datetime, http=None, zones: dict | None = None,
                    prior: tuple[float, float] | None = None, library: dict | None = None, save: bool = True,
                    labeled: str | None = None) -> dict | None:
    """One stored run → its analysis dict (with `saved`: True when a new
    row was written; False when the same input was already analyzed).
    None when the run is not in the store. Zones and the slope prior
    come from the athlete file unless the caller passes them (tests)."""
    run = ingest.load_run(con, workout_id)
    if run is None:
        return None
    athlete_file = None if zones else state.athlete()
    zones = zones or state.zones(athlete_file)
    if prior is None and athlete_file is not None:
        prior = state.slope_prior(athlete_file)
    point = ct.start_point_rounded(run.route) if run.has_route() else None
    conditions = conditions_for_run(start_local=run.start_local, point=point, meta=run.meta, now=now, http=http) \
        if (point is not None or run.meta) else None
    slope = slope_from_store(con, exclude=workout_id, prior=prior)
    result = ca.analyze(run, zones=zones, slope=slope, conditions=conditions, route_match=match_route(run, library),
                        labeled=labeled, now=now)
    sha = input_hash_for(run, zones, conditions)
    result["input_sha256"] = sha
    result["saved"] = state.save_analysis(con, workout_id, ENGINE_VERSION, result, input_sha256=sha, now=now) if save else False
    return result


def analyze_since(con: sqlite3.Connection, since: str, *, now: datetime, until: str | None = None, http=None,
                  zones: dict | None = None, prior: tuple[float, float] | None = None, library: dict | None = None,
                  save: bool = True) -> list[dict]:
    """Every primary run from `since` (local date), oldest first, so the
    slope each run sees is fitted on the runs before it."""
    out = []
    for wid in ingest.runs_in(con, since, until):
        r = analyze_workout(con, wid, now=now, http=http, zones=zones, prior=prior, library=library, save=save)
        if r is not None:
            out.append(r)
    return out


def trend_now(con: sqlite3.Connection, *, now: datetime, window_days: int = 120) -> cmodel.Estimate:
    """The rolling estimate over the saved A–C analyses' normalized paces."""
    pts: list[tuple[str, float, float, str]] = []
    for a in state.analyses(con, ENGINE_VERSION):
        norm = (a.get("normalized") or {}).get("full") or {}
        if a.get("fitness_data_point") and norm.get("pace_s_per_mi"):
            half = ((norm.get("hi") or 0) - (norm.get("lo") or 0)) / 2.0 or 20.0
            pts.append((a["day"], norm["pace_s_per_mi"], half, a["grade"]))
    return cmodel.trend(pts, now, window_days=window_days)


def _line(a: dict) -> str:
    recs = {r["id"].split(":", 2)[-1]: r["value"] for r in a["records"]}
    pace = recs.get("pace_s_per_mi")
    norm = recs.get("norm.pace_at_ref")
    fmt = lambda s: f"{int(s // 60)}:{int(round(s % 60)):02d}" if s else "—"  # noqa: E731
    h = a["halves"]["run"]
    return (f"{a['day']} {a['workout_id'][:8]} {a['session_type']:10} {a['grade']}  halves {h[0]['hr_avg']}→{h[1]['hr_avg']}  "
            f"pace {fmt(pace)}  norm {fmt(norm)}  sleep {a['sleep'].get('night_before_h')}  "
            f"{'saved' if a.get('saved') else 'on file'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--db", default=None, help="a store (a READ-ONLY copy with --no-save); default: the ladder")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--json", action="store_true", help="print each analysis as JSON")
    ap.add_argument("--now", default=None, help="YYYY-MM-DD HH:MM:SS (tests inject it)")
    a = ap.parse_args(argv)
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M:%S") if a.now else datetime.now()
    if a.db and a.no_save:
        con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    else:
        con = state.connect(a.db)
        state.ensure_schema(con)
    results = analyze_since(con, a.since, now=now, until=a.until, save=not a.no_save)
    for r in results:
        print(json.dumps(r, ensure_ascii=False) if a.json else _line(r))
    est = trend_now(con, now=now) if not a.no_save else None
    if est and est.pace_s_per_mi:
        print(f"trend: {est.pace_s_per_mi:.0f} s/mi ({est.lo:.0f}–{est.hi:.0f}) over {est.n_runs} runs, {est.method}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
