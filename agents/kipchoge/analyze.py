"""agents.kipchoge.analyze — one run, from samples to evidence records.

The engine's one entry point for a run: segments → metrics → terrain →
conditions → grade → normalization, every value landing in a `RecordSet`
under `run:<uuid>:<path>` (or `prov:<start utc>:<path>` for a provisional
run). The analysis dict is what `run_analysis` stores; the records are
what the coach may say (PRD v0.3 §12.2). Pure: the conditions, the
slope and the route match are passed in; nothing here reads a file, a
clock or the wire.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from agents.kipchoge import grade as cgrade
from agents.kipchoge import metrics as cm
from agents.kipchoge import model as cmodel
from agents.kipchoge import segments as seg
from agents.kipchoge import terrain as ct
from agents.kipchoge.conditions import Conditions
from agents.kipchoge.ingest import RunData
from agents.kipchoge.records import ENGINE_VERSION, RecordSet, rid


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def analyze(run: RunData, *, zones: dict, slope: cmodel.Slope | None = None, conditions: Conditions | None = None,
            route_match: dict | None = None, labeled: str | None = None, now: datetime | None = None) -> dict:
    """`route_match`: {"name", "benchmark": bool, "flat": bool|None} from
    the route library (None when the route is unknown or absent)."""
    slope = slope or cmodel.Slope(cmodel.SLOPE_PRIOR, cmodel.SLOPE_PRIOR_SD, 0, "the prior")
    key = run.workout_id if not run.provisional else run.workout_id        # prov:<start utc> already
    prefix = "run" if not run.provisional else None
    R = RecordSet()

    def put(path: str, value, unit: str = "", *, lo=None, hi=None, source: str = "", at: str = ""):
        id_ = rid(prefix, key, path) if prefix else rid(key, path)
        return R.put(id_, value, unit, lo=lo, hi=hi, source=source, at=at)

    notes: list[str] = []
    ps = seg.pauses(run)
    gaps = seg.hr_gaps(run)
    moving = seg.moving_time(run, ps)
    block = seg.main_block(run, zones, ps)
    block_moving = block.seconds - seg.paused_seconds(ps, block.start, block.end)
    ramp = seg.ramp(run, zones, ps)
    spikes = seg.spikes(run, zones, ps, gaps)
    longest_stop = seg.longest_pause(ps, 0.0, run.elapsed_s)
    block_longest = seg.longest_pause(ps, block.start, block.end)
    has_speed = bool(run.route or run.has_distance())
    hr_moving = seg.time_weighted_mean(cm._moving_only(run.hr, ps), 0.0, run.elapsed_s)

    # ── basics ──
    put("elapsed_min", _r(run.elapsed_s / 60.0), "min", source="the workout file: end minus start")
    put("duration_min", _r(run.duration_s / 60.0), "min", source="the workout file: the Watch's active duration, its own pauses out")
    put("watch_paused_s", _r(run.watch_paused_s, 0), "s", source="elapsed minus the active duration: what the Watch itself paused")
    put("moving_min", _r(moving / 60.0), "min",
        source=(f"elapsed minus {len(ps)} pauses (standing still under {seg.STILL_MPS} m/s, the Watch's own pauses, split stops)"
                if has_speed else "the Watch's active duration (no speed series: a stop it did not pause for is inside it)"))
    put("pauses", len(ps), "count", source="standing still, the Watch's own pauses and split stops" if has_speed
        else "split stops only: no speed series to see a stop")
    put("longest_stop_s", _r(longest_stop, 0), "s", source="the longest pause")
    put("hr_gaps", len(gaps), "count",
        source=f"gaps over {seg.HR_GAP_MIN_S:.0f} s (or 3× the cadence) in the heart-rate series: a lost pulse or a stop; reported, never subtracted")
    put("hr_gaps.longest_s", _r(max((g.seconds for g in gaps), default=0.0), 0), "s", source="the longest heart-rate gap")
    put("hr_gaps.total_s", _r(sum(g.seconds for g in gaps), 0), "s", source="all heart-rate gaps together")
    if run.distance_km:
        put("distance_km", _r(run.distance_km, 2), "km", source="the workout file")
        put("distance_mi", _r(run.distance_km / 1.609344, 3), "mi", source="the workout file")
        pace_all = seg.pace_s_per_mi(run.distance_km, moving)
        put("pace_s_per_mi", _r(pace_all), "s/mi", source="distance over moving time" if has_speed
            else "distance over the Watch's active duration")
    if run.avg_hr:
        put("hr_avg", _r(run.avg_hr), "bpm", source="the workout file")
    put("hr_avg_moving", _r(hr_moving), "bpm", source="time-weighted over the whole run's moving samples (the session type reads this)")
    if run.max_hr:
        put("hr_max", _r(run.max_hr), "bpm", source="the workout file")
    if run.kcal:
        put("kcal", _r(run.kcal, 0), "kcal", source="the Watch (noisy; never evidence of body mass)")
        kk = cm.kcal_per_km(run)
        if kk:
            put("kcal_per_km", kk, "kcal/km", source="the Watch")
    put("hr_samples", len(run.hr), "count", source=run.hr_source)
    put("start_local", run.start_local.strftime("%Y-%m-%d %H:%M"), "local", source="the workout file, in the sample's own offset")

    # ── the ramp and the block ──
    put("ramp_min", ramp.get("minutes"), "min", source="minutes until the 60-s rolling heart rate held the sweet spot's floor for 2 min")
    put("main_block.start_min", _r(block.start / 60.0), "min", source="the end of the ramp")
    put("main_block.end_min", _r(block.end / 60.0), "min", source=block.reason)
    put("main_block.moving_min", _r(block_moving / 60.0), "min", source="the block minus its pauses")
    block_hr = seg.time_weighted_mean(cm._moving_only(run.hr, ps), block.start, block.end)
    put("main_block.hr_avg", _r(block_hr), "bpm", source="time-weighted over the block's moving samples")
    block_km = seg.distance_between(run, block.start, block.end)
    block_pace = seg.pace_s_per_mi(block_km, block_moving)
    if block_km is not None:
        put("main_block.km", _r(block_km, 2), "km", source=run.dist_source)
    put("main_block.pace_s_per_mi", _r(block_pace), "s/mi", source=f"block distance over moving time ({run.dist_source or 'no distance'})")

    # ── halves ──
    h1, h2 = cm.halves(run, ps)
    b1, b2 = cm.halves(run, ps, block.start, block.end)
    for label, hh in (("run_halves.first", h1), ("run_halves.second", h2), ("block_halves.first", b1), ("block_halves.second", b2)):
        put(f"{label}.hr_avg", hh.hr_avg, "bpm", source="the whole run's moving time split in two (the thread's method)"
            if label.startswith("run") else "the main block's moving time split in two")
        put(f"{label}.pace_s_per_mi", hh.pace_s_per_mi, "s/mi", source=run.dist_source or "no distance")
        put(f"{label}.moving_min", _r(hh.moving_s / 60.0), "min")
    dec = cm.decoupling(b1, b2, block.seconds, block_longest)
    put("decoupling_pct", dec["pct"], "%", source=("EF first main-block half vs second; a coaching heuristic, one signal"
                                                   if dec["measurable"] else f"not measurable: {dec['why']}"))
    dr = cm.drift(b1, b2, slope.s_per_mi_per_bpm)
    put("drift_bpm", dr["raw_bpm"], "bpm", source="second main-block half minus first")
    put("drift_pace_matched_bpm", dr["pace_matched_bpm"], "bpm", source=f"at matched pace, with the HR–pace slope {slope.s_per_mi_per_bpm} s/mi per bpm")

    # ── zones ──
    tz = cm.time_in_zones(run, zones, ps)
    for k in ("below", "in_band", "sweet", "above", "over_cap"):
        put(f"time_in_zone.{k}_min", _r(tz.get(k, 0) / 60.0), "min", source="his zones from the athlete file, moving samples")
    if tz.get("total"):
        put("time_in_zone.in_band_pct", _r(tz["in_band"] / tz["total"] * 100.0, 0), "%")

    # ── terrain ──
    elev = ct.elevation(run.route) if run.has_route() else None
    gap = None
    gain_per_km = None
    if elev is not None:
        put("elevation.gain_m", elev.gain_m, "m", source="the route's smoothed altitude (gain over 3 m hysteresis)")
        put("elevation.loss_m", elev.loss_m, "m")
        put("elevation.gain_ft", _r(elev.gain_m * 3.28084, 0), "ft")
        put("elevation.steepest_200m_pct", elev.steepest_200m_pct, "%")
        put("elevation.spiky", elev.spiky, "flag", source="direction changes per km")
        if run.distance_km:
            gain_per_km = elev.gain_m / run.distance_km
        gap = ct.gap_pace_s_per_mi(run.route, block.start, block.end, block_moving)
        if gap:
            put("main_block.gap_s_per_mi", _r(gap["gap_s_per_mi"]), "s/mi", source=f"grade-adjusted pace, model {gap['model']}")
            put("main_block.gap_model", gap["model"], "name")
    elif run.route:
        notes.append("the route has too few points for a profile")
    else:
        notes.append("no route on file: no elevation, no grade-adjusted pace")

    # ── recovery, cadence, form, load ──
    rec = cm.hr_recovery(run)
    put("hrr.drop_60s", rec["drop_60s"], "bpm", source=rec["source"])
    put("hrr.drop_90s", rec["drop_90s"], "bpm", source=rec["source"])
    cad = cm.cadence(run, moving)
    put("cadence_spm", cad, "steps/min", source="the day series' steps over moving minutes")
    for metric, v in cm.form_in(run, block.start, block.end).items():
        put(f"form.{metric}", v, "", source="the Watch's running form, main block")
    load = cm.trimp(run, zones, ps)
    put("load", load["value"], "TRIMP" if "Banister" in load["method"] else "min×bpm/100", source=load["method"])

    # ── strides, artifacts ──
    pace_mps = (block_km * 1000.0 / block_moving) if block_km and block_moving > 0 else None
    st = seg.strides(run, pace_mps, after=block.end) if pace_mps else []
    put("strides", len(st), "count", source="10–30 s surges over 20% faster than the block, after it")
    put("artifacts", len(spikes), "count", source="early spikes and post-stop readings, excluded from the metrics")
    for i, s in enumerate(spikes):
        put(f"artifact.{i}.kind", s["kind"], "name", source=s["why"])
        put(f"artifact.{i}.bpm", s["bpm"], "bpm", source=f"at minute {s['t'] / 60:.1f}")

    # ── sleep and conditions ──
    night, two = run.sleep.get("night_before_h"), run.sleep.get("two_night_avg_h")
    put("sleep.night_before_h", night, "h",
        source=f"{run.sleep.get('method') or 'the sleep sessions'}: the longest session starting within 24 h before the run"
               + (f", ending {run.sleep['night_end']}" if run.sleep.get("night_end") else ""))
    put("sleep.two_night_avg_h", two, "h", source="with the night before that (starting 24–48 h before)")
    cond_json = None
    apparent = dew = temp = None
    sun = None
    if conditions is not None:
        cond_json = conditions.as_json()
        temp, dew, apparent = conditions.temperature_f.value, conditions.dew_point_f.value, conditions.apparent_f.value
        put("wx.temperature_f", _r(temp, 0), "°F", source=conditions.temperature_f.source)
        put("wx.dew_point_f", _r(dew, 0), "°F", source=conditions.dew_point_f.source)
        put("wx.apparent_f", _r(apparent, 0), "°F", source=conditions.apparent_f.source)
        put("wx.humidity_pct", _r(conditions.humidity_pct.value, 0), "%", source=conditions.humidity_pct.source)
        put("wx.heat_sum", conditions.heat_sum, "°F+°F", source="air + dew point (the PRD's heat guardrail)")
        if conditions.cloud_pct.value is not None:
            sun = conditions.cloud_pct.value < 30 and (route_match or {}).get("exposed", False)

    # ── type and grade ──
    stype, treasons = cgrade.session_type(name=run.name, avg_hr=hr_moving if hr_moving is not None else run.avg_hr,
                                          block_hr=block_hr, zones=zones, moving_s=moving,
                                          strides=len(st), gain_per_km=gain_per_km, labeled=labeled, indoor=run.indoor)
    excluded_share = (seg.paused_seconds(ps, block.start, block.end) / block.seconds) if block.seconds > 0 else 0.0
    cap = zones.get("cap")
    mg = cgrade.margins(first_half_hr=h1.hr_avg, block_hr=block_hr,
                        avg_hr=hr_moving if hr_moving is not None else run.avg_hr, zones=zones)
    # the start line (cap + a bpm of noise) on the first half's average decides D: a run that started over the cap is
    # a Zone 3 run from the gun, not a Zone 2 measurement; one that started in the band and drifted over is a noisy
    # Zone 2 run, and the drift is what we measure (S0 review, fix 3)
    started_over = mg["start"]["over"] if mg.get("start") else None
    # the protocol: the block's average at or under the cap, a bpm of sensor noise allowed; hovering at the cap is the
    # protocol, riding over it is not
    protocol_held = (not mg["protocol"]["over"]) if mg.get("protocol") else None
    if cap and block_hr is not None:
        over = [s for s in cm._moving_only(run.hr, ps) if block.start <= s.t <= block.end and s.v >= cap + 3]
        inside = [s for s in cm._moving_only(run.hr, ps) if block.start <= s.t <= block.end]
        share_over = (len(over) / len(inside)) if inside else 0.0
        put("main_block.over_cap_pct", _r(share_over * 100.0, 0), "%", source=f"share of the block 3+ bpm over the cap ({cap:.0f})")
    g, greasons, counts = cgrade.measurement_grade(
        stype=stype, indoor=run.indoor,
        benchmark_route=(bool((route_match or {}).get("benchmark")) if (route_match or run.has_route()) else None),
        flat=(route_match or {}).get("flat") if route_match else (None if elev is None else gain_per_km is not None and gain_per_km < cgrade.HILLY_GAIN_PER_KM),
        apparent_f=apparent, dew_f=dew, night_h=night, two_night_h=two, longest_stop_s=longest_stop,
        excluded_share=excluded_share, protocol_held=protocol_held, started_over_cap=started_over,
        start_margin_bpm=mg["start"]["margin_bpm"] if mg.get("start") else None)
    borderline = [b for b in mg["borderline"] if b in ("start", "protocol") or (b == "tempo" and stype in ("tempo", "z2_steady", "long"))]
    put("session_type", stype, "name", source="; ".join(treasons))
    put("grade", g, "grade", source="; ".join(greasons))
    put("grade.borderline", bool(borderline), "flag",
        source=("within a bpm of the " + " and ".join(f"{b} line ({mg[b]['value']:.1f} vs {mg[b]['line']:.0f})" for b in borderline)
                + ": the verdict is a coin at the edge; say so") if borderline else "no verdict within a bpm of its line")
    for name in ("start", "protocol", "tempo"):
        if mg.get(name):
            put(f"grade.margin.{name}_bpm", mg[name]["margin_bpm"], "bpm",
                source=f"{name} line {mg[name]['line']:.0f}: {'over' if mg[name]['over'] else 'under'} by {abs(mg[name]['margin_bpm']):.1f}")
    put("fitness_data_point", counts, "flag", source="A–C runs count toward the trend; D does not")
    if route_match and route_match.get("name"):
        put("route", route_match["name"], "name", source="the route library, matched on the trace")

    # ── normalization ──
    norm_json = None
    ref = zones.get("reference")                 # the athlete file's; no personal default lives in code
    pace_all = seg.pace_s_per_mi(run.distance_km, moving) if run.distance_km else None
    base_pace = (gap["gap_s_per_mi"] if gap else block_pace)
    base_hr = block_hr
    base_what = "grade-adjusted block pace" if gap else "block pace"
    if base_pace is None and pace_all and hr_moving:
        # no distance series for the block (an older run, a provisional one): the whole run stands in, said so
        base_pace, base_hr, base_what = pace_all, hr_moving, "whole-run pace (no distance series for the block) at the whole run's heart rate"
    if ref is None:
        notes.append("no reference heart rate in the athlete file: no normalization")
    elif counts and base_pace and base_hr:
        heat = cmodel.heat_adjustment(apparent, dew, sun)
        sleep_adj = cmodel.sleep_adjustment(night, two)
        full = cmodel.normalize(base_pace, base_hr, slope=slope, grade=g, ref_hr=ref, heat=heat, sleep=sleep_adj)
        hr_only = cmodel.normalize(base_pace, base_hr, slope=slope, grade=g, ref_hr=ref, exclude_adjustments=True)
        put("norm.basis", base_what, "name", source="what the normalization starts from")
        put("norm.pace_at_ref", full.pace_s_per_mi, "s/mi", lo=full.lo, hi=full.hi,
            source=f"{base_what} moved to {ref:.0f} bpm on the HR–pace line "
                   f"({slope.s_per_mi_per_bpm} s/mi per bpm, {slope.source}); adjustments: "
                   + "; ".join(f"{a.name} {a.bpm:+.1f} bpm ({a.lo:.1f}–{a.hi:.1f})" for a in full.adjustments))
        put("norm.pace_at_ref_hr_only", hr_only.pace_s_per_mi, "s/mi", lo=hr_only.lo, hi=hr_only.hi,
            source="the same, with no heat or sleep adjustment (the thread's HR-only normalization)")
        put("norm.ref_hr", ref, "bpm", source="the athlete file's reference heart rate")
        for a in full.adjustments:
            put(f"norm.adjust.{a.name}_bpm", a.bpm, "bpm", lo=a.lo, hi=a.hi, source=a.why)
        norm_json = {"full": asdict(full), "hr_only": asdict(hr_only),
                     "basis": {"hr": round(base_hr, 2), "pace_s_per_mi": round(base_pace, 1), "what": base_what}}
    elif not counts:
        notes.append(f"grade {g}: not a fitness data point, no normalization")
    else:
        notes.append("no block pace or heart rate: no normalization")

    return {
        "workout_id": run.workout_id, "engine_version": ENGINE_VERSION, "provisional": run.provisional,
        "name": run.name, "start_local": run.start_local.strftime("%Y-%m-%d %H:%M:%S"), "day": run.day,
        "session_type": stype, "grade": g, "fitness_data_point": counts, "borderline": borderline, "margins": mg,
        "reasons": {"type": treasons, "grade": greasons},
        "segments": {"pauses": [asdict(p) | {"seconds": round(p.seconds, 1)} for p in ps], "moving_s": round(moving, 1),
                     "elapsed_s": round(run.elapsed_s, 1), "watch_paused_s": round(run.watch_paused_s, 1),
                     "hr_gaps": [asdict(g) | {"seconds": round(g.seconds, 1)} for g in gaps], "ramp": ramp,
                     "main_block": asdict(block), "strides": [[round(a), round(b)] for a, b in st], "spikes": spikes},
        "halves": {"run": [asdict(h1), asdict(h2)], "block": [asdict(b1), asdict(b2)]},
        "decoupling": dec, "drift": dr, "time_in_zones": tz,
        "elevation": asdict(elev) if elev else None, "gap": gap,
        "sleep": run.sleep, "conditions": cond_json, "normalized": norm_json,
        "slope": asdict(slope), "merged_parts": run.merged_parts, "sources": {"hr": run.hr_source, "distance": run.dist_source},
        "notes": notes, "records": R.to_json(),
    }
