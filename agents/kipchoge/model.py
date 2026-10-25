"""agents.kipchoge.model — the HR–pace line, the fixed priors and the
rolling estimate (PRD v0.3 §6.4, §6.5). Version 1: the slope is fitted
from his runs around a prior; heat and sleep use fixed priors with wide
ranges; no coefficient learning until 20+ graded runs span conditions.

Every adjustment is made in bpm at a fixed pace, then converted to pace
with the slope (PRD §6.2). The reference condition is dew point 50°F,
apparent temperature 60°F, shade; the reference heart rate is the
athlete file's. Ranges are 80% intervals; they grow with
the size of each adjustment, the distance from the reference heart
rate, the grade of the run and the uncertainty of the coefficients.

Priors (PRD §6.4, Appendix A §7; low-confidence expert estimates, the
research supports the direction better than the size):
  heat   +0 bpm at or under the reference; up to about +8 at a warm
         midday (dew point 65°F / apparent 80°F), range ±4; full sun +3
         (range ±2);
  sleep  +4 bpm (range 3–5) after a night under 6 h; +1 more under 5 h;
         +1 more when the two-night average is under 5.5 h;
  slope  a wide physiology prior (6 ± 2.5 s/mi per bpm); the athlete
         file's own prior, when it has one, replaces it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

MODEL_VERSION = "hrpace-v1"
SLOPE_PRIOR = 6.0            # s/mi per bpm: a wide physiology prior; the athlete file's prior replaces it
SLOPE_PRIOR_SD = 2.5
SLOPE_PRIOR_N = 4            # the prior weighs like four runs
REF_DEW_F = 50.0
REF_APPARENT_F = 60.0
HEAT_MAX_BPM = 8.0
HEAT_RANGE = 4.0
SUN_BPM = 3.0
SUN_RANGE = 2.0
SLEEP_SHORT_H = 6.0
SLEEP_VERY_SHORT_H = 5.0
SLEEP_TWO_NIGHT_H = 5.5
GRADE_NOISE_S = {"A": 5.0, "B": 10.0, "C": 20.0, "D": 60.0}
LEARNING_MIN_RUNS = 20
Z80 = 1.2816                 # the 80% interval half-width in standard deviations


@dataclass
class Adjustment:
    name: str
    bpm: float
    lo: float
    hi: float
    why: str


@dataclass
class Slope:
    s_per_mi_per_bpm: float
    sd: float
    n_runs: int
    source: str


@dataclass
class Normalized:
    pace_s_per_mi: float
    lo: float
    hi: float
    ref_hr: float
    adjustments: list[Adjustment] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)

    @property
    def half_width(self) -> float:
        return (self.hi - self.lo) / 2.0


# ── the slope ─────────────────────────────────────────────────────────
def fit_slope(points: Sequence[tuple[float, float]], *, prior: float = SLOPE_PRIOR, prior_sd: float = SLOPE_PRIOR_SD,
              prior_n: int = SLOPE_PRIOR_N) -> Slope:
    """Points are (heart rate, pace s/mi) from steady blocks of A–C runs.
    Pace falls as heart rate rises, so the fitted slope is −d(pace)/d(HR),
    shrunk toward the prior as if the prior were prior_n runs. With one
    run or none, the prior stands."""
    pts = [(float(h), float(p)) for h, p in points if h and p]
    n = len(pts)
    if n < 2:
        return Slope(prior, prior_sd, n, "the prior (fewer than two runs)")
    mh = sum(h for h, _ in pts) / n
    mp = sum(p for _, p in pts) / n
    sxx = sum((h - mh) ** 2 for h, _ in pts)
    if sxx < 4.0:                                   # all runs at the same heart rate: the slope is unidentified
        return Slope(prior, prior_sd, n, "the prior (the runs sit at one heart rate)")
    sxy = sum((h - mh) * (p - mp) for h, p in pts)
    fitted = -sxy / sxx
    resid = [p - (mp - fitted * (h - mh)) for h, p in pts]
    se = math.sqrt(sum(r * r for r in resid) / max(1, n - 2) / sxx) if n > 2 else prior_sd
    w_fit = n
    w_prior = prior_n
    shrunk = (fitted * w_fit + prior * w_prior) / (w_fit + w_prior)
    sd = math.sqrt((se ** 2 * w_fit + prior_sd ** 2 * w_prior) / (w_fit + w_prior)) / math.sqrt(1 + n / prior_n)
    shrunk = max(2.0, min(14.0, shrunk))
    return Slope(round(shrunk, 2), round(max(0.5, sd), 2), n, f"fitted on {n} runs, shrunk toward the prior")


# ── the adjustments ───────────────────────────────────────────────────
def heat_adjustment(apparent_f: float | None, dew_f: float | None, sun: bool | None) -> Adjustment:
    """bpm the heat and humidity cost at a fixed pace, against the
    reference condition. Each of the two is taken as far as it is above
    its reference; the larger drives, the other adds a quarter."""
    parts = []
    if dew_f is not None:
        parts.append(max(0.0, dew_f - REF_DEW_F) / (65.0 - REF_DEW_F) * HEAT_MAX_BPM)
    if apparent_f is not None:
        parts.append(max(0.0, apparent_f - REF_APPARENT_F) / (80.0 - REF_APPARENT_F) * HEAT_MAX_BPM)
    if not parts:
        return Adjustment("heat", 0.0, 0.0, 0.0, "no conditions on file: no heat adjustment")
    parts.sort(reverse=True)
    bpm = min(HEAT_MAX_BPM * 1.25, parts[0] + 0.25 * (parts[1] if len(parts) > 1 else 0.0))
    rng = HEAT_RANGE * (bpm / HEAT_MAX_BPM) if bpm > 0 else 0.0
    why = (f"dew point {dew_f:.0f}°F" if dew_f is not None else "") + \
          (f", apparent {apparent_f:.0f}°F" if apparent_f is not None else "")
    adj = Adjustment("heat", round(bpm, 1), round(max(0.0, bpm - rng), 1), round(bpm + rng, 1),
                     f"prior: up to +{HEAT_MAX_BPM:.0f} bpm from dew point 50→65°F / apparent 60→80°F ({why.strip(', ')})")
    if sun:
        adj = Adjustment("heat+sun", round(adj.bpm + SUN_BPM, 1), round(adj.lo + SUN_BPM - SUN_RANGE, 1),
                         round(adj.hi + SUN_BPM + SUN_RANGE, 1), adj.why + f"; full sun +{SUN_BPM:.0f} (±{SUN_RANGE:.0f})")
    return adj


def sleep_adjustment(night_h: float | None, two_night_h: float | None) -> Adjustment:
    if night_h is None:
        return Adjustment("sleep", 0.0, 0.0, 0.0, "no sleep on file for the night before")
    bpm = 0.0
    why = []
    if night_h < SLEEP_SHORT_H:
        bpm += 4.0
        why.append(f"{night_h:.1f} h the night before (+4, range 3–5)")
    if night_h < SLEEP_VERY_SHORT_H:
        bpm += 1.0
        why.append("under 5 h (+1)")
    if two_night_h is not None and two_night_h < SLEEP_TWO_NIGHT_H:
        bpm += 1.0
        why.append(f"two-night average {two_night_h:.1f} h (+1)")
    if bpm == 0.0:
        return Adjustment("sleep", 0.0, 0.0, 0.0, f"{night_h:.1f} h the night before: no sleep adjustment")
    return Adjustment("sleep", bpm, max(0.0, bpm - 1.0), bpm + 1.0, "prior: " + "; ".join(why))


# ── normalization ─────────────────────────────────────────────────────
def normalize(pace_s_per_mi: float, hr_avg: float, *, slope: Slope, grade: str, ref_hr: float,
              heat: Adjustment | None = None, sleep: Adjustment | None = None,
              exclude_adjustments: bool = False) -> Normalized:
    """The pace he would have run at ref_hr in the reference condition,
    from one steady block: the block's (grade-adjusted) pace, moved along
    the HR–pace line from the block's heart rate, the heat and sleep
    taken off that heart rate first (they raised it at this pace).

    Range: the slope's uncertainty times the heart-rate distance, each
    adjustment's range times the slope, and the grade's noise, combined
    in quadrature, as an 80% interval."""
    adjustments = [a for a in (heat, sleep) if a is not None and not exclude_adjustments]
    adj_bpm = sum(a.bpm for a in adjustments)
    hr_eff = hr_avg - adj_bpm
    delta = ref_hr - hr_eff                         # bpm he was under the reference, in reference conditions
    pace = pace_s_per_mi - slope.s_per_mi_per_bpm * delta
    var = (Z80 * slope.sd * abs(delta)) ** 2
    for a in adjustments:
        var += ((a.hi - a.lo) / 2.0 * slope.s_per_mi_per_bpm) ** 2
    var += GRADE_NOISE_S.get(grade, 20.0) ** 2
    var += (0.5 * abs(delta)) ** 2                    # the line is less straight far from the reference
    half = math.sqrt(var)
    return Normalized(round(pace, 1), round(pace - half, 1), round(pace + half, 1), ref_hr, adjustments,
                      {"pace_s_per_mi": pace_s_per_mi, "hr_avg": hr_avg, "hr_effective": round(hr_eff, 1),
                       "delta_bpm": round(delta, 1), "slope": slope.s_per_mi_per_bpm, "grade": grade})


# ── the rolling estimate ──────────────────────────────────────────────
GRADE_WEIGHT = {"A": 1.0, "B": 0.7, "C": 0.4}


@dataclass
class Estimate:
    as_of: str
    pace_s_per_mi: float | None
    lo: float | None
    hi: float | None
    slope_per_week: float | None
    slope_lo: float | None
    slope_hi: float | None
    n_runs: int
    method: str


def trend(points: Sequence[tuple[str, float, float, str]], as_of: datetime, *, window_days: int = 120) -> Estimate:
    """Points are (local date 'YYYY-MM-DD', normalized pace, its 80% half
    width, grade) for A–C runs. A quality-weighted straight line over the
    last window_days, each run weighted by its grade and 1/half_width²;
    today's estimate is the line at as_of, with the line's own
    uncertainty added to the runs' spread. Under three runs: the
    weighted mean, no slope."""
    cutoff = (as_of - timedelta(days=window_days)).strftime("%Y-%m-%d")
    pts = [(datetime.strptime(d, "%Y-%m-%d"), p, max(h, 1.0), g) for d, p, h, g in points if d >= cutoff and g in GRADE_WEIGHT]
    n = len(pts)
    day = as_of.strftime("%Y-%m-%d")
    if n == 0:
        return Estimate(day, None, None, None, None, None, None, 0, "no graded runs in the window")
    xs = [(d - as_of).total_seconds() / 86400.0 / 7.0 for d, _, _, _ in pts]       # weeks from as_of (negative)
    ys = [p for _, p, _, _ in pts]
    ws = [GRADE_WEIGHT[g] / (h ** 2) for _, _, h, g in pts]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    if n < 3 or max(xs) - min(xs) < 2.0:
        half = math.sqrt(1.0 / sw) * Z80 + (max(h for _, _, h, _ in pts) if n < 2 else 0.0)
        return Estimate(day, round(my, 1), round(my - half, 1), round(my + half, 1), None, None, None, n,
                        "a weighted mean (fewer than three runs, or under two weeks of them)")
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx                       # the line at x = 0, i.e. as_of
    resid_var = sum(w * (y - (intercept + slope * x)) ** 2 for w, x, y in zip(ws, xs, ys)) / max(1.0, sw * (n - 2) / n)
    se_slope = math.sqrt(resid_var / sxx) if sxx > 0 else 0.0
    se_level = math.sqrt(resid_var / sw + (mx ** 2) * se_slope ** 2)
    spread = math.sqrt(sum(w * h * h for w, (_, _, h, _) in zip(ws, pts)) / sw)
    half = Z80 * math.sqrt(se_level ** 2 + spread ** 2 / n)
    return Estimate(day, round(intercept, 1), round(intercept - half, 1), round(intercept + half, 1),
                    round(slope, 2), round(slope - Z80 * se_slope, 2), round(slope + Z80 * se_slope, 2), n,
                    f"a quality-weighted line over {n} runs in {window_days} days")


def forecast(est: Estimate, target_date: datetime, as_of: datetime, *, target_pace_s: float | None = None) -> dict:
    """The line carried to target_date under the plan in force (v1: the
    current slope), with its range and, given a target, the chance of
    being at or under it (a normal approximation; 'early estimate' until
    forecasts have a track record, PRD §5.3)."""
    if est.pace_s_per_mi is None:
        return {"predicted": None, "lo": None, "hi": None, "p_target": None, "weeks": None, "early": True}
    weeks = (target_date - as_of).total_seconds() / 86400.0 / 7.0
    slope = est.slope_per_week or 0.0
    predicted = est.pace_s_per_mi + slope * weeks
    half_now = (est.hi - est.lo) / 2.0 if est.hi is not None and est.lo is not None else 30.0
    slope_half = ((est.slope_hi - est.slope_lo) / 2.0 if est.slope_hi is not None and est.slope_lo is not None else 5.0)
    half = math.sqrt(half_now ** 2 + (slope_half * abs(weeks)) ** 2 + (3.0 * abs(weeks)) ** 2)   # 3 s/mi a week of drift
    out = {"predicted": round(predicted, 1), "lo": round(predicted - half, 1), "hi": round(predicted + half, 1),
           "weeks": round(weeks, 1), "p_target": None, "early": True}
    if target_pace_s is not None and half > 0:
        z = (target_pace_s - predicted) / (half / Z80)
        out["p_target"] = round(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), 2)
    return out
