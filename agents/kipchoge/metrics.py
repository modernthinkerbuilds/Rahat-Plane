"""agents.kipchoge.metrics — the metrics catalog (PRD v0.3 §6.2), computed
from one run's samples and segments, each with its formula version.

  * time in zone, from his zones (never Apple's);
  * halves of two kinds, both reported: run halves (the whole moving
    time split in two: the thread's method, kept for continuity and the
    numeric goldens) and main-block halves (pause-aware; decoupling and
    drift read these);
  * efficiency factor (speed ÷ heart rate), decoupling (the change in
    EF between the main-block halves; needs a block of 20 minutes or
    more with no single pause over 60 s, else "not measurable"), cardiac
    drift (the second half's heart rate at the first half's pace, with
    the athlete's HR–pace slope);
  * heart-rate recovery (the drop 60 s and 90 s after the end);
  * cadence, the running-form averages inside the block, kcal per km;
  * session load: Banister's TRIMP when the athlete's resting and
    maximum heart rates are known, else minutes × average heart rate.

Everything returns plain numbers; `analyze.run_records` turns them into
evidence records with ids and ranges.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from agents.kipchoge import segments as seg
from agents.kipchoge.ingest import RunData, Sample

FORMULA_VERSION = "m1"
DECOUPLING_MIN_BLOCK_S = 20 * 60
DECOUPLING_MAX_PAUSE_S = 60


@dataclass
class Half:
    start: float
    end: float
    moving_s: float
    hr_avg: float | None
    km: float | None
    pace_s_per_mi: float | None
    ef: float | None            # metres per minute per bpm


def _half_bounds(run: RunData, ps: Sequence[seg.Pause], t1: float, t2: float) -> tuple[float, float, float]:
    """Split [t1, t2] at the point where half of its MOVING time has
    passed. Returns (mid, moving_first, moving_second)."""
    total_moving = (t2 - t1) - seg.paused_seconds(ps, t1, t2)
    target = total_moving / 2.0
    t = t1
    acc = 0.0
    step = 1.0
    while t < t2 and acc < target:
        nxt = min(t + step, t2)
        acc += (nxt - t) - seg.paused_seconds(ps, t, nxt)
        t = nxt
    return t, acc, total_moving - acc


def half(run: RunData, ps: Sequence[seg.Pause], t1: float, t2: float) -> Half:
    moving = (t2 - t1) - seg.paused_seconds(ps, t1, t2)
    hr = seg.time_weighted_mean(_moving_only(run.hr, ps), t1, t2)
    km = seg.distance_between(run, t1, t2)
    pace = seg.pace_s_per_mi(km, moving)
    ef = ((km * 1000.0 / (moving / 60.0)) / hr) if (km and moving > 0 and hr) else None
    return Half(t1, t2, moving, round(hr, 2) if hr is not None else None, round(km, 3) if km is not None else None,
                round(pace, 1) if pace is not None else None, round(ef, 4) if ef is not None else None)


def _moving_only(samples: Sequence[Sample], ps: Sequence[seg.Pause]) -> list[Sample]:
    if not ps:
        return list(samples)
    out = []
    for s in samples:
        if not any(p.start < s.t < p.end for p in ps):
            out.append(s)
    return out


def halves(run: RunData, ps: Sequence[seg.Pause], t1: float = 0.0, t2: float | None = None) -> tuple[Half, Half]:
    t2 = run.elapsed_s if t2 is None else t2
    mid, _, _ = _half_bounds(run, ps, t1, t2)
    return half(run, ps, t1, mid), half(run, ps, mid, t2)


def time_in_zones(run: RunData, zones: dict, ps: Sequence[seg.Pause]) -> dict:
    """Seconds below the band, in the band, in the sweet spot, above the
    band and above the cap, weighting each sample by the time it stands
    for and skipping pauses."""
    lo, hi = zones.get("zone2_lo"), zones.get("zone2_hi")
    sweet_lo, sweet_hi = zones.get("sweet_lo"), zones.get("sweet_hi")
    cap = zones.get("cap")
    out = {"below": 0.0, "in_band": 0.0, "sweet": 0.0, "above": 0.0, "over_cap": 0.0, "total": 0.0}
    pts = _moving_only(run.hr, ps)
    if not pts or lo is None or hi is None:
        return out
    for k, s in enumerate(pts):
        left = pts[k - 1].t if k > 0 else s.t
        right = pts[k + 1].t if k < len(pts) - 1 else s.t
        w = min(60.0, max(0.0, (right - left) / 2.0))
        out["total"] += w
        if s.v < lo:
            out["below"] += w
        elif s.v <= hi:
            out["in_band"] += w
            if sweet_lo is not None and sweet_hi is not None and sweet_lo <= s.v <= sweet_hi:
                out["sweet"] += w
        else:
            out["above"] += w
        if cap is not None and s.v > cap:
            out["over_cap"] += w
    return {k: round(v) for k, v in out.items()}


def decoupling(first: Half, second: Half, block_s: float, longest_pause_s: float) -> dict:
    """(EF first − EF second) ÷ EF first, in percent. Positive means the
    second half cost more heart rate per metre."""
    if block_s < DECOUPLING_MIN_BLOCK_S:
        return {"pct": None, "measurable": False, "why": "the main block is under 20 minutes"}
    if longest_pause_s > DECOUPLING_MAX_PAUSE_S:
        return {"pct": None, "measurable": False, "why": f"a pause of {longest_pause_s:.0f} s inside the main block"}
    if not first.ef or not second.ef:
        return {"pct": None, "measurable": False, "why": "no pace or no heart rate in one half"}
    return {"pct": round((first.ef - second.ef) / first.ef * 100.0, 2), "measurable": True, "why": ""}


def drift(first: Half, second: Half, slope_s_per_mi_per_bpm: float | None) -> dict:
    """The heart-rate rise between the halves, raw and at matched pace
    (the second half's heart rate corrected for its pace difference with
    the athlete's slope)."""
    if first.hr_avg is None or second.hr_avg is None:
        return {"raw_bpm": None, "pace_matched_bpm": None}
    raw = second.hr_avg - first.hr_avg
    matched = None
    if slope_s_per_mi_per_bpm and first.pace_s_per_mi and second.pace_s_per_mi:
        # faster second half: part of the rise bought pace; slower: the rise is understated
        matched = raw - (first.pace_s_per_mi - second.pace_s_per_mi) / slope_s_per_mi_per_bpm
    return {"raw_bpm": round(raw, 1), "pace_matched_bpm": round(matched, 1) if matched is not None else None}


def hr_recovery(run: RunData) -> dict:
    """The drop from the end of the run at 60 s and 90 s, from the
    workout's recovery series (or the day series after the end, for an
    older run); None without either."""
    if not run.hr_recovery or not run.hr:
        return {"drop_60s": None, "drop_90s": None, "source": ""}
    end_hr = run.hr[-1].v
    out = {"drop_60s": None, "drop_90s": None, "source": run.recovery_source or "the workout's recovery series"}
    for key, secs in (("drop_60s", 60.0), ("drop_90s", 90.0)):
        v = seg.value_at(run.hr_recovery, secs)
        if v is not None and run.hr_recovery[-1].t >= secs - 15:
            out[key] = round(end_hr - v)
    return out


def cadence(run: RunData, moving_s: float) -> float | None:
    """Steps per moving minute from the day series' per-minute steps."""
    if not run.steps or moving_s <= 0:
        return None
    return round(sum(s.v for s in run.steps) / (moving_s / 60.0))


def form_in(run: RunData, t1: float, t2: float) -> dict:
    out = {}
    for metric, samples in run.form.items():
        v = seg.time_weighted_mean(samples, t1, t2)
        if v is not None:
            out[metric] = round(v, 2)
    return out


def trimp(run: RunData, zones: dict, ps: Sequence[seg.Pause]) -> dict:
    """Banister's TRIMP: Σ minutes × HRr × 0.64·e^(1.92·HRr), HRr the heart-
    rate reserve fraction; needs resting and max. Else minutes × avg HR."""
    pts = _moving_only(run.hr, ps)
    if not pts:
        return {"value": None, "method": ""}
    rest, hmax = zones.get("resting"), zones.get("max")
    if rest and hmax and hmax > rest:
        total = 0.0
        for k, s in enumerate(pts):
            left = pts[k - 1].t if k > 0 else s.t
            right = pts[k + 1].t if k < len(pts) - 1 else s.t
            minutes = min(1.0, max(0.0, (right - left) / 2.0) / 60.0)
            hrr = max(0.0, min(1.0, (s.v - rest) / (hmax - rest)))
            total += minutes * hrr * 0.64 * math.exp(1.92 * hrr)
        return {"value": round(total, 1), "method": "Banister TRIMP (resting and max from the athlete file)"}
    moving_min = seg.moving_time(run, ps) / 60.0
    avg = seg.mean(pts) or 0.0
    return {"value": round(moving_min * avg / 100.0, 1), "method": "minutes × average heart rate ÷ 100 (no resting/max on file)"}


def kcal_per_km(run: RunData) -> float | None:
    if run.kcal and run.distance_km:
        return round(run.kcal / run.distance_km, 1)
    return None
