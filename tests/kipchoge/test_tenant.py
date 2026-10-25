"""The coach as a harness tenant (ADR-018 A6; PRD v0.3 §12–13), on an
invented athlete, an invented plan and synthetic runs, with a fake wire
that answers each step by the schema it asks for. The checks are the
real ones; the renderer is the real one; no network, no model.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core import state_scopes as ss  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from agents.kipchoge import pipeline, state  # noqa: E402
from agents.kipchoge import tenant as kt  # noqa: E402
from agents.kipchoge.records import ENGINE_VERSION  # noqa: E402
from tests.kipchoge.fixtures import ZONES, load_fixture, sandbox_db, synthetic_run, write_private_twins  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 30)          # a Saturday; the plan's Sunday session is tomorrow
RUN_DAY = datetime(2026, 10, 18, 7, 30)


class Wire:
    """Answers by what the request asks for: the coach's frame schema
    (`intent`), the sanity schema, the tool loop, the answer schema
    (`bottom_line`), the judge schema."""

    def __init__(self, *, frame: dict, rounds: list[list[dict]], answers: list[dict], judge: dict | None = None,
                 errors: dict | None = None):
        self.frame, self.rounds, self.answers, self.judge = frame, rounds, list(answers), judge
        self.errors = errors or {}
        self.calls: list[str] = []

    def kind_of(self, req: dict) -> str:
        props = ((req.get("schema") or {}).get("properties") or {})
        if "intent" in props:
            return "understand"
        if "faithful" in props:
            return "sanity"
        if "verdicts" in props:
            return "judge"
        if "bottom_line" in props:
            return "answer"
        if req.get("tools"):
            return "execute"
        return "other"

    def __call__(self, req: dict) -> dict:
        kind = self.kind_of(req)
        self.calls.append(kind)
        if kind in self.errors:
            return {"error": self.errors[kind]}
        base = {"stop_reason": "stop", "citations": [], "search_queries": 0, "usage": {"in": 800, "out": 150, "thought": 0, "tool_prompt": 0},
                "error": None}
        if kind == "understand":
            t = json.dumps(self.frame)
            return base | {"text": t, "parts": [{"text": t}]}
        if kind == "sanity":
            t = json.dumps({"faithful": True, "problem": None})
            return base | {"text": t, "parts": [{"text": t}]}
        if kind == "execute":
            n_model = sum(1 for m in req["messages"] if m.get("role") == "model")
            calls = self.rounds[n_model] if n_model < len(self.rounds) else []
            if not calls:
                return base | {"text": "READY", "parts": [{"text": "READY"}]}
            return base | {"text": "", "parts": [{"function_call": c} for c in calls]}
        if kind == "answer":
            a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            t = json.dumps(a)
            return base | {"text": t, "parts": [{"text": t}]}
        if kind == "judge":
            t = json.dumps(self.judge or {"verdicts": []})
            return base | {"text": t, "parts": [{"text": t}]}
        return base | {"text": "{}", "parts": [{"text": "{}"}]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_KIPCHOGE_WX_DIR", str(tmp_path / "wx"))
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_KIPCHOGE", "0")
    monkeypatch.setenv("RAHAT_HARNESS_SANITY", "1")
    (tmp_path / "vault").mkdir()
    write_private_twins(tmp_path / "vault")
    db = tmp_path / "vault" / "health.db"
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    con = sandbox_db(db)
    state.ensure_schema(con)
    # three runs: a clean one (Oct 4), an easy one (Oct 11), the last one (Oct 18) with a drift over the cap
    load_fixture(con, synthetic_run("R-A", datetime(2026, 10, 4, 7, 30), 70, km=9.4, hr_first=142.0, hr_second=144.0))
    load_fixture(con, synthetic_run("R-B", datetime(2026, 10, 11, 18, 40), 35, km=4.4, hr_first=132.0, hr_second=134.0, route=False, series=False))
    load_fixture(con, synthetic_run("R-C", RUN_DAY, 75, km=10.2, hr_first=142.0, hr_second=150.0))
    con.close()
    kt.reset()

    def http(url, params):
        hours = [f"2026-10-{d:02d}T{h:02d}:00" for d in (24, 25, 26) for h in range(24)]
        n = len(hours)
        return {"hourly": {"time": hours, "temperature_2m": [52.0] * n, "relative_humidity_2m": [82.0] * n, "dew_point_2m": [47.0] * n,
                           "apparent_temperature": [50.0] * n, "wind_speed_10m": [3.0] * n, "cloud_cover": [20.0] * n, "uv_index": [1.0] * n}}
    tenant = kt.KipchogeTenant(http=http)
    return {"db": str(db), "tenant": tenant, "tmp": tmp_path}


def _wire(monkeypatch, w: Wire) -> Wire:
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    return w


CARD_FRAME = {"intent": "next_run", "date_ref": "tomorrow", "wants_thorough": True}
CARD_ROUNDS = [[{"name": "get_plan", "args": {"date_ref": "tomorrow"}}, {"name": "clock", "args": {"date_ref": "tomorrow"}},
                {"name": "get_session", "args": {"type": "z2_strides"}}, {"name": "get_athlete", "args": {}}],
               [{"name": "route_info", "args": {"name": "creek"}}, {"name": "weather_at", "args": {"date": "2026-10-25", "hour": 7, "route": "creek"}}],
               [{"name": "prescribe", "args": {"date": "2026-10-25", "with_conditions": True}}], []]
P = "plan:2026-10-25"
X = "rx:2026-10-25"
GOOD_CARD = {
    "kind": "card",
    "headline": f"{{{{r:clock:tomorrow:weekday}}}} {{{{r:{P}:time}}}} · {{{{r:{P}:route}}}} · Zone 2 + strides ({{{{r:{P}:week_of_block}}}})",
    "bottom_line": [f"Run {{{{r:{P}:minutes|int}}}}. Expect {{{{r:{X}:distance_km|range}}}}.",
                    f"HR: ramp to {{{{r:{P}:hr_lo|bare}}}}–{{{{r:{P}:hr_hi}}}} by minute {{{{r:lib:z2_strides:ramp_min|bare}}}}, hold it, cap {{{{r:{P}:cap}}}}.",
                    f"Pace will be ~{{{{r:{X}:pace_s_per_mi|range}}}}; don't chase it."],
    "sections": [{"title": "After", "lines": [f"Walk {{{{r:lib:z2_strides:walk_min_after|bare}}}} min, then {{{{r:{P}:strides|bare}}}} × {{{{r:lib:z2_strides:stride_s}}}} strides, full recovery."]},
                 {"title": "The one rule", "lines": [f"If HR passes {{{{r:{P}:cap}}}}, ease off until it's back in band. Never chase pace."]},
                 {"title": "Why", "lines": ["{{r:lib:z2_strides:purpose}}."]},
                 {"title": "Conditions", "lines": ["{{r:wx:2026-10-25:07:temperature_f}} at start, dew point {{r:wx:2026-10-25:07:dew_point_f|bare}}, shaded: good test weather."]}],
    "records_used": [f"{P}:minutes", f"{X}:pace_s_per_mi", f"{P}:cap"],
}


def test_a_next_run_card_runs_the_eight_steps_on_the_coach_s_shapes(world, monkeypatch):
    w = _wire(monkeypatch, Wire(frame=CARD_FRAME, rounds=CARD_ROUNDS, answers=[GOOD_CARD],
                                judge={"verdicts": [{"outcome": "V1", "verdict": "pass", "line": "direct", "evidence_ids": []}]}))
    res = hrun.run(world["tenant"], "what's my next run?", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.error is None and not res.floor_used, res.error
    assert res.frame.intent == "next_run" and res.control.verify.ok, res.control.verify.as_json()
    ids = res.contract.ids()
    assert {"E1", "E2", "E3", "E4", "E5", "A1", "B1", "S2"} <= set(ids)
    text = res.text
    assert "Sunday 07:30 · creek · Zone 2 + strides (week 1 of 5)" in text
    assert "Run 75 min. Expect " in text and " km (" in text and "/mi" in text, text
    assert f"ramp to {int(ZONES['sweet_lo'])}–{int(ZONES['sweet_hi'])} bpm" in text and f"cap {int(ZONES['cap'])} bpm" in text
    assert "6 × 20 s strides" in text and "52°F at start, dew point 47" in text
    assert "Sources:" in text and "records" in text
    assert w.calls[:2] == ["understand", "sanity"] and "judge" in w.calls
    assert res.tool_calls == 7 and res.cost_usd > 0
    last = ss.get("kipchoge", "c1", "last_answer", now=NOW, db_path=world["db"])
    assert last["kind"] == "card" and last["trace_id"] == res.trace_id


def test_a_digit_the_model_wrote_fails_e1_and_is_repaired(world, monkeypatch):
    bad = json.loads(json.dumps(GOOD_CARD))
    bad["bottom_line"][0] = "Run {{r:plan:2026-10-25:minutes|int}}. Expect about 10 km."
    w = _wire(monkeypatch, Wire(frame=CARD_FRAME, rounds=CARD_ROUNDS, answers=[bad, GOOD_CARD]))
    res = hrun.run(world["tenant"], "what's my next run?", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs == 1 and res.control.verify.ok
    assert w.calls.count("answer") == 2
    hint = json.dumps([m for m in res.exec.history if m.get("role") == "user"][-1])
    assert "E1" in hint and "digits written by the model" in hint
    assert "about 10 km" not in res.text


def test_a_run_s_pace_cited_as_the_target_fails_e3(world, monkeypatch):
    bad = json.loads(json.dumps(GOOD_CARD))
    bad["bottom_line"][2] = "Pace will be ~{{r:run:R-C:pace_s_per_mi}}; don't chase it."
    rounds = CARD_ROUNDS[:-1] + [[{"name": "analyze_run", "args": {"which": "last"}}], []]
    w = _wire(monkeypatch, Wire(frame=CARD_FRAME, rounds=rounds, answers=[bad, GOOD_CARD]))
    res = hrun.run(world["tenant"], "what's my next run?", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs == 1 and res.control.verify.ok
    hint = json.dumps([m for m in res.exec.history if m.get("role") == "user"][-1])
    assert "E3" in hint and "prescribe" in hint


ANALYSIS_FRAME = {"intent": "analyze", "run_ref": {"which": "last"}, "wants_thorough": True}
ANALYSIS_ROUNDS = [[{"name": "analyze_run", "args": {"which": "last"}}, {"name": "get_athlete", "args": {}}],
                   [{"name": "compare_runs", "args": {"which": "last", "n": 5}}, {"name": "trend", "args": {}}, {"name": "clock", "args": {}}],
                   [{"name": "get_plan", "args": {}}], []]


def _analysis_answer(wid: str = "R-C", *, normalized: bool = True) -> dict:
    r = lambda p: f"{{{{r:run:{wid}:{p}}}}}"  # noqa: E731
    trend_line = ("the rolling estimate {{r:trend:2026-10-24:pace_s_per_mi}} ({{r:trend:2026-10-24:pace_s_per_mi|range}}) "
                  "over {{r:trend:2026-10-24:n_runs}} runs.")
    if normalized:
        trend_line = f"Normalized {r('norm.pace_at_ref')} ({r('norm.pace_at_ref|range')}) at {r('norm.ref_hr')}; " + trend_line
    return {
        "kind": "analysis",
        "headline": f"A steady run that drifted over the cap in the second half: {r('session_type')}, grade {r('grade')}; "
                    f"{r('distance_km')} in {r('moving_min|int')} at {r('pace_s_per_mi')}, avg {r('hr_avg_moving')}, max {r('hr_max')}.",
        "sections": [
            {"title": "What the data shows", "lines": [f"Halves {r('run_halves.first.hr_avg')} → {r('run_halves.second.hr_avg')}.",
                                                       f"Ramp {r('ramp_min')} to the band; time in band {r('time_in_zone.in_band_pct')}.",
                                                       f"Decoupling {r('decoupling_pct')}; drift {r('drift_bpm|signed')}.",
                                                       f"Slept {r('sleep.night_before_h')}."]},
            {"title": "What went well", "lines": ["The first half sat in the band from the ramp on; the pace held when the heart rate rose."]},
            {"title": "What to fix", "lines": [f"The second half rode over the cap: ease off at {{{{r:athlete:hr:cap}}}} next time, the block is the measurement."]},
            {"title": "The trend", "lines": [trend_line]},
            {"title": "Next", "lines": ["{{r:plan:2026-10-25:weekday}}: {{r:plan:2026-10-25:type}}, {{r:plan:2026-10-25:minutes|int}} min, cap {{r:plan:2026-10-25:cap}}."]},
        ],
        "data_notes": [f"{r('artifacts')} artifacts excluded; {r('hr_gaps')} heart-rate gaps reported, not subtracted."],
        "next": "Hold the band when the second half gets hard; the drift is what we measure.",
        "records_used": [f"run:{wid}:grade", f"run:{wid}:session_type"],
    }


def test_an_analysis_covers_its_sections_with_every_number_from_a_record(world, monkeypatch):
    w = _wire(monkeypatch, Wire(frame=ANALYSIS_FRAME, rounds=ANALYSIS_ROUNDS, answers=[_analysis_answer()]))
    res = hrun.run(world["tenant"], "analyze my run, be thorough", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.error is None and res.control.verify.ok, res.control.verify.as_json()
    assert {"A2", "A4", "E6", "E8", "E1"} <= set(res.contract.ids())
    assert "grade C" in res.text and "Halves " in res.text and "/mi" in res.text and "Next: Hold the band" in res.text
    assert "Data notes:" in res.text and "The trend:" in res.text
    assert res.tool_calls == 6


def test_a_d_run_must_be_said_not_to_count(world, monkeypatch):
    # make the last run a run from the gun: over the cap from minute one
    con = sandbox_db(world["tmp"] / "vault" / "health.db")
    load_fixture(con, synthetic_run("R-D", datetime(2026, 10, 20, 7, 30), 60, km=8.0, hr_first=153.0, hr_second=154.0, ramp_min=1.0))
    con.close()
    ans = _analysis_answer("R-D", normalized=False)          # a D run has no normalized pace: citing one would fail E1
    ans["headline"] = ans["headline"].replace("drifted over the cap in the second half", "started over the cap")
    said = json.loads(json.dumps(ans))
    said["data_notes"].append("Grade D: this run does not count as a fitness data point.")
    w = _wire(monkeypatch, Wire(frame=ANALYSIS_FRAME, rounds=ANALYSIS_ROUNDS, answers=[ans, said]))
    res = hrun.run(world["tenant"], "analyze my run", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs == 1 and res.control.verify.ok
    assert "grade D" in res.text and "does not count" in res.text


HEALTH_FRAME = {"intent": "health", "symptoms": [], "claims": []}


def test_a_symptom_gets_the_clinician_line_even_when_the_reading_missed_it(world, monkeypatch):
    rounds = [[{"name": "get_athlete", "args": {}}, {"name": "freshness", "args": {}}], []]
    without = {"kind": "answer", "headline": "A sweaty evening run and a short rehydration window: a fluid and sodium debt is the likely cause.",
               "sections": [{"title": "What to do", "lines": ["A litre with sodium over the next hours; a quiet day."]}],
               "red_flag_line": False}
    with_line = json.loads(json.dumps(without))
    with_line["sections"].append({"title": "Red flags", "lines": ["A worst-ever headache, neck stiffness, vision changes, confusion or fever: see a clinician promptly."]})
    with_line["red_flag_line"] = True
    w = _wire(monkeypatch, Wire(frame=HEALTH_FRAME, rounds=rounds, answers=[without, with_line]))
    res = hrun.run(world["tenant"], "I woke up with a pretty bad headache after last night's run", chat_id="c1", now=NOW, db_path=world["db"],
                   judge_enabled=False)
    assert res.frame.symptoms == ["headache"], "the guardrail read the symptom in code"
    assert "S1" in res.contract.ids() and res.control.repairs == 1 and res.control.verify.ok
    assert "see a clinician promptly" in res.text


def test_the_floor_renders_the_card_the_analysis_and_the_review_from_records(world, monkeypatch):
    _wire(monkeypatch, Wire(frame=CARD_FRAME, rounds=[], answers=[GOOD_CARD], errors={"understand": "gemini-not-configured"}))
    res = hrun.run(world["tenant"], "what's my next run tomorrow?", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.floor_used and "Run 75 min" in res.text and f"cap {int(ZONES['cap'])} bpm" in res.text and "/mi" in res.text
    assert "Conditions: 52°F at start" in res.text
    res2 = hrun.run(world["tenant"], "analyze my run", chat_id="c1", now=NOW, db_path=world["db"])
    assert res2.floor_used and "grade C" in res2.text and "What the data shows:" in res2.text and "Next:" in res2.text
    # the templates carry no digit of their own: every number came from a record
    from agents.kipchoge import render as kr
    from core.harness import numbers as cnum
    ctx = pipeline._ctx_for(world["tenant"], NOW, db_path=world["db"])
    ev, nxt = pipeline.card_records(ctx, NOW)
    captured = {}
    real = cnum.substitute

    def spy(text, records, **kw):
        captured["t"] = text
        return real(text, records, **kw)
    monkeypatch.setattr(cnum, "substitute", spy)
    kr.card_floor(ev, date_label=nxt.date)
    assert cnum.stray_digits(captured["t"], ["Zone 2"]) == [], captured["t"]
    ctx["con"].close()


def test_the_pipelines_send_once_per_run_per_stage_and_respect_quiet_hours(world):
    sent: list[str] = []
    out = pipeline.on_workout_landed(world["tenant"], "R-C", now=NOW, send=sent.append, db_path=world["db"])
    assert out["sent"] and "grade C" in sent[0] and out["why"] == ["no provisional message was sent"]
    again = pipeline.on_workout_landed(world["tenant"], "R-C", now=NOW, send=sent.append, db_path=world["db"])
    assert not again["sent"] and again["why"] == "already sent" and len(sent) == 1
    held = pipeline.on_workout_landed(world["tenant"], "R-A", now=NOW.replace(hour=23), send=sent.append, db_path=world["db"])
    assert held.get("held") and not held["sent"] and len(sent) == 1
    # a provisional run from the minute data, then its final: the final goes out only when it materially differs
    session = {"name": "Outdoor Run", "start": "2026-10-11 18:40:00", "end": "2026-10-11 19:15:00", "minutes": 35, "kind": "run", "inferred": True}
    prov = pipeline.on_provisional_run(world["tenant"], session, now=datetime(2026, 10, 11, 19, 20), send=sent.append, db_path=world["db"])
    assert prov["sent"] and sent[-1].startswith("Provisional") and len(sent) == 2
    assert not pipeline.on_provisional_run(world["tenant"], session, now=datetime(2026, 10, 11, 19, 25), send=sent.append, db_path=world["db"])["sent"]
    differs, why = pipeline.materially_differs({"grade": "C", "halves": {"run": [{"hr_avg": 133.0}, {"hr_avg": 134.0}]}, "records": []},
                                               {"grade": "C", "halves": {"run": [{"hr_avg": 133.2}, {"hr_avg": 134.1}]}, "records": []})
    assert not differs
    differs2, why2 = pipeline.materially_differs({"grade": "C", "halves": {"run": [{}, {}]}, "records": []},
                                                 {"grade": "B", "halves": {"run": [{}, {}]}, "records": [{"id": "run:x:pace_s_per_mi", "value": 700}]})
    assert differs2 and "grade C → B" in why2


def test_the_ticks_send_the_card_the_evening_before_and_the_review_on_sunday(world):
    sent: list[str] = []
    evening = datetime(2026, 10, 24, 20, 5)                         # Saturday 20:05: Sunday's session is tomorrow
    out = pipeline.tick(world["tenant"], now=evening, send=sent.append, db_path=world["db"])
    assert [o["kind"] for o in out] == ["card"] and sent[0].endswith("Still on?") and "Run 75 min" in sent[0]
    assert pipeline.tick(world["tenant"], now=evening.replace(minute=30), send=sent.append, db_path=world["db"]) == []
    sunday = datetime(2026, 10, 25, 19, 2)
    out2 = pipeline.tick(world["tenant"], now=sunday, send=sent.append, db_path=world["db"])
    assert [o["kind"] for o in out2] == ["review"] and "Week review: week 1 of 5" in sent[-1]
    assert "Done " in sent[-1] and "planned" in sent[-1]
    assert pipeline.tick(world["tenant"], now=sunday.replace(minute=40), send=sent.append, db_path=world["db"]) == []


def test_week_one_is_graded_from_the_store(world):
    con = sandbox_db(world["tmp"] / "vault" / "health.db")
    plan = state.load_json(state.PLAN_FILE, {})
    before = {r["planned_id"]: r["status"] for r in pipeline.grade_week(con, plan, 1, now=NOW)}
    assert before == {"w1-wed": "missed", "w1-sun": "pending"}, "Wednesday went by without a run; Sunday is still to come"
    load_fixture(con, synthetic_run("R-E", datetime(2026, 10, 25, 7, 35), 76, km=10.0, hr_first=142.0, hr_second=144.0))
    load_fixture(con, synthetic_run("R-F", datetime(2026, 10, 28, 18, 50), 20, km=2.6, hr_first=130.0, hr_second=131.0, route=False))
    rows = pipeline.grade_week(con, plan, 1, now=datetime(2026, 10, 27, 9, 0))
    by = {r["planned_id"]: r for r in rows}
    assert by["w1-wed"]["status"] == "missed" and by["w1-sun"]["status"] == "done"
    assert by["w1-sun"]["workout_id"] == "R-E" and by["w1-sun"]["grade"] in ("A", "B")
    week2 = {r["planned_id"]: r for r in pipeline.grade_week(con, plan, 2, now=datetime(2026, 11, 2, 9, 0))}
    assert week2["w2-wed"]["status"] == "modified" and "20 min" in week2["w2-wed"]["note"], "a 20-minute jog for a 35-minute session"
    logged = con.execute("SELECT planned_id, status FROM session_log ORDER BY planned_id").fetchall()
    assert ("w1-sun", "done") in logged and ("w1-wed", "missed") in logged and ("w2-wed", "modified") in logged
    con.close()
