"""agents.kipchoge.ingest — one run, read from the store into samples.

The bridge keeps a run in five places: the summary row (`workouts_hk`,
local-naive times), the workout's own series and route (`workout_series`,
`workout_route`, UTC plus offset), the Watch's day series (`raw_vitals`,
local-naive: heart rate every ~5 s inside a workout, the running-form
metrics every minute, steps and distance per minute), the sleep sessions
and the scalars (`workout_meta`). `load_run` joins them on time into one
`RunData`: every sample is seconds from the run's start, so the engine
never sees a clock, and the DST change and a trip cannot shift a sample
(PRD §6.6). Two lengths travel with a run: `elapsed_s` (end minus start;
the series span it) and `duration_s` (the Watch's active duration, its
own pauses already out: the moving time when there is no speed series).

Preference order for the heart rate: the workout's own series, then the
day series inside the window (older runs, before the bridge kept the
series). Distance: the workout's own series (cumulative km), then the
route's speed, then nothing (the day series sums to ~6 of 10 km inside a
run and is never used for pace: PRD §6.1).

A provisional run (`provisional_run`) is what the minute data shows
before the workout file lands: heart rate and time only (PRD §6.1),
from Huberman's detectors (the running-form minutes, or the dense
heart-rate stretch).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bridges.healthkit import sleep as hk_sleep
from bridges.healthkit import workouts as hkw

HOME_ZONE = "America/Los_Angeles"
FORM_METRICS = ("running_speed", "running_power", "running_ground_contact_time", "running_vertical_oscillation",
                "running_stride_length")
RECOVERY_MIN = 3             # minutes of the day series after the end that stand in for the recovery series
NIGHT_START_WINDOW_H = hk_sleep.NIGHT_START_WINDOW_H    # the shared reader's windows (bridges.healthkit.sleep)
PRIOR_NIGHT_WINDOW_H = hk_sleep.PRIOR_NIGHT_WINDOW_H


@dataclass
class Sample:
    t: float            # seconds from the run's start
    v: float


@dataclass
class RoutePoint:
    t: float
    lat: float
    lon: float
    alt: float | None
    speed: float | None     # m/s
    h_acc: float | None = None


@dataclass
class RunData:
    workout_id: str
    name: str
    start_local: datetime
    duration_s: float                   # the Watch's active duration (its pauses out)
    offset_min: int = 0
    elapsed_s: float = 0.0              # end minus start; every series is inside it
    distance_km: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    kcal: float | None = None
    hr: list[Sample] = field(default_factory=list)
    hr_source: str = ""
    dist: list[Sample] = field(default_factory=list)          # cumulative km at t
    dist_source: str = ""
    route: list[RoutePoint] = field(default_factory=list)
    form: dict[str, list[Sample]] = field(default_factory=dict)
    steps: list[Sample] = field(default_factory=list)         # steps per minute sample
    hr_recovery: list[Sample] = field(default_factory=list)   # t from the END of the run
    recovery_source: str = ""
    meta: dict = field(default_factory=dict)
    sleep: dict = field(default_factory=dict)
    merged_parts: list[str] = field(default_factory=list)
    part_gaps: list[tuple[float, float]] = field(default_factory=list)   # the stops between a split run's parts
    provisional: bool = False
    indoor: bool = False

    def __post_init__(self) -> None:
        if self.elapsed_s < self.duration_s:
            self.elapsed_s = self.duration_s

    @property
    def start_utc(self) -> str:
        return (self.start_local - timedelta(minutes=self.offset_min)).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def end_local(self) -> datetime:
        return self.start_local + timedelta(seconds=self.elapsed_s)

    @property
    def watch_paused_s(self) -> float:
        """What the Watch itself paused: elapsed minus its active duration."""
        return max(0.0, self.elapsed_s - self.duration_s)

    @property
    def day(self) -> str:
        return self.start_local.strftime("%Y-%m-%d")

    def has_route(self) -> bool:
        return len(self.route) >= 10

    def has_distance(self) -> bool:
        return len(self.dist) >= 2


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return bool(_rows(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


# ── the reads ─────────────────────────────────────────────────────────
def summary_row(con: sqlite3.Connection, workout_id: str) -> dict | None:
    rows = _rows(con, "SELECT workout_id, name, start, end, duration_s, active_kcal, avg_hr, max_hr, distance_km "
                      "FROM workouts_hk WHERE workout_id = ?", (workout_id,))
    if not rows:
        return None
    r = rows[0]
    return {"workout_id": r[0], "name": r[1] or "", "start": r[2], "end": r[3], "duration_s": r[4], "kcal": r[5],
            "avg_hr": r[6], "max_hr": r[7], "distance_km": r[8]}


def _series(con: sqlite3.Connection, workout_id: str, kind: str) -> list[tuple[str, int, float | None, float | None, float | None]]:
    if not _table_exists(con, "workout_series"):
        return []
    return _rows(con, "SELECT ts_utc, offset_min, min, avg, max FROM workout_series WHERE workout_id=? AND kind=? ORDER BY ts_utc",
                 (workout_id, kind))


def _route(con: sqlite3.Connection, workout_id: str) -> list[tuple]:
    if not _table_exists(con, "workout_route"):
        return []
    return _rows(con, "SELECT ts_utc, offset_min, lat, lon, alt_m, speed_mps, h_acc_m FROM workout_route WHERE workout_id=? "
                      "ORDER BY seq", (workout_id,))


def _meta(con: sqlite3.Connection, workout_id: str) -> dict:
    if not _table_exists(con, "workout_meta"):
        return {}
    out = {}
    for key, value, text, units in _rows(con, "SELECT key, value, text, units FROM workout_meta WHERE workout_id=?", (workout_id,)):
        out[key] = {"value": value, "text": text, "units": units}
    return out


def _vitals(con: sqlite3.Connection, metric: str, start: datetime, end: datetime) -> list[tuple[str, float]]:
    return _rows(con, "SELECT timestamp, value FROM raw_vitals WHERE metric_type=? AND timestamp >= ? AND timestamp <= ? "
                      "ORDER BY timestamp", (metric, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")))


def sleep_before(con: sqlite3.Connection, start_local: datetime) -> dict:
    """The night before the run and the two-night average (PRD §6.6), in
    hours, from the shared reader `bridges.healthkit.sleep.night_before`
    (the stages when the session has them; the longest session starting
    within 24 h before the run; a shifted duplicate counted once)."""
    n = hk_sleep.night_before(con, start_local)
    return {"night_before_h": n["hours"], "two_night_avg_h": n["two_night_avg_h"], "night_end": n["end"],
            "method": n["method"], "sessions": n["sessions"]}


def load_run(con: sqlite3.Connection, workout_id: str, *, include_merged: bool = True) -> RunData | None:
    """One run with every series the store has, relative to its start.
    Merged parts (`workout_link merged_into` this run) are appended with
    their real time offsets; the stop between them becomes a pause."""
    row = summary_row(con, workout_id)
    if row is None or _dt(row["start"]) is None:
        return None
    start = _dt(row["start"])
    end = _dt(row["end"]) or (start + timedelta(seconds=float(row["duration_s"] or 0)))
    elapsed = max(0.0, (end - start).total_seconds())
    duration = float(row["duration_s"] or elapsed)
    run = RunData(workout_id=workout_id, name=row["name"], start_local=start, duration_s=duration, elapsed_s=max(elapsed, duration),
                  distance_km=row["distance_km"], avg_hr=row["avg_hr"], max_hr=row["max_hr"], kcal=row["kcal"])
    _fill(con, run, workout_id, start, end, t0=0.0)
    if include_merged and _table_exists(con, "workout_link"):
        parts = _rows(con, "SELECT l.workout_id FROM workout_link l JOIN workouts_hk w ON w.workout_id = l.workout_id "
                           "WHERE l.kind='merged_into' AND l.other_id=? ORDER BY w.start", (workout_id,))
        for (pid,) in parts:
            prow = summary_row(con, pid)
            pstart = _dt(prow["start"]) if prow else None
            if prow is None or pstart is None:
                continue
            pend = _dt(prow["end"]) or (pstart + timedelta(seconds=float(prow["duration_s"] or 0)))
            t0 = (pstart - start).total_seconds()
            p_elapsed = max(0.0, (pend - pstart).total_seconds())
            p_duration = float(prow["duration_s"] or p_elapsed)
            if t0 > run.elapsed_s:
                run.part_gaps.append((run.elapsed_s, t0))         # the stop between the parts
            _fill(con, run, pid, pstart, pend, t0=t0, part=True)
            run.merged_parts.append(pid)
            run.elapsed_s = max(run.elapsed_s, t0 + max(p_elapsed, p_duration))
            run.duration_s += p_duration
            if prow.get("distance_km") and run.distance_km:
                run.distance_km = round(run.distance_km + float(prow["distance_km"]), 3)
    run.sleep = sleep_before(con, start)
    run.indoor = bool((run.meta.get("is_indoor") or {}).get("value")) or \
        str((run.meta.get("location") or {}).get("text") or "").lower() == "indoor"
    return run


def _fill(con: sqlite3.Connection, run: RunData, workout_id: str, start: datetime, end: datetime, *, t0: float,
          part: bool = False) -> None:
    """The series of one workout id into `run`, offset by t0 seconds."""
    hr_rows = _series(con, workout_id, "hr")
    if hr_rows:
        if not part:
            run.offset_min = int(hr_rows[0][1] or 0)
        base_utc = (start - timedelta(minutes=int(hr_rows[0][1] or 0)))
        for ts, off, mn, avg, mx in hr_rows:
            t = (_dt(ts) - base_utc).total_seconds() + t0
            v = avg if avg is not None else (mx if mx is not None else mn)
            if v is not None:
                run.hr.append(Sample(t, float(v)))
        run.hr_source = run.hr_source or "the workout's own heart-rate series"
    else:
        for ts, v in _vitals(con, "heart_rate", start, end):
            run.hr.append(Sample((_dt(ts) - start).total_seconds() + t0, float(v)))
        if run.hr:
            run.hr_source = run.hr_source or "the Watch's day series inside the run"
    dist_rows = _series(con, workout_id, "distance")
    if dist_rows:
        # each entry is the distance covered in the interval STARTING at its date (HAE's series shape), so the
        # cumulative total holds at the entry's date before it is added, and the full total one interval after the last
        base_utc = (start - timedelta(minutes=int(dist_rows[0][1] or 0)))
        total = run.dist[-1].v if run.dist else 0.0
        times = [(_dt(ts) - base_utc).total_seconds() + t0 for ts, *_ in dist_rows]
        interval = (times[-1] - times[0]) / (len(times) - 1) if len(times) > 1 else 60.0
        for (ts, off, mn, avg, mx), t in zip(dist_rows, times):
            run.dist.append(Sample(t, round(total, 4)))
            total += float(avg if avg is not None else 0.0)
        run.dist.append(Sample(times[-1] + interval, round(total, 4)))
        run.dist_source = run.dist_source or "the workout's own distance series"
    route_rows = _route(con, workout_id)
    if route_rows:
        if not part and not hr_rows:
            run.offset_min = int(route_rows[0][1] or 0)
        base_utc = (start - timedelta(minutes=int(route_rows[0][1] or 0)))
        for ts, off, lat, lon, alt, speed, hacc in route_rows:
            run.route.append(RoutePoint((_dt(ts) - base_utc).total_seconds() + t0, float(lat), float(lon),
                                        float(alt) if alt is not None else None,
                                        float(speed) if speed is not None else None,
                                        float(hacc) if hacc is not None else None))
        if not dist_rows and not run.dist_source:
            run.dist_source = "the route's speed"
    for metric in FORM_METRICS:
        rows = _vitals(con, metric, start, end)
        if rows:
            run.form.setdefault(metric, []).extend(Sample((_dt(ts) - start).total_seconds() + t0, float(v)) for ts, v in rows)
    for ts, v in _vitals(con, "steps", start, end):
        run.steps.append(Sample((_dt(ts) - start).total_seconds() + t0, float(v)))
    rec_rows = _series(con, workout_id, "hr_recovery")
    if rec_rows and not part:
        end_utc = end - timedelta(minutes=int(rec_rows[0][1] or 0))
        for ts, off, mn, avg, mx in rec_rows:
            v = avg if avg is not None else mx
            if v is not None:
                run.hr_recovery.append(Sample((_dt(ts) - end_utc).total_seconds(), float(v)))
        run.recovery_source = "the workout's recovery series"
    elif not part:
        # older runs: the Watch's day series keeps reading after the end; three minutes of it is the recovery
        for ts, v in _vitals(con, "heart_rate", end + timedelta(seconds=1), end + timedelta(minutes=RECOVERY_MIN)):
            run.hr_recovery.append(Sample((_dt(ts) - end).total_seconds(), float(v)))
        if run.hr_recovery:
            run.recovery_source = "the Watch's day series after the end"
    if not part:
        run.meta = _meta(con, workout_id)


def runs_in(con: sqlite3.Connection, since: str, until: str | None = None, *, names=("run",)) -> list[str]:
    """Primary workout ids (no duplicates, no second parts) whose name
    matches, between two local dates (inclusive), oldest first."""
    rows = _rows(con, "SELECT workout_id, name, start FROM workouts_hk WHERE start >= ? AND start < ? ORDER BY start",
                 (since, (until or "9999-12-31") + " 23:59:59"))
    primary = hkw.primary_ids(con.cursor()) if _table_exists(con, "workout_link") else {r[0] for r in rows}
    return [r[0] for r in rows if r[0] in primary and any(n in (r[1] or "").lower() for n in names)]


# ── provisional ───────────────────────────────────────────────────────
def provisional_id(session: dict, *, offset_min: int | None = None) -> str | None:
    """A provisional run's id, `prov:<start utc>` (20261018T143000: no space
    and no colon, so the id fits a `{{r:...}}` placeholder and splits on
    ':' like `run:<uuid>`), from the detector's session dict alone (the
    pipeline's sent-once key is built on it)."""
    start = _dt(session.get("start"))
    if start is None:
        return None
    if offset_min is None:
        offset_min = home_offset_min(start)
    return "prov:" + (start - timedelta(minutes=offset_min)).strftime("%Y%m%dT%H%M%S")


def provisional_run(con: sqlite3.Connection, session: dict, *, offset_min: int | None = None) -> RunData | None:
    """A run the minute data shows before the file lands (Huberman's
    `sessions_from_vitals` dict: name, start, end, minutes, kind,
    inferred). Heart rate and time only; no distance, no route. Its id
    is `prov:<start utc>` (PRD v0.3 §6.1)."""
    start, end = _dt(session.get("start")), _dt(session.get("end"))
    if start is None or end is None:
        return None
    if offset_min is None:
        offset_min = home_offset_min(start)
    run = RunData(workout_id=provisional_id(session, offset_min=offset_min), name=session.get("name") or "Outdoor Run", start_local=start,
                  duration_s=(end - start).total_seconds(), elapsed_s=(end - start).total_seconds(), offset_min=offset_min,
                  provisional=True)
    for ts, v in _vitals(con, "heart_rate", start, end):
        run.hr.append(Sample((_dt(ts) - start).total_seconds(), float(v)))
    run.hr_source = "the Watch's day series (provisional; the workout file hasn't landed)"
    for metric in FORM_METRICS:
        rows = _vitals(con, metric, start, end)
        if rows:
            run.form[metric] = [Sample((_dt(ts) - start).total_seconds(), float(v)) for ts, v in rows]
    for ts, v in _vitals(con, "steps", start, end):
        run.steps.append(Sample((_dt(ts) - start).total_seconds(), float(v)))
    run.sleep = sleep_before(con, start)
    if run.hr:
        run.avg_hr = round(sum(s.v for s in run.hr) / len(run.hr), 1)
        run.max_hr = max(s.v for s in run.hr)
    return run


def home_offset_min(local: datetime) -> int:
    """The home zone's UTC offset at a local wall-clock time (E5: his home
    zone is America/Los_Angeles); -420 when the zone database is missing."""
    try:
        from zoneinfo import ZoneInfo
        off = local.replace(tzinfo=ZoneInfo(HOME_ZONE)).utcoffset()
        return int(off.total_seconds() // 60) if off is not None else -420
    except Exception:  # noqa: BLE001
        return -420
