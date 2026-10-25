"""agents.kipchoge.plan — the plan in force, the session library and a
prescription from the model (PRD v0.3 §5.5, §7.2, E3).

The plan is `vault/kipchoge/plan.json`, imported as dated weeks (the
Oct 18 plan: week 1 started Oct 19; the store says what happened). A
session is typed; its numbers (minutes, the band, the cap, strides)
are the plan's. The expected pace for a session is never a number
someone said: `prescribe` moves the rolling estimate (the normalized
pace at the reference heart rate) to the target heart rate on the
athlete's HR–pace line and widens the range with the slope's
uncertainty and the conditions, or, without conditions, gives the
HR-only range and says so (E3's fallback). Every value is a record.

The library (`SESSIONS`) is the shape of each session type with the
cues from the thread; the athlete's numbers (bands, caps) come from the
plan and the athlete file, never from here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from agents.kipchoge import model as cmodel
from agents.kipchoge import state
from agents.kipchoge.records import Record, RecordSet, rid

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# the shape of each session type; no athlete number lives here
LABELS = {"z2_steady": "Zone 2 steady", "z2_strides": "Zone 2 + strides", "easy_strides": "easy run + strides", "easy": "easy run",
          "long": "long run", "drift_test": "drift test", "benchmark": "the benchmark", "recovery": "recovery jog"}

SESSIONS: dict[str, dict] = {
    "z2_steady": {"purpose": "hold the aerobic band for an hour-plus; the week's measurement of fitness",
                  "ramp_min": 10, "ramp": "the first minutes easy by pace, then settle on the band by minute ten",
                  "governing": "hr", "strides": 0,
                  "cues": ["ramp by pace, not by feel", "hover at the band's top, never over the cap",
                           "if the heart rate passes the cap, ease off until it is back in the band; never chase pace"]},
    "z2_strides": {"purpose": "the steady run, then six short fast strides for economy at negligible cost",
                   "ramp_min": 10, "ramp": "the first minutes easy by pace, then settle on the band by minute ten",
                   "governing": "hr", "strides": 6, "stride_s": 20, "stride_effort_pct": 90, "stride_recovery_s": 60,
                   "walk_min_after": 2,
                   "cues": ["after the steady part: walk a minute or two first", "quick and light; land under the hips",
                            "tall and relaxed; no reaching; the turnover rises, the stride does not lengthen",
                            "full recovery between strides; they are not a workout"]},
    "easy_strides": {"purpose": "an easy midweek run with strides: frequency and economy without load",
                     "ramp_min": 5, "ramp": "easy from the start", "governing": "hr", "strides": 6, "stride_s": 20,
                     "stride_effort_pct": 90, "stride_recovery_s": 60, "walk_min_after": 1,
                     "cues": ["conversational the whole way", "strides at the end, relaxed and quick"]},
    "easy": {"purpose": "an easy run; frequency, not fitness", "ramp_min": 5, "ramp": "easy from the start",
             "governing": "hr", "strides": 0, "cues": ["conversational the whole way"]},
    "long": {"purpose": "time on feet in the band", "ramp_min": 10, "ramp": "the first minutes easy by pace",
             "governing": "hr", "strides": 0, "cues": ["the second half is the run: hold the band when it gets hard"]},
    "drift_test": {"purpose": "the ceiling test: the main block locked at the band, read as first half against second",
                   "ramp_min": 10, "ramp": "ten minutes up to the band", "governing": "hr", "strides": 0,
                   "cues": ["no stops, no surges", "pee before the watch starts", "phone on do-not-disturb"]},
    "benchmark": {"purpose": "the measurement: a cool morning on the benchmark route, no stops",
                  "ramp_min": 10, "ramp": "ten minutes up to the band", "governing": "hr", "strides": 0,
                  "cues": ["no new shoes", "pee before the watch starts", "phone on do-not-disturb", "no stops"]},
    "recovery": {"purpose": "legs moving, nothing else", "ramp_min": 0, "ramp": "none", "governing": "rpe", "strides": 0,
                 "cues": ["slower than feels necessary"]},
}

STRIDES_HOW = [
    "about nine-tenths effort, not a sprint: fast but relaxed, the kind of fast you could hold for a minute",
    "each one builds over the first few seconds, holds, and eases out; no lunge at the start",
    "quicker turnover, feet landing under the hips, tall through the chest, arms compact; no reaching ahead, no high knees",
    "walk back or jog very easy until the breath is fully back; the rest is the point, not the strain",
    "why: a handful of fast, relaxed seconds teaches the legs economy without a load the band would pay for; the aerobic work stays aerobic",
]


@dataclass
class Session:
    id: str
    date: str                   # YYYY-MM-DD
    type: str
    minutes: float
    minutes_lo: float | None = None
    minutes_hi: float | None = None
    hr_lo: float | None = None
    hr_hi: float | None = None
    cap: float | None = None
    strides: int = 0
    route: str | None = None
    time: str | None = None     # HH:MM local
    optional: bool = False
    note: str = ""
    week: int | None = None
    weeks: int | None = None


def load_plan(plan_file: dict | None = None) -> dict:
    return plan_file if plan_file is not None else state.load_json(state.PLAN_FILE, {})


def sessions(plan: dict) -> list[Session]:
    out: list[Session] = []
    weeks = plan.get("weeks") or []
    for w in weeks:
        for s in w.get("sessions") or []:
            out.append(Session(id=s.get("id") or f"w{w.get('n')}-{s.get('date')}", date=s["date"], type=s.get("type", "z2_steady"),
                               minutes=float(s.get("minutes") or 0), minutes_lo=s.get("minutes_lo"), minutes_hi=s.get("minutes_hi"),
                               hr_lo=s.get("hr_lo"), hr_hi=s.get("hr_hi"), cap=s.get("cap"), strides=int(s.get("strides") or 0),
                               route=s.get("route"), time=s.get("time"), optional=bool(s.get("optional")), note=s.get("note", ""),
                               week=w.get("n"), weeks=len(weeks)))
    return sorted(out, key=lambda s: (s.date, s.time or ""))


def week_of(plan: dict, day: str) -> tuple[int | None, int]:
    weeks = plan.get("weeks") or []
    for w in weeks:
        if w.get("start") <= day <= w.get("end"):
            return w.get("n"), len(weeks)
    return None, len(weeks)


def next_session(plan: dict, now: datetime, *, days: int = 7) -> Session | None:
    """The first planned session on or after today (today's counts until
    its time has passed by two hours)."""
    today = now.strftime("%Y-%m-%d")
    for s in sessions(plan):
        if s.date < today:
            continue
        if s.date == today and s.time:
            hh, mm = (int(x) for x in s.time.split(":"))
            if now > now.replace(hour=hh, minute=mm, second=0) + timedelta(hours=2):
                continue
        if s.date > (now + timedelta(days=days)).strftime("%Y-%m-%d"):
            return None
        return s
    return None


def session_on(plan: dict, day: str) -> Session | None:
    for s in sessions(plan):
        if s.date == day:
            return s
    return None


def session_records(s: Session, plan: dict, *, prefix: str | None = None) -> list[Record]:
    """A planned session's numbers as records, `plan:<date>:<field>`."""
    p = prefix or rid("plan", s.date)
    src = f"the plan in force ({plan.get('name') or plan.get('id') or 'plan'}), session {s.id}"
    wk, total = s.week, s.weeks
    out = [Record(f"{p}:date", s.date, "local", source=src), Record(f"{p}:weekday", WEEKDAYS[date.fromisoformat(s.date).weekday()], "name", source=src),
           Record(f"{p}:type", s.type, "name", source=src), Record(f"{p}:label", LABELS.get(s.type, s.type.replace("_", " ")), "name", source=src),
           Record(f"{p}:minutes", s.minutes, "min", lo=s.minutes_lo, hi=s.minutes_hi, source=src),
           Record(f"{p}:strides", s.strides, "count", source=src), Record(f"{p}:optional", s.optional, "flag", source=src)]
    if s.hr_lo is not None and s.hr_hi is not None:
        out.append(Record(f"{p}:hr_lo", s.hr_lo, "bpm", source=src))
        out.append(Record(f"{p}:hr_hi", s.hr_hi, "bpm", source=src))
    if s.cap is not None:
        out.append(Record(f"{p}:cap", s.cap, "bpm", source=src))
    if s.route:
        out.append(Record(f"{p}:route", s.route, "name", source=src))
    if s.time:
        out.append(Record(f"{p}:time", s.time, "local", source=src))
    if wk is not None:
        out.append(Record(f"{p}:week", wk, "count", source=src))
        out.append(Record(f"{p}:weeks", total, "count", source=src))
        out.append(Record(f"{p}:week_of_block", f"week {wk} of {total}", "text", source=src))
    if s.note:
        out.append(Record(f"{p}:note", s.note, "text", source=src))
    return out


def library_records(stype: str) -> list[Record]:
    lib = SESSIONS.get(stype)
    if not lib:
        return [Record(rid("lib", stype, "known"), False, "flag", source="the session library")]
    src = "the session library (PRD §7.2)"
    out = [Record(rid("lib", stype, "label"), LABELS.get(stype, stype.replace("_", " ")), "name", source=src),
           Record(rid("lib", stype, "purpose"), lib["purpose"], "text", source=src),
           Record(rid("lib", stype, "ramp_min"), lib["ramp_min"], "min", source=src),
           Record(rid("lib", stype, "ramp"), lib["ramp"], "text", source=src),
           Record(rid("lib", stype, "strides"), lib.get("strides", 0), "count", source=src)]
    for k in ("stride_s", "stride_effort_pct", "stride_recovery_s", "walk_min_after"):
        if k in lib:
            unit = {"stride_s": "s", "stride_effort_pct": "%", "stride_recovery_s": "s", "walk_min_after": "min"}[k]
            out.append(Record(rid("lib", stype, k), lib[k], unit, source=src))
    for i, c in enumerate(lib.get("cues", []), 1):
        out.append(Record(rid("lib", stype, f"cue.{i}"), c, "text", source=src))
    return out


def strides_records() -> list[Record]:
    return [Record(rid("lib", "strides", f"how.{i}"), t, "text", source="the session library's stride cues (Jun 27)")
            for i, t in enumerate(STRIDES_HOW, 1)]


# ── a prescription from the model (E3) ────────────────────────────────
def prescribe(*, estimate: cmodel.Estimate | None, slope: cmodel.Slope, ref_hr: float | None, target_lo: float, target_hi: float,
              minutes: float, conditions: dict | None = None, label: str = "next", zones: dict | None = None) -> list[Record]:
    """The expected pace range at the target band, from the rolling
    estimate at the reference heart rate moved along the HR–pace line,
    widened by the slope's uncertainty and, when conditions are known,
    the heat prior; the expected distance from pace and minutes. Without
    an estimate or a reference, no pace is prescribed and the record says
    why: the band and the cap still stand (E3's HR-only anchor)."""
    p = rid("rx", label)
    out: list[Record] = [Record(f"{p}:hr_lo", target_lo, "bpm", source="the plan's band"),
                         Record(f"{p}:hr_hi", target_hi, "bpm", source="the plan's band"),
                         Record(f"{p}:minutes", minutes, "min", source="the plan")]
    if estimate is None or estimate.pace_s_per_mi is None or ref_hr is None:
        out.append(Record(f"{p}:basis", "no pace range: no rolling estimate on file yet (the band and the cap are the anchor)",
                          "text", source="the model"))
        return out
    mid_hr = (target_lo + target_hi) / 2.0
    delta = ref_hr - mid_hr                         # bpm under the reference → slower by slope × delta
    centre = estimate.pace_s_per_mi + slope.s_per_mi_per_bpm * delta
    half_est = ((estimate.hi - estimate.lo) / 2.0) if (estimate.hi is not None and estimate.lo is not None) else 20.0
    half_slope = slope.sd * abs(delta)
    band_half = slope.s_per_mi_per_bpm * (target_hi - target_lo) / 2.0
    heat_bpm = 0.0
    why = [f"the rolling estimate at the reference heart rate moved {delta:+.0f} bpm along the line ({slope.s_per_mi_per_bpm} s/mi per bpm)"]
    basis = "HR-only (no conditions)"
    if conditions:
        adj = cmodel.heat_adjustment(conditions.get("apparent_f"), conditions.get("dew_point_f"), conditions.get("sun"))
        heat_bpm = adj.bpm
        if heat_bpm:
            why.append(f"heat {heat_bpm:+.1f} bpm at the planned conditions")
        basis = "with the forecast conditions"
    centre += slope.s_per_mi_per_bpm * heat_bpm
    half = math.sqrt(half_est ** 2 + half_slope ** 2 + band_half ** 2)
    lo, hi = centre - half, centre + half
    out += [Record(f"{p}:pace_s_per_mi", round(centre, 1), "s/mi", lo=round(lo, 1), hi=round(hi, 1), source="; ".join(why)),
            Record(f"{p}:pace_lo", round(lo, 1), "s/mi", source="the range's fast end"),
            Record(f"{p}:pace_hi", round(hi, 1), "s/mi", source="the range's slow end"),
            Record(f"{p}:basis", basis, "text", source="the model")]
    km_lo = minutes * 60.0 / hi * 1.609344 if hi > 0 else None
    km_hi = minutes * 60.0 / lo * 1.609344 if lo > 0 else None
    if km_lo and km_hi:
        out.append(Record(f"{p}:distance_km", round((km_lo + km_hi) / 2, 2), "km", lo=round(km_lo, 2), hi=round(km_hi, 2),
                          source="minutes over the pace range"))
        out.append(Record(f"{p}:distance_km_lo", round(km_lo, 2), "km", source="minutes at the slow end"))
        out.append(Record(f"{p}:distance_km_hi", round(km_hi, 2), "km", source="minutes at the fast end"))
    if zones:
        ramp = SESSIONS.get("z2_steady", {}).get("ramp_min", 10)
        out.append(Record(f"{p}:ramp_min", ramp, "min", source="the session library"))
    return out


def fits(pace_s_per_mi: float, rx: RecordSet | list[Record], label: str = "next", tolerance_s: float = 0.0) -> bool | None:
    """E3: does a stated pace lie inside the prescription's range? None
    when the prescription carried no range."""
    recs = {r.id: r for r in (rx if not isinstance(rx, RecordSet) else list(rx))}
    r = recs.get(rid("rx", label, "pace_s_per_mi"))
    if r is None or r.lo is None or r.hi is None:
        return None
    return (r.lo - tolerance_s) <= pace_s_per_mi <= (r.hi + tolerance_s)
