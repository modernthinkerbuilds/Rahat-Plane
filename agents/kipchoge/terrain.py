"""agents.kipchoge.terrain — the route's shape (PRD v0.3 §6.2, §9.2).

The route's altitude is smoothed over distance before anything is
summed (GPS altitude jitters by metres point to point; on the Ultra 2
it is fused with the barometer, which S0 verifies on the first real
route). From the smoothed profile: gain, loss, range, the steepest
200 m, and whether the terrain is spiky or smooth. `flights_climbed`
is never a terrain measure (PRD: it misses rolling terrain).

One grade-adjusted-pace model, named in every output:
`kipchoge-gap-1`. Uphill it is Minetti's energy cost of running on a
grade (Minetti et al. 2002, J Appl Physiol: C(i) = 155.4i^5 - 30.4i^4
- 43.3i^3 + 46.3i^2 + 19.5i + 3.6 J/kg/m) relative to the flat;
downhill it credits half of Minetti's saving and never more than 8%,
because heart-rate-calibrated curves (Strava's 2017 work is the
reference) find descents help far less than the energy cost implies.
Confidence: medium uphill, low-medium downhill (PRD §6.2).

Route fingerprints identify a route across runs: a coarse polyline of
the trace (points every ~400 m, rounded to ~200 m). Coordinates never
leave this module as evidence; only derived values do.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Sequence

from agents.kipchoge.ingest import RoutePoint
from bridges.healthkit.workouts import haversine_m

GAP_MODEL = "kipchoge-gap-1"
SMOOTH_M = 25.0               # altitude averaged over ±25 m of horizontal distance
HYSTERESIS_M = 3.0            # a climb or a drop counts once it exceeds this
STEEP_WINDOW_M = 200.0
SPIKY_PER_KM = 6              # direction changes per km above this read as spiky
MAX_GRADE = 0.30
FINGERPRINT_STEP_M = 400.0
FINGERPRINT_ROUND = 0.002     # ~200 m


@dataclass
class Elevation:
    gain_m: float
    loss_m: float
    min_m: float
    max_m: float
    steepest_200m_pct: float
    spiky: bool
    points: int
    smoothed: bool = True

    @property
    def range_m(self) -> float:
        return self.max_m - self.min_m


def cumulative_distance_m(route: Sequence[RoutePoint]) -> list[float]:
    out = [0.0]
    for a, b in zip(route, route[1:]):
        out.append(out[-1] + haversine_m((a.lat, a.lon), (b.lat, b.lon)))
    return out


def smooth_altitude(route: Sequence[RoutePoint], window_m: float = SMOOTH_M) -> list[float | None]:
    """Each point's altitude averaged with the points within ±window_m of
    horizontal distance. None where the route has no altitude."""
    dist = cumulative_distance_m(route)
    alts = [p.alt for p in route]
    out: list[float | None] = []
    j0 = 0
    for i, d in enumerate(dist):
        while j0 < i and dist[j0] < d - window_m:
            j0 += 1
        vals = []
        j = j0
        while j < len(dist) and dist[j] <= d + window_m:
            if alts[j] is not None:
                vals.append(alts[j])
            j += 1
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def elevation(route: Sequence[RoutePoint]) -> Elevation | None:
    """Gain and loss with hysteresis over the smoothed profile."""
    if len(route) < 10:
        return None
    alts = smooth_altitude(route)
    dist = cumulative_distance_m(route)
    pts = [(d, a) for d, a in zip(dist, alts) if a is not None]
    if len(pts) < 10:
        return None
    gain = loss = 0.0
    anchor = pts[0][1]
    direction = 0
    changes = 0
    for _, a in pts[1:]:
        delta = a - anchor
        if delta >= HYSTERESIS_M:
            gain += delta
            anchor = a
            if direction == -1:
                changes += 1
            direction = 1
        elif delta <= -HYSTERESIS_M:
            loss += -delta
            anchor = a
            if direction == 1:
                changes += 1
            direction = -1
    steepest = 0.0
    j = 0
    for i, (d, a) in enumerate(pts):
        while pts[j][0] < d - STEEP_WINDOW_M:
            j += 1
        if i > j and d - pts[j][0] >= STEEP_WINDOW_M * 0.8:
            g = abs(a - pts[j][1]) / (d - pts[j][0]) * 100.0
            steepest = max(steepest, g)
    km = max(dist[-1] / 1000.0, 0.1)
    return Elevation(round(gain, 1), round(loss, 1), round(min(a for _, a in pts), 1), round(max(a for _, a in pts), 1),
                     round(steepest, 1), changes / km > SPIKY_PER_KM, len(pts))


# ── grade-adjusted pace ───────────────────────────────────────────────
def minetti_cost(grade: float) -> float:
    i = max(-MAX_GRADE, min(MAX_GRADE, grade))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def gap_factor(grade: float) -> float:
    """How much harder a metre at this grade is than a flat metre
    (kipchoge-gap-1): Minetti uphill; half the Minetti saving downhill,
    never below 0.92."""
    ratio = minetti_cost(grade) / 3.6
    if grade >= 0:
        return ratio
    return max(0.92, 1.0 - (1.0 - ratio) / 2.0)


def flat_equivalent_m(route: Sequence[RoutePoint], t1: float, t2: float) -> tuple[float, float] | None:
    """(flat-equivalent metres, raw metres) covered between t1 and t2 on
    the route, grade by grade over the smoothed profile."""
    idx = [i for i, p in enumerate(route) if t1 <= p.t <= t2]
    if len(idx) < 2:
        return None
    alts = smooth_altitude(route)
    flat = raw = 0.0
    for i, j in zip(idx, idx[1:]):
        a, b = route[i], route[j]
        d = haversine_m((a.lat, a.lon), (b.lat, b.lon))
        if d <= 0:
            continue
        raw += d
        ai, aj = alts[i], alts[j]
        grade = ((aj - ai) / d) if (ai is not None and aj is not None and d >= 2.0) else 0.0
        flat += d * gap_factor(grade)
    return flat, raw


def gap_pace_s_per_mi(route: Sequence[RoutePoint], t1: float, t2: float, moving_s: float) -> dict | None:
    """Grade-adjusted pace over [t1, t2]: the flat-equivalent distance over
    the moving time. Returns the raw pace beside it and the model's name."""
    fe = flat_equivalent_m(route, t1, t2)
    if fe is None or moving_s <= 0:
        return None
    flat, raw = fe
    if raw < 100:
        return None
    return {"gap_s_per_mi": moving_s / (flat / 1609.344), "raw_s_per_mi": moving_s / (raw / 1609.344),
            "flat_equivalent_km": round(flat / 1000.0, 3), "raw_km": round(raw / 1000.0, 3), "model": GAP_MODEL}


# ── route identity ────────────────────────────────────────────────────
def fingerprint(route: Sequence[RoutePoint]) -> str:
    """A coarse polyline: a point every ~400 m, rounded to ~200 m, hashed.
    The hash is the route's name in evidence; the points stay here."""
    cells = fingerprint_cells(route)
    return hashlib.sha1("|".join(cells).encode()).hexdigest()[:16] if cells else ""


def fingerprint_cells(route: Sequence[RoutePoint]) -> list[str]:
    if len(route) < 2:
        return []
    dist = cumulative_distance_m(route)
    cells: list[str] = []
    next_d = 0.0
    for p, d in zip(route, dist):
        if d >= next_d:
            cells.append(f"{round(p.lat / FINGERPRINT_ROUND) * FINGERPRINT_ROUND:.3f},{round(p.lon / FINGERPRINT_ROUND) * FINGERPRINT_ROUND:.3f}")
            next_d += FINGERPRINT_STEP_M
    return cells


def same_route(cells_a: Sequence[str], cells_b: Sequence[str], threshold: float = 0.6) -> bool:
    """Two traces are the same route when they share most of their cells
    (either direction, either length)."""
    a, b = set(cells_a), set(cells_b)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def start_point_rounded(route: Sequence[RoutePoint], step: float = 0.02) -> tuple[float, float] | None:
    """The start, rounded to about 2 km, for a weather call (PRD §3.10)."""
    if not route:
        return None
    p = route[0]
    return round(round(p.lat / step) * step, 2), round(round(p.lon / step) * step, 2)
