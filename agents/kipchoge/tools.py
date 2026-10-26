"""agents.kipchoge.tools — the coach's tools (PRD v0.3 §12.2, S1 set).

Every tool returns evidence records and adds them to the ask's evidence
(`ctx["evidence"]`), so the checks and the renderer read the same set
the model saw. Reads of the athlete file, the plan, the library, the
store's analyses and the clock cost nothing and are never braked; the
weather is the one network tool (cacheable: it replays from cassettes
in tests). No tool prints a coordinate.

  get_athlete        his zones, cap, reference heart rate, units, with provenance per field
  get_goal           the goal in force
  get_plan           the plan in force: this week, the next session, a date's session
  get_session        a session type's shape and cues from the library
  list_runs          the recent runs: date, type, grade, pace, heart rate
  analyze_run        every record of one run's analysis (the engine's; analyzed on first ask)
  compare_runs       the last comparable runs beside this one
  trend              the rolling estimate and the goal forecast
  prescribe          the model's pace and distance range at a target band for a session (E3)
  clock              now, today, days since the last run, the block week, a date reference resolved
  weather_at         the forecast at a route's point for a day and hour (nothing without a point)
  route_info         a route's attributes from the library
  freshness          when the store last heard from the Watch and the phone
  population_estimate a population comparison with its derivation and its low confidence (journey H)
  strides_how        the stride cues (journey K)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from agents.kipchoge import engine as cengine
from agents.kipchoge import ingest
from agents.kipchoge import model as cmodel
from agents.kipchoge import plan as cplan
from agents.kipchoge import state
from agents.kipchoge.conditions import conditions_for_run
from agents.kipchoge.records import ENGINE_VERSION, Record, RecordSet, rid
from core.tools import ToolRegistry

AGENT = "kipchoge"


class NoArgs(BaseModel):
    pass


class RunRefArgs(BaseModel):
    which: str = Field("last", description="last | date | uuid")
    value: str | None = Field(None, description="a local date YYYY-MM-DD, 'yesterday', or the workout uuid")


class CompareArgs(RunRefArgs):
    n: int = Field(5, ge=1, le=8)


class SessionArgs(BaseModel):
    type: str = Field(..., description="a session type: z2_steady, z2_strides, easy_strides, easy, long, drift_test, benchmark, recovery")


class PrescribeArgs(BaseModel):
    date: str | None = Field(None, description="the session's date (YYYY-MM-DD) when it is a planned one; else None")
    type: str | None = Field(None, description="a session type when no planned session: z2_steady, easy …")
    hr_lo: float | None = Field(None, description="the target band's floor, from the plan or the athlete file (never a guess)")
    hr_hi: float | None = Field(None, description="the band's ceiling")
    minutes: float | None = None
    with_conditions: bool = Field(True, description="use the forecast for the date and route when on file")


class ClockArgs(BaseModel):
    date_ref: str | None = Field(None, description="'today', 'tomorrow', 'yesterday', a weekday name or YYYY-MM-DD to resolve")


class WeatherArgs(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    hour: int = Field(7, ge=0, le=23)
    route: str | None = None


class RouteArgs(BaseModel):
    name: str = Field(..., description="the route's name or key, as he said it")


class PopulationArgs(BaseModel):
    age: int = Field(..., ge=18, le=90)
    sex: str = Field("male")


def _ev(c: dict):
    return c["evidence"]


def _con(c: dict):
    return c["con"]


def _add(c: dict, records: list[Record]) -> dict:
    _ev(c).add(records)
    return {"records": [r.as_json() for r in records]}


def _athlete_records(a: dict) -> list[Record]:
    out: list[Record] = []
    hr = a.get("hr") or {}
    prov = hr.get("provenance") or {}
    src = lambda k: f"the athlete file: {prov.get(k) or prov.get(k.split('_')[0]) or 'provenance not stated'}"  # noqa: E731
    for k in ("max", "resting", "lt1_lo", "lt1_hi", "zone2_lo", "zone2_hi", "sweet_lo", "sweet_hi", "cap", "reference"):
        if hr.get(k) is not None:
            out.append(Record(rid("athlete", "hr", k), float(hr[k]), "bpm", source=src("lt1" if k.startswith("lt1") else "zone2" if k.startswith(("zone2", "sweet")) else k)))
    units = a.get("units") or {}
    out.append(Record(rid("athlete", "units", "pace"), units.get("pace", "min/mi"), "text", source="the athlete file"))
    out.append(Record(rid("athlete", "units", "distance"), units.get("distance", "km+mi"), "text", source="the athlete file"))
    body = a.get("body") or {}
    for k in ("age", "height_cm", "mass_kg"):
        v = body.get(k)
        if isinstance(v, dict) and v.get("value") is not None:
            unit = {"age": "count", "height_cm": "cm", "mass_kg": "kg"}[k]
            out.append(Record(rid("athlete", "body", k), v["value"], unit, source=f"the athlete file: {v.get('provenance', '')}", at=v.get("at", "")))
    m = a.get("model") or {}
    if m.get("slope_s_per_mi_per_bpm") is not None:
        out.append(Record(rid("athlete", "model", "slope_s_per_mi_per_bpm"), float(m["slope_s_per_mi_per_bpm"]), "s/mi/bpm",
                          source=f"the athlete file: {m.get('provenance', '')}"))
    tr = (a.get("training") or {})
    for k in ("long_min", "runs_per_week"):
        v = tr.get(k)
        if isinstance(v, dict) and v.get("value") is not None:
            out.append(Record(rid("athlete", "training", k), v["value"], "min" if k == "long_min" else "count",
                              source=f"the athlete file: {v.get('provenance', '')}"))
    return out


def _goal_records(g: dict) -> list[Record]:
    out: list[Record] = []
    p = g.get("primary") or {}
    if not p:
        return [Record("goal:primary:set", False, "flag", source="no goal on file")]
    src = f"the goal file: {p.get('provenance', 'stated')}"
    if p.get("pace_s_per_mi") is not None:
        out.append(Record("goal:primary:pace_s_per_mi", float(p["pace_s_per_mi"]), "s/mi", source=src))
    for k, unit in (("hr_lo", "bpm"), ("hr_hi", "bpm")):
        if p.get(k) is not None:
            out.append(Record(f"goal:primary:{k}", float(p[k]), unit, source=src))
    for k in ("date", "test_date", "test_route", "label", "measured_by"):
        if p.get(k):
            out.append(Record(f"goal:primary:{k}", p[k], "text" if k not in ("date", "test_date") else "local", source=src))
    return out


def _resolve_run(c: dict, which: str, value: str | None) -> str | None:
    con = _con(c)
    now: datetime = c["now"]
    if which == "uuid" and value:
        return value
    ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    if not ids:
        return None
    if which == "date" and value:
        day = value
        if value == "yesterday":
            day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        elif value == "today":
            day = now.strftime("%Y-%m-%d")
        for wid in reversed(ids):
            row = ingest.summary_row(con, wid)
            if row and str(row["start"]).startswith(day):
                return wid
        return None
    return ids[-1]


def _analysis_for(c: dict, wid: str) -> dict | None:
    con = _con(c)
    a = state.load_analysis(con, wid, ENGINE_VERSION)
    if a is None:
        a = cengine.analyze_workout(con, wid, now=c["now"], http=c.get("http"), zones=c.get("zones"), prior=c.get("slope_prior"),
                                    library=c.get("routes"), save=True)
    return a


def _run_records(a: dict) -> list[Record]:
    return list(RecordSet.from_json(a.get("records") or []))


def _ensure_analyses(c: dict, *, days: int = 120) -> None:
    """The trend and a prescription read saved analyses; a store that has
    runs but no analyses yet gets them (deterministic, cheap)."""
    con = _con(c)
    now: datetime = c["now"]
    if state.analyses(con, ENGINE_VERSION):
        return
    cengine.analyze_since(con, (now - timedelta(days=days)).strftime("%Y-%m-%d"), now=now, http=c.get("http"), zones=c.get("zones"),
                          prior=c.get("slope_prior"), library=c.get("routes"), save=True)


def _clock_records(c: dict, date_ref: str | None) -> list[Record]:
    now: datetime = c["now"]
    con = _con(c)
    out = [Record("clock:now", now.strftime("%Y-%m-%d %H:%M"), "local", source="the clock (local time)"),
           Record("clock:today", now.strftime("%Y-%m-%d"), "local", source="the clock"),
           Record("clock:weekday", cplan.WEEKDAYS[now.weekday()], "name", source="the clock")]
    ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    if ids:
        row = ingest.summary_row(con, ids[-1])
        last = ingest._dt(row["start"]) if row else None
        if last:
            days = (now.date() - last.date()).days
            out.append(Record("clock:last_run_day", last.strftime("%Y-%m-%d"), "local", source="the store's last primary run"))
            out.append(Record("clock:last_run_weekday", cplan.WEEKDAYS[last.weekday()], "name", source="the store"))
            out.append(Record("clock:days_since_last_run", days, "count", source="today minus the last run's local date"))
    else:
        out.append(Record("clock:days_since_last_run", None, "count", source="no run in the store"))
    plan = c.get("plan") or {}
    wk, total = cplan.week_of(plan, now.strftime("%Y-%m-%d"))
    if wk is not None:
        out.append(Record("clock:block_week", f"week {wk} of {total}", "text", source="the plan's dated weeks"))
        out.append(Record("clock:block_week_n", wk, "count", source="the plan's dated weeks"))
    if date_ref:
        d = _resolve_date(now, date_ref)
        if d:
            out.append(Record(rid("clock", date_ref, "date"), d.strftime("%Y-%m-%d"), "local", source=f"'{date_ref}' on the clock"))
            out.append(Record(rid("clock", date_ref, "weekday"), cplan.WEEKDAYS[d.weekday()], "name", source=f"'{date_ref}' on the clock"))
            out.append(Record(rid("clock", date_ref, "days_from_today"), (d.date() - now.date()).days, "count", source="the clock"))
    return out


def _resolve_date(now: datetime, ref: str) -> datetime | None:
    r = (ref or "").strip().lower()
    if r in ("today", "now"):
        return now
    if r == "tomorrow":
        return now + timedelta(days=1)
    if r == "yesterday":
        return now - timedelta(days=1)
    for i, name in enumerate(cplan.WEEKDAYS):
        if r == name.lower() or r == name.lower()[:3]:
            ahead = (i - now.weekday()) % 7
            return now + timedelta(days=ahead)
    try:
        return datetime.strptime(r[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _population(age: int, sex: str) -> list[Record]:
    """A Zone 2 pace comparison with its derivation: ACSM-style VO2max
    percentiles for the age band (men; coarse, rounded), the velocity at
    VO2max from Daniels' running-economy equation, and Zone 2 taken as
    about 70% of it. Low confidence by construction: there is no
    published Zone 2 pace table; this is a derivation, said so."""
    bands = {(18, 29): (44, 50, 55, 59), (30, 39): (41, 47, 52, 56), (40, 49): (38, 44, 49, 53), (50, 59): (35, 40, 45, 49),
             (60, 90): (31, 36, 41, 45)}
    vo2 = next(v for (lo, hi), v in bands.items() if lo <= age <= hi)
    if sex.lower().startswith("f"):
        vo2 = tuple(v - 6 for v in vo2)
    src = (f"derived: ACSM-style VO2max percentiles (p50/p75/p90/p95) for {sex} {age}, Daniels' economy equation for the "
           "velocity at VO2max, Zone 2 as ~70% of it; low confidence, a derivation, not a published Zone 2 table")
    out: list[Record] = []
    for pct, v in zip((50, 75, 90, 95), vo2):
        # Daniels: VO2 = -4.60 + 0.182258 v + 0.000104 v^2 (v in m/min) → solve for v
        a, b, cc = 0.000104, 0.182258, -4.60 - v
        vmax = (-b + math.sqrt(b * b - 4 * a * cc)) / (2 * a)
        z2 = 0.70 * vmax                             # m/min
        s_per_mi = 1609.344 / z2 * 60.0
        out.append(Record(rid("pop", f"zone2_p{pct}", "pace_s_per_mi"), round(s_per_mi), "s/mi",
                          lo=round(s_per_mi * 0.94), hi=round(s_per_mi * 1.06), source=src))
        out.append(Record(rid("pop", f"vo2max_p{pct}"), v, "ml/kg/min", source=src))
    out.append(Record("pop:population", f"{sex}, {age // 10 * 10}s, recreationally active (ACSM-style norms)", "text", source=src))
    out.append(Record("pop:confidence", "low", "text", source=src))
    return out


def build_registry(ctx: dict, *, allow: list[str] | None = None) -> ToolRegistry:
    reg = ToolRegistry(allow=allow)

    @reg.tool("get_athlete", "The athlete file: his zones, cap, LT1, reference heart rate, resting and max, units, body, with provenance per field. Never infer a zone from a pace.", args=NoArgs)
    def get_athlete(a: NoArgs, c):
        return _add(c, _athlete_records(c.get("athlete") or {}))

    @reg.tool("get_goal", "The goal in force: target pace, band, date, test date and route.", args=NoArgs)
    def get_goal(a: NoArgs, c):
        return _add(c, _goal_records(c.get("goals") or {}))

    @reg.tool("get_plan", "The plan in force: the next planned session (type, date, minutes, band, cap, strides, route, time), this week's sessions and the block week.", args=ClockArgs)
    def get_plan(a: ClockArgs, c):
        plan = c.get("plan") or {}
        recs: list[Record] = []
        nxt = cplan.next_session(plan, c["now"])
        if nxt:
            recs += cplan.session_records(nxt, plan)
            recs.append(Record("plan:next:date", nxt.date, "local", source="the plan: the next planned session"))
        if a.date_ref:
            d = _resolve_date(c["now"], a.date_ref)
            s = cplan.session_on(plan, d.strftime("%Y-%m-%d")) if d else None
            if s:
                recs += cplan.session_records(s, plan)
            elif d:
                recs.append(Record(rid("plan", d.strftime("%Y-%m-%d"), "type"), "none", "name", source="the plan: nothing planned that day"))
        recs.append(Record("plan:name", plan.get("name") or "no plan on file", "text", source="the plan file"))
        return _add(c, recs)

    @reg.tool("get_session", "A session type's shape from the library: purpose, ramp, strides, cues. The athlete's numbers come from the plan and the athlete file, not from here.", args=SessionArgs)
    def get_session(a: SessionArgs, c):
        return _add(c, cplan.library_records(a.type))

    @reg.tool("list_runs", "The recent runs, newest first: date, type, grade, pace, average heart rate, each a record.", args=CompareArgs)
    def list_runs(a: CompareArgs, c):
        con = _con(c)
        now = c["now"]
        ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        recs: list[Record] = []
        for wid in list(reversed(ids))[: a.n]:
            an = _analysis_for(c, wid)
            if not an:
                continue
            rs = RecordSet.from_json(an.get("records") or [])
            for path in ("start_local", "session_type", "grade", "pace_s_per_mi", "hr_avg_moving", "distance_km", "moving_min",
                         "norm.pace_at_ref", "grade.borderline"):
                r = rs.get(rid("run", wid, path))
                if r:
                    recs.append(r)
        recs.append(Record("runs:count", len(ids), "count", source="primary runs in the store"))
        return _add(c, recs)

    @reg.tool("analyze_run", "Every record of one run's analysis: halves, ramp, block, time in zones, decoupling, drift, artifacts, sleep, conditions, type, grade, margins, normalization. 'last' is the most recent run.", args=RunRefArgs)
    def analyze_run(a: RunRefArgs, c):
        wid = _resolve_run(c, a.which, a.value)
        if not wid:
            return _add(c, [Record("run:none", True, "flag", source="no such run in the store")])
        an = _analysis_for(c, wid)
        if not an:
            return _add(c, [Record("run:none", True, "flag", source="the run could not be analyzed")])
        recs = _run_records(an)
        recs.append(Record(rid("run", wid, "notes"), "; ".join(an.get("notes") or []) or "none", "text", source="the engine's notes"))
        recs.append(Record(rid("run", wid, "borderline"), ", ".join(an.get("borderline") or []) or "none", "text",
                           source="verdicts within a bpm of their line"))
        return _add(c, recs)

    @reg.tool("compare_runs", "The last comparable runs (A–C grades) beside one run: date, grade, pace, heart rate, normalized pace with its range.", args=CompareArgs)
    def compare_runs(a: CompareArgs, c):
        con = _con(c)
        wid = _resolve_run(c, a.which, a.value)
        now = c["now"]
        ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        recs: list[Record] = []
        count = 0
        for other in reversed(ids):
            if other == wid:
                continue
            an = _analysis_for(c, other)
            if not an or not an.get("fitness_data_point"):
                continue
            rs = RecordSet.from_json(an.get("records") or [])
            for path in ("start_local", "grade", "session_type", "pace_s_per_mi", "hr_avg_moving", "norm.pace_at_ref",
                         "norm.pace_at_ref_hr_only", "sleep.night_before_h"):
                r = rs.get(rid("run", other, path))
                if r:
                    recs.append(r)
            count += 1
            if count >= a.n:
                break
        recs.append(Record("compare:count", count, "count", source="comparable runs found"))
        return _add(c, recs)

    @reg.tool("trend", "The rolling estimate of the normalized pace at the reference heart rate with its range and slope, and the forecast at the goal date with the chance of the target.", args=NoArgs)
    def trend(a: NoArgs, c):
        con = _con(c)
        now = c["now"]
        _ensure_analyses(c)
        est = cengine.trend_now(con, now=now)
        src = f"the rolling estimate: {est.method}"
        recs = [Record(rid("trend", est.as_of, "pace_s_per_mi"), est.pace_s_per_mi, "s/mi", lo=est.lo, hi=est.hi, source=src),
                Record(rid("trend", est.as_of, "n_runs"), est.n_runs, "count", source=src),
                Record(rid("trend", est.as_of, "method"), est.method, "text", source="the model")]
        if est.slope_per_week is not None:
            recs.append(Record(rid("trend", est.as_of, "slope_s_per_mi_per_week"), est.slope_per_week, "s/mi/wk", lo=est.slope_lo, hi=est.slope_hi,
                               source="the line's slope in seconds per mile per week (negative = faster)"))
        goal = (c.get("goals") or {}).get("primary") or {}
        if goal.get("date") and est.pace_s_per_mi:
            target = datetime.strptime(goal["date"], "%Y-%m-%d")
            fc = cmodel.forecast(est, target, now, target_pace_s=goal.get("pace_s_per_mi"))
            p = rid("forecast", goal["date"])
            recs.append(Record(f"{p}:pace_s_per_mi", fc["predicted"], "s/mi", lo=fc["lo"], hi=fc["hi"],
                               source="the line carried to the goal date (an early estimate: no track record yet)"))
            recs.append(Record(f"{p}:weeks", fc["weeks"], "count", source="weeks to the goal date"))
            if fc.get("p_target") is not None:
                recs.append(Record(f"{p}:p_target", round(fc["p_target"] * 100), "%", source="the chance of the target pace under a normal approximation; an early estimate"))
        c["evidence"].add(recs)
        return {"records": [r.as_json() for r in recs]}

    @reg.tool("prescribe", "The model's expected pace range at a target band for a session, and the distance range for its minutes (E3). Use the plan's band and cap, or the athlete file's; without conditions the range is HR-only and says so.", args=PrescribeArgs)
    def prescribe(a: PrescribeArgs, c):
        con = _con(c)
        now = c["now"]
        plan = c.get("plan") or {}
        zones = c.get("zones") or {}
        s = cplan.session_on(plan, a.date) if a.date else None
        hr_lo = a.hr_lo if a.hr_lo is not None else (s.hr_lo if s and s.hr_lo is not None else zones.get("sweet_lo") or zones.get("zone2_lo"))
        hr_hi = a.hr_hi if a.hr_hi is not None else (s.hr_hi if s and s.hr_hi is not None else zones.get("sweet_hi") or zones.get("zone2_hi"))
        minutes = a.minutes if a.minutes is not None else (s.minutes if s else 60.0)
        label = a.date or a.type or "next"
        if hr_lo is None or hr_hi is None:
            return _add(c, [Record(rid("rx", label, "basis"), "no band: the athlete file has no zones", "text", source="the model")])
        _ensure_analyses(c)
        est = cengine.trend_now(con, now=now)
        prior = c.get("slope_prior")
        slope = cengine.slope_from_store(con, prior=prior)
        cond = c.get("forecast_conditions") if a.with_conditions else None
        recs = cplan.prescribe(estimate=est, slope=slope, ref_hr=zones.get("reference"), target_lo=float(hr_lo), target_hi=float(hr_hi),
                               minutes=float(minutes), conditions=cond, label=label, zones=zones)
        if s and s.cap is not None:
            recs.append(Record(rid("rx", label, "cap"), float(s.cap), "bpm", source="the plan's cap"))
        elif zones.get("cap") is not None:
            recs.append(Record(rid("rx", label, "cap"), float(zones["cap"]), "bpm", source="the athlete file's cap"))
        return _add(c, recs)

    @reg.tool("clock", "Now, today, the weekday, days since the last run, the block week, and a date reference ('tomorrow', 'Sunday', a date) resolved to a date and weekday.", args=ClockArgs)
    def clock(a: ClockArgs, c):
        return _add(c, _clock_records(c, a.date_ref))

    @reg.tool("weather_at", "The forecast at a route's point for a day and hour: temperature, dew point, apparent temperature, cloud, the heat sum. Nothing without a route point on file.", args=WeatherArgs,
              kind="network", cacheable=True)
    def weather_at(a: WeatherArgs, c):
        routes = (c.get("routes") or {}).get("routes") or {}
        key = _route_key(routes, a.route or "")
        entry = routes.get(key) if key else None
        point = tuple(entry["point"]) if entry and entry.get("point") else None
        p = rid("wx", a.date, f"{a.hour:02d}")
        if point is None:
            return _add(c, [Record(f"{p}:available", False, "flag", source="no route point on file: the weather is looked up at the route's own point only")])
        when = datetime.strptime(a.date, "%Y-%m-%d").replace(hour=a.hour)
        cond = conditions_for_run(start_local=when, point=point, meta={}, now=c["now"], http=c.get("http"))
        js = cond.as_json()
        recs = []
        for k in ("temperature_f", "dew_point_f", "apparent_f", "humidity_pct", "wind_mph", "cloud_pct"):
            f = js.get(k) or {}
            if f.get("value") is not None:
                unit = {"temperature_f": "°F", "dew_point_f": "°F", "apparent_f": "°F", "humidity_pct": "%", "wind_mph": "mph", "cloud_pct": "%"}[k]
                recs.append(Record(f"{p}:{k}", f["value"], unit, source=f.get("source", "")))
        if cond.heat_sum is not None:
            recs.append(Record(f"{p}:heat_sum", cond.heat_sum, "°F+°F", source="air temperature + dew point (the heat heuristic)"))
        recs.append(Record(f"{p}:available", bool(recs), "flag", source="the forecast"))
        c["forecast_conditions"] = {"apparent_f": (js.get("apparent_f") or {}).get("value"), "dew_point_f": (js.get("dew_point_f") or {}).get("value"),
                                    "sun": ((js.get("cloud_pct") or {}).get("value") or 100) < 30 and bool(entry.get("exposed"))}
        return _add(c, recs)

    @reg.tool("route_info", "A route's attributes from the library: flat, shaded or exposed, benchmark, stops, hazards.", args=RouteArgs)
    def route_info(a: RouteArgs, c):
        routes = (c.get("routes") or {}).get("routes") or {}
        key = _route_key(routes, a.name)
        if not key:
            return _add(c, [Record(rid("route", a.name.lower().replace(" ", "_"), "known"), False, "flag", source="the route library has no such route; it is learned on the first run with a trace")])
        e = routes[key]
        p = rid("route", key)
        recs = [Record(f"{p}:name", e.get("name", key), "name", source="the route library"),
                Record(f"{p}:known", True, "flag", source="the route library")]
        for k, unit in (("flat", "flag"), ("benchmark", "flag"), ("exposed", "flag"), ("shade", "text"), ("surface", "text"),
                        ("stops", "text"), ("hazards", "text"), ("notes", "text"), ("gain_m", "m")):
            if e.get(k) is not None:
                recs.append(Record(f"{p}:{k}", e[k], unit, source="the route library"))
        recs.append(Record(f"{p}:has_point", bool(e.get("point")), "flag", source="whether a weather point is on file"))
        return _add(c, recs)

    @reg.tool("week_review", "This week's planned sessions against the store (done, modified, missed, pending), each with its grade and pace; the trend; the goal forecast; next week's first session.", args=NoArgs)
    def week_review(a: NoArgs, c):
        from agents.kipchoge import pipeline
        before = set(c["evidence"].ids())
        ev, label = pipeline.review_records(c, c["now"])
        recs = [ev.get(i) for i in ev.ids() if i not in before]
        return {"records": [r.as_json() for r in recs if r is not None], "week": label or "no plan covers this week"}

    @reg.tool("freshness", "When the store last heard from the Watch (vitals), the phone's workout export and the sleep export.", args=NoArgs)
    def freshness(a: NoArgs, c):
        con = _con(c)
        recs = []
        for label, sql in (("last_vitals_at", "SELECT MAX(timestamp) FROM raw_vitals"),
                           ("last_workout_at", "SELECT MAX(start) FROM workouts_hk"),
                           ("last_sleep_at", "SELECT MAX(sleep_end) FROM sleep_sessions")):
            try:
                v = con.execute(sql).fetchone()[0]
            except Exception:  # noqa: BLE001
                v = None
            recs.append(Record(rid("fresh", label), (v or "never")[:16], "local", source="the store"))
        return _add(c, recs)

    @reg.tool("population_estimate", "A population comparison for Zone 2 pace: percentiles for his age and sex with the derivation and its low confidence; one population, one metric.", args=PopulationArgs)
    def population_estimate(a: PopulationArgs, c):
        return _add(c, _population(a.age, a.sex))

    @reg.tool("strides_how", "How to run strides: effort, shape, form cues, recovery, and why they serve Zone 2 economy.", args=NoArgs)
    def strides_how(a: NoArgs, c):
        return _add(c, cplan.strides_records() + cplan.library_records("z2_strides"))

    return reg


def _route_key(routes: dict, name: str) -> str | None:
    n = (name or "").strip().lower()
    if not n:
        return None
    for key, e in routes.items():
        names = [key.lower(), str(e.get("name", "")).lower()] + [str(x).lower() for x in (e.get("aliases") or [])]
        if n in names or any(n and (n in x or x in n) for x in names if x):
            return key
    return None
