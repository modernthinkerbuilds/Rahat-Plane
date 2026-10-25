"""agents.kipchoge.segments — the shape of one run (PRD v0.3 §6.2, §6.6).

Pure functions over the samples `ingest.load_run` returns. The
definitions are the PRD's, verbatim where it has them:

  * a pause: route speed under 0.5 m/s for at least 10 s (GPS jitter
    under trees is tolerated; the distance series stands in without a
    route), a gap in the route when the Watch's own duration says it
    paused (its pauses leave gaps, not zeros; the longest gaps, until
    they account for the paused time), or the stop between the parts of
    a split run. A gap in the heart-rate series is not a pause: the Watch
    loses the pulse to sweat and a loose strap while the legs keep going.
    `hr_gaps` reports those; the moving time of a run with no speed
    series is the Watch's active duration;
  * the ramp: minutes from the start until the 60-s rolling heart rate
    first reaches the athlete's sweet-spot floor and stays at or above
    the band's floor for two minutes;
  * the main block: from the end of the ramp to the first of the strides
    (two or more 10–30 s surges over 20% faster than the block's pace),
    a cooldown (heart rate 9 bpm or more under the band's floor for over
    60 s at the end) or the
    end; pauses inside it are excluded and counted;
  * strides, spikes (the first-minutes cadence lock, a post-stop reading
    bleeding into a split) and the moving time.

Every function takes the zones it needs as arguments; nothing here
reads a file or a clock.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Sequence

from agents.kipchoge.ingest import RunData, Sample

GAP_S = 10.0
STILL_MPS = 0.5
STILL_S = 10.0
WATCH_PAUSE_MIN_S = 5.0          # the summary must show at least this much paused time before route gaps count
HR_GAP_MIN_S = 15.0              # a heart-rate gap is reported from here (or 3× the series' cadence, whichever is longer)
HR_GAP_CADENCE_X = 3.0
POST_STOP_GAP_S = 20.0           # a heart-rate gap this long is a stop candidate for the post-stop artifact
WEIGHT_CAP_S = 30.0              # no single sample stands for more than this in a time-weighted mean
RAMP_HOLD_S = 120.0
ROLL_S = 60.0
STRIDE_MIN_S, STRIDE_MAX_S = 8.0, 35.0
STRIDE_FASTER = 1.20
COOLDOWN_BELOW_BAND = 9.0        # a cooldown runs this far under the band's floor
COOLDOWN_S = 60.0
EARLY_SPIKE_S = 180.0
POST_STOP_S = 70.0


@dataclass
class Pause:
    start: float
    end: float
    kind: str                 # still | gap (the Watch's own pause) | split (between the parts) | signal (a lost pulse)
    label: str | None = None  # call, traffic, water, pee: from his words (S1)

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class Block:
    start: float
    end: float
    reason: str = ""

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


# ── sample helpers ────────────────────────────────────────────────────
def _times(samples: Sequence[Sample]) -> list[float]:
    return [s.t for s in samples]


def value_at(samples: Sequence[Sample], t: float) -> float | None:
    """Linear interpolation; the nearest end outside the range."""
    if not samples:
        return None
    ts = _times(samples)
    i = bisect.bisect_left(ts, t)
    if i <= 0:
        return samples[0].v
    if i >= len(samples):
        return samples[-1].v
    a, b = samples[i - 1], samples[i]
    if b.t == a.t:
        return b.v
    return a.v + (b.v - a.v) * (t - a.t) / (b.t - a.t)


def between(samples: Sequence[Sample], t1: float, t2: float) -> list[Sample]:
    ts = _times(samples)
    i, j = bisect.bisect_left(ts, t1), bisect.bisect_right(ts, t2)
    return list(samples[i:j])


def mean(samples: Sequence[Sample]) -> float | None:
    return (sum(s.v for s in samples) / len(samples)) if samples else None


def time_weighted_mean(samples: Sequence[Sample], t1: float, t2: float) -> float | None:
    """The mean of a series over [t1, t2] weighting each sample by the time
    it stands for (half the gap to each neighbour, at most WEIGHT_CAP_S),
    so a stretch sampled every 5 s and a stretch sampled every minute
    count by duration and a sample beside a lost-pulse gap does not
    speak for the gap."""
    pts = between(samples, t1, t2)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0].v
    total = 0.0
    weight = 0.0
    for k, s in enumerate(pts):
        left = pts[k - 1].t if k > 0 else t1
        right = pts[k + 1].t if k < len(pts) - 1 else t2
        w = min(WEIGHT_CAP_S, max(0.0, (right - left) / 2.0))
        total += s.v * w
        weight += w
    return total / weight if weight > 0 else mean(pts)


def rolling(samples: Sequence[Sample], window_s: float = ROLL_S) -> list[Sample]:
    """A trailing mean over window_s, one point per sample."""
    out: list[Sample] = []
    acc: list[Sample] = []
    for s in samples:
        acc.append(s)
        while acc and acc[0].t < s.t - window_s:
            acc.pop(0)
        out.append(Sample(s.t, sum(a.v for a in acc) / len(acc)))
    return out


# ── pauses ────────────────────────────────────────────────────────────
def _speed_intervals(run: RunData) -> list[tuple[float, float, float]]:
    """(start, end, m/s) between consecutive route points (the point's
    own speed), else between consecutive cumulative-distance samples."""
    if run.route:
        pts = [p for p in run.route if p.speed is not None]
        return [(a.t, b.t, float(a.speed)) for a, b in zip(pts, pts[1:]) if b.t > a.t]
    if run.has_distance():
        return [(a.t, b.t, max(0.0, (b.v - a.v) * 1000.0 / (b.t - a.t))) for a, b in zip(run.dist, run.dist[1:]) if b.t > a.t]
    return []


def pauses(run: RunData, *, gap_s: float = GAP_S, still_mps: float = STILL_MPS, still_s: float = STILL_S) -> list[Pause]:
    """Standing still (speed under still_mps for still_s or more), the
    Watch's own pauses (the route's longest gaps, until they account for
    the paused time the summary reports) and the stops between a split
    run's parts. Overlaps merge. Gaps in the heart-rate series are not
    pauses: see `hr_gaps`."""
    found: list[Pause] = []
    paused_by_watch = run.watch_paused_s
    if paused_by_watch >= WATCH_PAUSE_MIN_S and len(run.route) >= 3:
        gaps = [(a.t, b.t) for a, b in zip(run.route, run.route[1:]) if b.t - a.t > gap_s]
        gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
        accounted = 0.0
        for a, b in gaps:
            if accounted >= paused_by_watch + gap_s:
                break
            found.append(Pause(a, b, "gap"))
            accounted += b - a
    start = end = None
    for a, b, v in _speed_intervals(run):
        if v < still_mps:
            if start is None:
                start = a
            end = b
        else:
            if start is not None and end is not None and end - start >= still_s:
                found.append(Pause(start, end, "still"))
            start = end = None
    if start is not None and end is not None and end - start >= still_s:
        found.append(Pause(start, end, "still"))
    for a, b in run.part_gaps:
        found.append(Pause(a, b, "split"))
    found.sort(key=lambda p: p.start)
    merged: list[Pause] = []
    for p in found:
        if merged and p.start <= merged[-1].end + 1.0:
            merged[-1].end = max(merged[-1].end, p.end)
            if merged[-1].kind != p.kind and "split" not in (merged[-1].kind, p.kind):
                merged[-1].kind = "gap"
        else:
            merged.append(Pause(p.start, p.end, p.kind))
    return merged


def hr_gaps(run: RunData, *, min_s: float = HR_GAP_MIN_S, cadence_x: float = HR_GAP_CADENCE_X) -> list[Pause]:
    """Gaps in the heart-rate series longer than max(min_s, cadence_x × the
    series' median cadence): the Watch losing the pulse (sweat, a loose
    strap) or a stop it did not pause for. Reported, never subtracted
    from the moving time; a minute-by-minute series has no gaps under
    three minutes by nature."""
    if len(run.hr) < 3:
        return []
    dts = sorted(b.t - a.t for a, b in zip(run.hr, run.hr[1:]))
    threshold = max(min_s, cadence_x * dts[len(dts) // 2])
    return [Pause(a.t, b.t, "signal") for a, b in zip(run.hr, run.hr[1:]) if b.t - a.t > threshold]


def paused_seconds(ps: Sequence[Pause], t1: float, t2: float) -> float:
    return sum(max(0.0, min(p.end, t2) - max(p.start, t1)) for p in ps)


def moving_time(run: RunData, ps: Sequence[Pause]) -> float:
    """Elapsed time minus the pauses when the run has a speed series (a
    route or the distance series); without one, the Watch's own active
    duration (its pauses already out) minus the split stops, so a heart-
    rate-only run keeps the Watch's moving time and a stop it did not
    pause for stays inside it."""
    if run.route or run.has_distance():
        return max(0.0, run.elapsed_s - paused_seconds(ps, 0.0, run.elapsed_s))
    split = sum(b - a for a, b in run.part_gaps)
    return max(0.0, min(run.duration_s, run.elapsed_s - split))


def longest_pause(ps: Sequence[Pause], t1: float, t2: float) -> float:
    return max((min(p.end, t2) - max(p.start, t1) for p in ps if p.end > t1 and p.start < t2), default=0.0)


# ── speed ─────────────────────────────────────────────────────────────
def speed_series(run: RunData) -> list[Sample]:
    """m/s: from the cumulative distance (differences over time), else the
    route's own speed. Empty when neither exists (an indoor run, a
    provisional run)."""
    if run.has_distance():
        out: list[Sample] = []
        for a, b in zip(run.dist, run.dist[1:]):
            dt = b.t - a.t
            if dt > 0:
                out.append(Sample(b.t, max(0.0, (b.v - a.v) * 1000.0 / dt)))
        return out
    if run.route:
        return [Sample(p.t, p.speed) for p in run.route if p.speed is not None]
    return []


def distance_between(run: RunData, t1: float, t2: float) -> float | None:
    """km covered in [t1, t2] from the cumulative series; from the route's
    speed when there is none; None without either."""
    if run.has_distance():
        a, b = value_at(run.dist, t1), value_at(run.dist, t2)
        return max(0.0, (b or 0.0) - (a or 0.0)) if a is not None and b is not None else None
    if run.route:
        pts = [p for p in run.route if t1 <= p.t <= t2 and p.speed is not None]
        if len(pts) < 2:
            return None
        km = 0.0
        for a, b in zip(pts, pts[1:]):
            km += ((a.speed or 0.0) + (b.speed or 0.0)) / 2.0 * (b.t - a.t) / 1000.0
        return km
    return None


def pace_s_per_mi(km: float | None, seconds: float) -> float | None:
    if not km or km <= 0 or seconds <= 0:
        return None
    return seconds / (km / 1.609344)


# ── the ramp, the strides, the cooldown, the main block ───────────────
def ramp(run: RunData, zones: dict, ps: Sequence[Pause] | None = None) -> dict:
    """Minutes from the start until the 60-s rolling heart rate first
    reaches sweet_lo and stays >= zone2_lo for two minutes. 'reached'
    False when it never does (a rest-day jog, an easy run)."""
    sweet, floor = zones.get("sweet_lo"), zones.get("zone2_lo")
    if not run.hr or sweet is None or floor is None:
        return {"minutes": None, "reached": False, "reason": "no heart rate or no zones"}
    roll = rolling(run.hr)
    for i, s in enumerate(roll):
        if s.v >= sweet:
            hold_until = s.t + RAMP_HOLD_S
            ok = True
            for r in roll[i:]:
                if r.t > hold_until:
                    break
                if r.v < floor:
                    ok = False
                    break
            if ok:
                return {"minutes": round(s.t / 60.0, 1), "reached": True, "reason": ""}
    return {"minutes": None, "reached": False, "reason": f"the rolling heart rate never held {sweet:.0f}"}


def cooldown_start(run: RunData, hr_below: float | None) -> float | None:
    """The start of a closing stretch with the heart rate under hr_below
    for more than COOLDOWN_S, or None (also None without a band)."""
    if not run.hr or hr_below is None:
        return None
    below_from: float | None = None
    for s in run.hr:
        if s.v < hr_below:
            if below_from is None:
                below_from = s.t
        else:
            below_from = None
    if below_from is not None and run.hr[-1].t - below_from > COOLDOWN_S:
        return below_from
    return None


def strides(run: RunData, block_pace_mps: float | None, after: float = 0.0) -> list[tuple[float, float]]:
    """10–30 s surges more than 20% faster than the block's speed, after
    `after` seconds. Needs a speed series; [] without one."""
    sp = speed_series(run)
    if not sp or not block_pace_mps:
        return []
    threshold = block_pace_mps * STRIDE_FASTER
    out: list[tuple[float, float]] = []
    start: float | None = None
    last_t = after
    for s in sp:
        if s.t < after:
            continue
        if s.v >= threshold:
            if start is None:
                start = last_t
        else:
            if start is not None:
                length = last_t - start
                if STRIDE_MIN_S <= length <= STRIDE_MAX_S:
                    out.append((start, last_t))
                start = None
        last_t = s.t
    return out


def main_block(run: RunData, zones: dict, ps: Sequence[Pause] | None = None) -> Block:
    """From the end of the ramp to the strides, the cooldown or the end.
    Without a ramp (never in band), the block starts at minute 10, the
    protocol's ramp length, so an easy run still has a steady block."""
    ps = list(ps or [])
    r = ramp(run, zones, ps)
    start = (r["minutes"] * 60.0) if r.get("minutes") is not None else min(600.0, run.elapsed_s / 4.0)
    end = run.elapsed_s
    reason = "the end of the run"
    floor = zones.get("zone2_lo")
    hr_below = (floor - COOLDOWN_BELOW_BAND) if floor is not None else None
    cd = cooldown_start(run, hr_below)
    if cd is not None and cd > start + 600:
        end, reason = cd, f"a cooldown (heart rate under {hr_below:.0f})"
    # strides: measured against the block's own pace, then the block ends where they begin
    km = distance_between(run, start, end)
    secs = (end - start) - paused_seconds(ps, start, end)
    pace_mps = (km * 1000.0 / secs) if km and secs > 0 else None
    st = strides(run, pace_mps, after=start + 600)
    if len(st) >= 2 and st[0][0] > start + 600:
        end, reason = st[0][0], "the first stride"
    return Block(start, end, reason)


# ── artifacts ─────────────────────────────────────────────────────────
def spikes(run: RunData, zones: dict, ps: Sequence[Pause], gaps: Sequence[Pause] = ()) -> list[dict]:
    """What to exclude from the metrics and say in the data notes: an
    early spike (the first minutes well above the band: a hill, a surge
    or the cadence lock), and post-stop readings (within 70 s after a
    pause, or after a heart-rate gap of 20 s or more, the Watch reports
    the stopped heart rate into the next split: the Sep 20 '132 bpm')."""
    out: list[dict] = []
    if not run.hr:
        return out
    cap = zones.get("cap") or zones.get("zone2_hi")
    early = [s for s in run.hr if s.t <= EARLY_SPIKE_S]
    if cap and early:
        peak = max(early, key=lambda s: s.v)
        if peak.v >= cap + 5:
            out.append({"kind": "early_spike", "t": round(peak.t), "bpm": round(peak.v),
                        "why": "a hill, a surge off the line, or the Watch locking onto cadence in the first minutes"})
    stops = sorted(list(ps) + [g for g in gaps if g.seconds >= POST_STOP_GAP_S], key=lambda p: p.start)
    for p in stops:
        after = between(run.hr, p.end, p.end + POST_STOP_S)
        before = between(run.hr, max(0.0, p.start - 120), p.start)
        if after and before:
            lo = min(after, key=lambda s: s.v)
            ref = mean(before)
            if ref is not None and lo.v <= ref - 10:
                out.append({"kind": "post_stop", "t": round(lo.t), "bpm": round(lo.v), "pause_end": round(p.end),
                            "why": f"a reading {round(ref - lo.v)} bpm under the pre-stop level inside {POST_STOP_S:.0f} s "
                                   "of a stop: the stopped heart rate bleeding into the next split"})
    return out
