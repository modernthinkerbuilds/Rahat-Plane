"""agents.kipchoge.pipeline — the runs no message started (PRD v0.3
§12.1, §10.5; ADR-018 A6 addition 3).

  * a workout lands → the analysis: provisional from the Watch's minute
    data when the running series shows a run before the export names
    it, final when the workout file is in; at most one message per run
    per stage, keyed on the workout UUID (HAE re-posts recent workouts
    on every sync), and the final only when it materially differs from
    the provisional (the grade, the pace by more than ten seconds a
    mile, a half by more than a bpm and a half, or a distance the
    provisional did not have);
  * the evening before a planned session (20:00): the next-run card;
  * Sunday 19:00: the weekly review (done vs planned, the trend, the
    forecast, next week's first session).

Each message is the harness's (`run_pipeline`: the same eight steps on
a frame built here, the brake, the ledger, Record, the shadow judge),
or, with the model off or down, the floor template from records. Every
number is a record either way. Quiet hours (22:30–06:30) hold a message
until morning.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable

from agents.kipchoge import engine as cengine
from agents.kipchoge import ingest
from agents.kipchoge import plan as cplan
from agents.kipchoge import render as krender
from agents.kipchoge import shapes
from agents.kipchoge import state
from agents.kipchoge import tools as ktools
from agents.kipchoge.evidence import Evidence
from agents.kipchoge.records import ENGINE_VERSION, Record, RecordSet, rid

AGENT = "kipchoge"
CARD_HOUR = 20
WEEKEND_CARD_DAYS = (4, 5)                       # Friday and Saturday evenings (owner, 2026-11-01)
NO_SESSION_LINE = ("Running tomorrow? Nothing's on the plan this weekend. Tell me when you'll go and I'll set "
                   "the card, or /next.")
REVIEW_WEEKDAY, REVIEW_HOUR = 6, 19           # Sunday 19:00
QUIET = ("22:30", "06:30")
MATERIAL_PACE_S = 10.0
MATERIAL_HR_BPM = 1.5
PROVISIONAL_MIN_MINUTES = 20


# ── bookkeeping: one message per run per stage ────────────────────────
def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS kipchoge_sent (
        key TEXT PRIMARY KEY, kind TEXT, stage TEXT, trace_id TEXT, text_sha TEXT, at TEXT, json TEXT)""")
    con.commit()


def already_sent(con: sqlite3.Connection, key: str) -> dict | None:
    ensure_schema(con)
    row = con.execute("SELECT kind, stage, trace_id, text_sha, at, json FROM kipchoge_sent WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return {"kind": row[0], "stage": row[1], "trace_id": row[2], "text_sha": row[3], "at": row[4], "json": json.loads(row[5] or "{}")}


def mark_sent(con: sqlite3.Connection, key: str, *, kind: str, stage: str, trace_id: str, text: str, now: datetime, extra: dict | None = None) -> None:
    ensure_schema(con)
    con.execute("INSERT OR REPLACE INTO kipchoge_sent (key, kind, stage, trace_id, text_sha, at, json) VALUES (?,?,?,?,?,?,?)",
                (key, kind, stage, trace_id, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], now.strftime("%Y-%m-%d %H:%M:%S"),
                 json.dumps(extra or {}, ensure_ascii=False)))
    con.commit()


def in_quiet_hours(now: datetime) -> bool:
    hm = now.strftime("%H:%M")
    return hm >= QUIET[0] or hm < QUIET[1]


# ── the evidence for a card, an analysis, a review ────────────────────
def _ctx_for(tenant: Any, now: datetime, *, db_path: str | None, chat_id: str = "pipeline") -> dict:
    return tenant.build_ctx(chat_id, now, "", db_path=db_path, cassette_dir=None, trace_id="pipeline")


def _run_tool(ctx: dict, name: str, args: dict) -> None:
    reg = ktools.build_registry(ctx)
    reg.execute(name, args, ctx)


def card_records(ctx: dict, now: datetime) -> tuple[Evidence, cplan.Session | None]:
    """The records a card needs: the next planned session, the model's
    range at its band, the clock, the library, the conditions when the
    route has a point, last night's sleep."""
    plan = ctx.get("plan") or {}
    nxt = cplan.next_session(plan, now)
    if nxt is None:
        return ctx["evidence"], None
    _run_tool(ctx, "get_plan", {"date_ref": nxt.date})
    _run_tool(ctx, "clock", {"date_ref": nxt.date})
    _run_tool(ctx, "get_session", {"type": nxt.type})
    if nxt.route:
        _run_tool(ctx, "route_info", {"name": nxt.route})
        hour = int((nxt.time or "07:30").split(":")[0])
        _run_tool(ctx, "weather_at", {"date": nxt.date, "hour": hour, "route": nxt.route})
    _run_tool(ctx, "prescribe", {"date": nxt.date, "with_conditions": True})
    try:
        from bridges.healthkit import sleep as hk_sleep
        night = hk_sleep.night_before(ctx["con"], now)
        if night.get("hours"):
            ctx["evidence"].add([Record("sleep:night_before_h", night["hours"], "h", source=f"{night['method']}, the night before {now.strftime('%Y-%m-%d')}")])
    except Exception:  # noqa: BLE001
        pass
    return ctx["evidence"], nxt


def card_text(ctx: dict, *, now: datetime, model: bool = False, tenant: Any = None, db_path: str | None = None) -> str | None:
    """The next-run card: the floor template, or the harness when `model`."""
    ev, nxt = card_records(ctx, now)
    if nxt is None:
        return None
    if not model:
        return krender.card_floor(ev, date_label=nxt.date, units=krender._units(ctx))
    from core.harness import run as hrun
    frame = shapes.Frame(intent="next_run", date_ref=nxt.date, session_ref=nxt.type, route_ref=nxt.route)
    res = hrun.run_pipeline(tenant, f"the next-run card for {nxt.date} ({nxt.type})", frame=frame, chat_id="pipeline", now=now,
                            db_path=db_path, serve_mode="shadow", prefetch=card_prefetch(nxt))
    return res.text


def card_prefetch(nxt: cplan.Session) -> list[tuple[str, dict]]:
    """The tool calls a card needs, read before the model's first turn (the same ones `card_records` makes)."""
    calls: list[tuple[str, dict]] = [("get_plan", {"date_ref": nxt.date}), ("clock", {"date_ref": nxt.date}),
                                     ("get_session", {"type": nxt.type}), ("get_athlete", {})]
    if nxt.route:
        hour = int((nxt.time or "07:30").split(":")[0])
        calls += [("route_info", {"name": nxt.route}), ("weather_at", {"date": nxt.date, "hour": hour, "route": nxt.route})]
    calls.append(("prescribe", {"date": nxt.date, "with_conditions": True}))
    return calls


def analysis_records(ctx: dict, wid: str, now: datetime) -> Evidence:
    _run_tool(ctx, "analyze_run", {"which": "uuid", "value": wid})
    _run_tool(ctx, "compare_runs", {"which": "uuid", "value": wid, "n": 5})
    _run_tool(ctx, "trend", {})
    nxt = cplan.next_session(ctx.get("plan") or {}, now)
    if nxt:
        _run_tool(ctx, "get_plan", {"date_ref": nxt.date})
    return ctx["evidence"]


def analysis_text(ctx: dict, wid: str, *, now: datetime, model: bool = False, tenant: Any = None, db_path: str | None = None,
                  provisional: bool = False) -> str:
    ev = analysis_records(ctx, wid, now)
    if not model:
        text = krender.analysis_floor(ev, wid, units=krender._units(ctx), zones=ctx.get("zones"))
        if provisional:
            text = "Provisional (the Watch's minute data; the workout file hasn't landed):\n" + text
        return text
    from core.harness import run as hrun
    frame = shapes.Frame(intent="analyze", run_ref=shapes.RunRef(which="uuid", value=wid), wants_thorough=True)
    event = f"the {'provisional ' if provisional else ''}analysis of run {wid}"
    res = hrun.run_pipeline(tenant, event, frame=frame, chat_id="pipeline", now=now, db_path=db_path, serve_mode="shadow",
                            prefetch=analysis_prefetch(ctx, wid, now))
    return res.text


def analysis_prefetch(ctx: dict, wid: str, now: datetime) -> list[tuple[str, dict]]:
    """The tool calls an analysis needs, read before the model's first turn (the same ones `analysis_records` makes)."""
    calls: list[tuple[str, dict]] = [("analyze_run", {"which": "uuid", "value": wid}), ("compare_runs", {"which": "uuid", "value": wid, "n": 5}),
                                     ("trend", {}), ("get_athlete", {})]
    nxt = cplan.next_session(ctx.get("plan") or {}, now)
    if nxt:
        calls.append(("get_plan", {"date_ref": nxt.date}))
    return calls


def last_analysis_text(ctx: dict, *, now: datetime, model: bool = False, tenant: Any = None, db_path: str | None = None) -> str | None:
    con = ctx["con"]
    ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
    if not ids:
        return None
    return analysis_text(ctx, ids[-1], now=now, model=model, tenant=tenant, db_path=db_path)


# ── a workout lands ───────────────────────────────────────────────────
def _key_numbers(analysis: dict) -> dict:
    h = analysis.get("halves", {}).get("run") or [{}, {}]
    recs = {r["id"].split(":", 2)[-1]: r.get("value") for r in analysis.get("records", [])}
    return {"grade": analysis.get("grade"), "pace": recs.get("pace_s_per_mi"), "h1": h[0].get("hr_avg"), "h2": h[1].get("hr_avg"),
            "distance": recs.get("distance_km")}


def materially_differs(prov: dict | None, final: dict) -> tuple[bool, list[str]]:
    """The PRD's rule for a second message about one run."""
    if not prov:
        return True, ["no provisional message was sent"]
    a, b = _key_numbers(prov), _key_numbers(final)
    why: list[str] = []
    if a["grade"] != b["grade"]:
        why.append(f"grade {a['grade']} → {b['grade']}")
    if a["pace"] and b["pace"] and abs(a["pace"] - b["pace"]) > MATERIAL_PACE_S:
        why.append("the pace moved by more than ten seconds a mile")
    if not a["pace"] and b["pace"]:
        why.append("the final has a pace; the provisional had none")
    for k in ("h1", "h2"):
        if a[k] is not None and b[k] is not None and abs(a[k] - b[k]) > MATERIAL_HR_BPM:
            why.append(f"a half moved by more than {MATERIAL_HR_BPM} bpm")
            break
    if not a["distance"] and b["distance"]:
        why.append("the final has a distance")
    return bool(why), why


def on_workout_landed(tenant: Any, workout_id: str, *, now: datetime, send: Callable[[str], Any] | None, db_path: str | None = None,
                      model: bool = False, chat_id: str = "pipeline") -> dict:
    """The final analysis of a landed run: analyzed, rendered, sent once.
    Returns what happened: {sent, stage, why, text}."""
    ctx = _ctx_for(tenant, now, db_path=db_path, chat_id=chat_id)
    con = ctx["con"]
    try:
        key = f"{workout_id}:final"
        prior = already_sent(con, key)
        if prior:
            return {"sent": False, "stage": "final", "why": "already sent", "text": None, "trace_id": prior["trace_id"]}
        cengine.ensure_analyzed(con, now=now, http=ctx.get("http"), zones=ctx.get("zones"), prior=ctx.get("slope_prior"),
                                library=ctx.get("routes"))
        analysis = state.load_analysis(con, workout_id, ENGINE_VERSION) or cengine.analyze_workout(
            con, workout_id, now=now, http=ctx.get("http"), zones=ctx.get("zones"), prior=ctx.get("slope_prior"),
            library=ctx.get("routes"), save=True)
        if analysis is None:
            return {"sent": False, "stage": "final", "why": "no such run", "text": None}
        prov = already_sent(con, f"{workout_id}:provisional")
        differs, why = materially_differs((prov or {}).get("json", {}).get("analysis") if prov else None, analysis)
        if prov and not differs:
            mark_sent(con, key, kind="analysis", stage="final", trace_id="", text="", now=now, extra={"skipped": "not materially different"})
            return {"sent": False, "stage": "final", "why": "the final does not materially differ from the provisional", "text": None}
        text = analysis_text(ctx, workout_id, now=now, model=model, tenant=tenant, db_path=db_path)
        if in_quiet_hours(now):
            return {"sent": False, "stage": "final", "why": "quiet hours", "text": text, "held": True}
        if send is not None:
            send(text)
        mark_sent(con, key, kind="analysis", stage="final", trace_id="", text=text, now=now, extra={"analysis": _slim(analysis), "why": why})
        return {"sent": send is not None, "stage": "final", "why": why, "text": text}
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def on_provisional_run(tenant: Any, session: dict, *, now: datetime, send: Callable[[str], Any] | None, db_path: str | None = None,
                       model: bool = False, chat_id: str = "pipeline") -> dict:
    """A run the minute data shows before the workout file lands
    (Huberman's `sessions_from_vitals` dict): heart rate and time only,
    sent once per start time."""
    ctx = _ctx_for(tenant, now, db_path=db_path, chat_id=chat_id)
    con = ctx["con"]
    try:
        run = ingest.provisional_run(con, session)
        if run is None or run.duration_s < PROVISIONAL_MIN_MINUTES * 60:
            return {"sent": False, "stage": "provisional", "why": "too short or unreadable", "text": None}
        key = f"{run.workout_id}:provisional"
        if already_sent(con, key):
            return {"sent": False, "stage": "provisional", "why": "already sent", "text": None}
        from agents.kipchoge import analyze as ca
        analysis = ca.analyze(run, zones=ctx.get("zones") or {}, now=now)
        ctx["evidence"].add(RecordSet.from_json(analysis.get("records") or []))
        text = krender.analysis_floor(ctx["evidence"], run.workout_id, units=krender._units(ctx)) if not model else None
        if model:
            from core.harness import run as hrun
            frame = shapes.Frame(intent="analyze", run_ref=shapes.RunRef(which="uuid", value=run.workout_id))
            text = hrun.run_pipeline(tenant, f"the provisional analysis of {run.workout_id}", frame=frame, chat_id=chat_id, now=now,
                                     db_path=db_path, serve_mode="shadow",
                                     prefetch=[("analyze_run", {"which": "uuid", "value": run.workout_id}), ("get_athlete", {})]).text
        text = "Provisional (the Watch's minute data; the workout file hasn't landed):\n" + (text or "")
        if in_quiet_hours(now):
            return {"sent": False, "stage": "provisional", "why": "quiet hours", "text": text, "held": True}
        if send is not None:
            send(text)
        mark_sent(con, key, kind="analysis", stage="provisional", trace_id="", text=text, now=now, extra={"analysis": _slim(analysis)})
        # the final's key is the workout uuid, unknown here; the final looks the provisional up by the start time
        mark_sent(con, f"start:{run.start_utc}:provisional", kind="link", stage="provisional", trace_id="", text="", now=now,
                  extra={"analysis": _slim(analysis)})
        return {"sent": send is not None, "stage": "provisional", "why": "", "text": text}
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def _slim(analysis: dict) -> dict:
    keep = {k: analysis.get(k) for k in ("workout_id", "grade", "session_type", "halves")}
    keep["records"] = [r for r in analysis.get("records", []) if r["id"].endswith((":pace_s_per_mi", ":distance_km"))]
    return keep


# ── the ticks ─────────────────────────────────────────────────────────
def tick(tenant: Any, *, now: datetime, send: Callable[[str], Any] | None, db_path: str | None = None, model: bool = False) -> list[dict]:
    """What the minute tick sends: the card the evening before a planned
    session (once), the review on Sunday evening (once a week)."""
    out: list[dict] = []
    ctx = _ctx_for(tenant, now, db_path=db_path)
    con = ctx["con"]
    try:
        plan = ctx.get("plan") or {}
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        s = cplan.session_on(plan, tomorrow)
        if s and now.hour == CARD_HOUR and not already_sent(con, f"card:{s.date}"):
            text = card_text(ctx, now=now, model=model, tenant=tenant, db_path=db_path)
            if text:
                text += "\n\nStill on?"
                if send is not None:
                    send(text)
                mark_sent(con, f"card:{s.date}", kind="card", stage="evening", trace_id="", text=text, now=now)
                out.append({"kind": "card", "date": s.date, "text": text})
        if now.weekday() == REVIEW_WEEKDAY and now.hour == REVIEW_HOUR:
            week_label = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            if not already_sent(con, f"review:{week_label}"):
                text = review_text(ctx, now=now, model=model, tenant=tenant, db_path=db_path)
                if text:
                    if send is not None:
                        send(text)
                    mark_sent(con, f"review:{week_label}", kind="review", stage="sunday", trace_id="", text=text, now=now)
                    out.append({"kind": "review", "week": week_label, "text": text})
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def weekend_card(tenant: Any, *, now: datetime, send: Callable[[str], Any] | None, db_path: str | None = None) -> list[dict]:
    """The check-in the owner chose (2026-11-01): "a notification once
    Friday night and once Saturday night ... I might run on Saturdays or
    I might run on Sundays", on demand otherwise. At CARD_HOUR on those
    evenings: the next planned session's card from the templates (never
    the model), or one line when nothing is planned. Once an evening."""
    if now.weekday() not in WEEKEND_CARD_DAYS or now.hour != CARD_HOUR:
        return []
    ctx = _ctx_for(tenant, now, db_path=db_path)
    con = ctx["con"]
    try:
        key = f"weekend-card:{now.strftime('%Y-%m-%d')}"
        if already_sent(con, key):
            return []
        text = card_text(ctx, now=now, model=False, tenant=tenant, db_path=db_path)
        text = (text + "\n\nStill on?") if text else NO_SESSION_LINE
        if send is not None:
            send(text)
        mark_sent(con, key, kind="card", stage="weekend", trace_id="", text=text, now=now)
        return [{"kind": "card", "date": now.strftime("%Y-%m-%d"), "text": text}]
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


# ── the weekly review ─────────────────────────────────────────────────
def grade_week(con: sqlite3.Connection, plan: dict, week_n: int, *, now: datetime, tolerance_days: int = 1,
               zones: dict | None = None, prior: tuple[float, float] | None = None, library: dict | None = None,
               http=None) -> list[dict]:
    """Each planned session of a week against the store: done (a primary
    run within a day of it), modified (a run of another type or length),
    missed; written to session_log. The run's grade travels with it,
    from the same analyses every other path reads (ensure_analyzed)."""
    state.ensure_schema(con)
    cengine.ensure_analyzed(con, now=now, http=http, zones=zones, prior=prior, library=library)
    out: list[dict] = []
    weeks = {w.get("n"): w for w in plan.get("weeks") or []}
    w = weeks.get(week_n)
    if not w:
        return out
    runs = ingest.runs_in(con, (datetime.strptime(w["start"], "%Y-%m-%d") - timedelta(days=tolerance_days)).strftime("%Y-%m-%d"),
                          (datetime.strptime(w["end"], "%Y-%m-%d") + timedelta(days=tolerance_days)).strftime("%Y-%m-%d"))
    used: set[str] = set()
    for s in cplan.sessions({"weeks": [w]}):
        match = None
        for wid in runs:
            if wid in used:
                continue
            row = ingest.summary_row(con, wid)
            d = ingest._dt(row["start"]) if row else None
            if d and abs((d.date() - datetime.strptime(s.date, "%Y-%m-%d").date()).days) <= tolerance_days:
                match = (wid, row)
                break
        if match is None:
            status, wid, grade, note = ("pending" if s.date > now.strftime("%Y-%m-%d") else "missed"), None, None, ""
        else:
            wid, row = match
            used.add(wid)
            an = state.load_analysis(con, wid, ENGINE_VERSION) or cengine.analyze_workout(
                con, wid, now=now, zones=zones, prior=prior, library=library, http=http, save=True) or {}
            grade = an.get("grade")
            mins = float(row.get("duration_s") or 0) / 60.0
            modified = abs(mins - s.minutes) > max(10.0, 0.2 * s.minutes) or (an.get("session_type") in ("tempo", "hills") and s.type.startswith("z2"))
            status = "modified" if modified else "done"
            note = f"{mins:.0f} min as {an.get('session_type')}" if modified else ""
        con.execute("INSERT OR REPLACE INTO session_log (planned_id, workout_id, status, note, at) VALUES (?,?,?,?,?)",
                    (s.id, wid or "", status, note, now.strftime("%Y-%m-%d %H:%M:%S")))
        out.append({"planned_id": s.id, "date": s.date, "type": s.type, "status": status, "workout_id": wid, "grade": grade, "note": note})
    con.commit()
    return out


def review_records(ctx: dict, now: datetime) -> tuple[Evidence, str | None]:
    plan = ctx.get("plan") or {}
    week_n, total = cplan.week_of(plan, now.strftime("%Y-%m-%d"))
    if week_n is None:
        return ctx["evidence"], None
    label = f"w{week_n}"
    rows = grade_week(ctx["con"], plan, week_n, now=now, zones=ctx.get("zones"), prior=ctx.get("slope_prior"),
                      library=ctx.get("routes"), http=ctx.get("http"))
    ev = ctx["evidence"]
    src = "the plan against the store (session_log)"
    ev.add([Record(rid("review", label, "label"), f"week {week_n} of {total}", "text", source=src),
            Record(rid("review", label, "planned"), len(rows), "count", source=src),
            Record(rid("review", label, "done"), sum(1 for r in rows if r["status"] == "done"), "count", source=src),
            Record(rid("review", label, "missed"), sum(1 for r in rows if r["status"] == "missed"), "count", source=src)])
    for i, r in enumerate(rows):
        base = rid("review", label, "session", r["planned_id"])
        ev.add([Record(f"{base}:weekday", cplan.WEEKDAYS[datetime.strptime(r["date"], "%Y-%m-%d").weekday()], "name", source=src),
                Record(f"{base}:type", r["type"], "name", source=src),
                Record(f"{base}:label", cplan.LABELS.get(r["type"], r["type"].replace("_", " ")), "name", source=src),
                Record(f"{base}:order", i, "count", source=src),
                Record(f"{base}:status", r["status"] + (f" ({r['note']})" if r["note"] else ""), "text", source=src)])
        if r.get("workout_id"):
            an = state.load_analysis(ctx["con"], r["workout_id"], ENGINE_VERSION) or {}
            rs = RecordSet.from_json(an.get("records") or [])
            g, p = rs.get(rid("run", r["workout_id"], "grade")), rs.get(rid("run", r["workout_id"], "pace_s_per_mi"))
            if g:
                ev.add([Record(f"{base}:grade", g.value, "grade", source=g.source)])
            if p:
                ev.add([Record(f"{base}:pace_s_per_mi", p.value, "s/mi", source=p.source)])
    _run_tool(ctx, "trend", {})
    nxt = cplan.next_session(plan, now + timedelta(days=1))
    if nxt:
        _run_tool(ctx, "get_plan", {"date_ref": nxt.date})
    return ev, label


def review_text(ctx: dict, *, now: datetime, model: bool = False, tenant: Any = None, db_path: str | None = None) -> str | None:
    ev, label = review_records(ctx, now)
    if label is None:
        return None
    if not model:
        return krender.review_floor(ev, label, units=krender._units(ctx))
    from core.harness import run as hrun
    frame = shapes.Frame(intent="trend", date_ref=now.strftime("%Y-%m-%d"))
    res = hrun.run_pipeline(tenant, f"the weekly review for {label}", frame=frame, chat_id="pipeline", now=now, db_path=db_path,
                            serve_mode="shadow", prefetch=[("week_review", {}), ("get_athlete", {}), ("get_goal", {})],
                            ctx_extra={"weekly_review": True})
    return res.text
