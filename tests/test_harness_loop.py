"""The harness loop end to end on a scripted wire (ADR-018 §2.2):
Understand → Contract → Execute → Verify → Judge (shadow) → Control →
Render → Record, over the synthetic knowledge base. No network; the
model wire is a script keyed on what each step asks for."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import decisions, judge as cjudge, llm_chat  # noqa: E402
from core import state_scopes as ss  # noqa: E402
from core.harness import control as ccontrol  # noqa: E402
from core.harness import record as crecord  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.kb import migrate, query, schema as kb  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402
from tests.test_verify_render import PROFILE, _add_place  # noqa: E402

NOW = datetime(2026, 9, 22, 18, 40)      # Tuesday, inside the synthetic alpha_city leg


# ── the scripted wire ──────────────────────────────────────────────────
class Wire:
    """Answers each step by what it asks for: the frame schema, the sanity
    schema, the tool loop, the answer schema, the judge schema."""

    def __init__(self, *, frame: dict, rounds: list[list[dict]], answers: list[dict], judge: dict | None = None,
                 errors: dict | None = None, sanity: dict | None = None):
        self.frame, self.rounds, self.answers, self.judge = frame, rounds, list(answers), judge
        self.errors = errors or {}
        self.sanity = sanity or {"faithful": True, "problem": None}
        self.calls: list[str] = []

    def kind_of(self, req: dict) -> str:
        props = ((req.get("schema") or {}).get("properties") or {})
        if "speaker" in props:
            return "understand"
        if "faithful" in props:
            return "sanity"
        if "verdicts" in props:
            return "judge"
        if "picks" in props:
            return "answer"
        if req.get("tools"):
            return "execute"
        return "other"

    def __call__(self, req: dict) -> dict:
        kind = self.kind_of(req)
        self.calls.append(kind)
        if kind in self.errors:
            return {"error": self.errors[kind]}
        usage = {"in": 1000, "out": 200, "thought": 0, "tool_prompt": 0}
        base = {"stop_reason": "stop", "citations": [], "search_queries": 0, "usage": usage, "error": None}
        if kind == "understand":
            text = json.dumps(self.frame)
            return base | {"text": text, "parts": [{"text": text}]}
        if kind == "sanity":
            text = json.dumps(self.sanity)
            return base | {"text": text, "parts": [{"text": text}]}
        if kind == "execute":
            n_model = sum(1 for m in req["messages"] if m.get("role") == "model")
            calls = self.rounds[n_model] if n_model < len(self.rounds) else []
            if not calls:
                return base | {"text": "READY", "parts": [{"text": "READY"}]}
            return base | {"text": "", "parts": [{"function_call": c} for c in calls]}
        if kind == "answer":
            a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            text = json.dumps(a)
            return base | {"text": text, "parts": [{"text": text}]}
        if kind == "judge":
            text = json.dumps(self.judge or {"verdicts": []})
            return base | {"text": text, "parts": [{"text": text}]}
        return base | {"text": "{}", "parts": [{"text": "{}"}]}


FRAME = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"], "meal": "dinner",
         "cuisines": ["italian"], "origin": {"label": "the stay", "kind": "stay", "source": "stay"},
         "assumptions": ["the baby is along"]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = str(tmp_path / "loop.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=db)
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.setenv("RAHAT_HARNESS_SANITY", "1")
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", tmp_path / "loop.db")
    tenant = bh.BourdainTenant(profile=json.loads(json.dumps(PROFILE)))
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    con = kb.connect(db)
    casa = query.find_place(con, "Casa Pasta")[0]["place_id"]
    menu = {r[0]: r[1] for r in con.execute("SELECT name, fact_id FROM kb_menu_item WHERE place_id = ?", (casa,))}
    hours_fid = query.fact(con, casa, "hours")["fact_id"]
    bad = _add_place(con, "Pub Grub", cuisine="pub", price="$$$", rating=("Google", 3.8, 12), hours="Mon–Fri 11–15",
                     room={"bar_led": True}, location_count=25)
    con.close()
    good_answer = {"kind": "picks", "picks": [{
        "place_id": casa, "name": "Casa Pasta", "why": "the penne is the one Local Mag called the best",
        "diner_lines": [{"member": "primary", "dish": "GF penne", "note": "ask for the gluten-free version", "verified": True,
                         "fact_id": menu.get("GF Penne")},
                        {"member": "spouse", "dish": "penne", "note": "with the vegetarian sauce"}],
        "evidence": [{"claim": "hours", "fact_id": hours_fid}], "next_step": "book for 7"}],
        "assumptions": ["the baby is along"]}
    bad_pick = {"place_id": bad, "name": "Pub Grub", "why": "hearty",
                "diner_lines": [{"member": "primary", "dish": "pub burger"}, {"member": "spouse", "dish": "beef burger"}]}
    return {"db": db, "tenant": tenant, "casa": casa, "bad": bad, "good": good_answer, "bad_pick": bad_pick,
            "rounds": [[{"name": "kb_search_places", "args": {"family": "italian", "within_minutes": 20, "mode": "walk"}}], []]}


def _wire(monkeypatch, w: Wire) -> Wire:
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    return w


def test_full_run_through_the_eight_steps(world, monkeypatch):
    spy = {"floor": 0}
    real_kf = ccontrol.keyword_frame
    monkeypatch.setattr(ccontrol, "keyword_frame", lambda text: (spy.__setitem__("floor", spy["floor"] + 1), real_kf(text))[1])
    judge_out = {"verdicts": [{"outcome": "U1", "verdict": "pass", "line": "both parts answered", "evidence_ids": []},
                              {"outcome": "D3", "verdict": "fail", "line": "no signature named", "evidence_ids": [], "hint": "name the signature"}]}
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]], judge=judge_out))
    res = hrun.run(world["tenant"], "pasta tonight near the stay", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.error is None and not res.floor_used and spy["floor"] == 0
    assert res.frame.meal == "dinner" and "P1" in res.contract.ids() and res.control.verify.ok
    assert "*Casa Pasta*" in res.text and "Checked 6:40 PM" in res.text and "Assuming: the baby is along." in res.text
    assert res.tool_calls == 1 and res.exec.rounds == 2 and res.cost_usd > 0
    assert w.calls[:2] == ["understand", "sanity"] and "judge" in w.calls
    # the judge ran in shadow: logged, nothing in the reply
    assert res.control.judge.authority == "shadow" and [v.outcome for v in res.control.judge.verdicts] == res.control.judge.judged
    assert "second check" not in res.text and "D3" not in res.text
    assert {"U1", "D3"} <= set(res.control.judge.judged)
    # recorded: outcomes, the run, the spans, the last answer, the search row
    outs = crecord.outcomes_for(res.trace_id, db_path=world["db"])
    assert outs["P1"] == "pass" and outs["E1"] == "pass" and outs["U1"] == "judge_pass" and outs["D3"] == "judge_fail"
    assert outs["U3"].startswith("pass") and "judge" in outs["U3"]
    ops = [r["op"] for r in decisions.by_trace(res.trace_id, db_path=world["db"])]
    for op in ("harness.understand", "harness.contract", "harness.execute", "harness.judge", "harness.render",
               "harness.control", "harness.record"):
        assert op in ops, (op, ops)
    last = ss.get("bourdain", "c1", "last_answer", now=NOW, db_path=world["db"])
    assert last["picks"][0]["name"] == "Casa Pasta" and last["trace_id"] == res.trace_id
    con = kb.connect(world["db"])
    assert con.execute("SELECT COUNT(*) FROM kb_search WHERE trace_id = ?", (res.trace_id,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM harness_runs WHERE trace_id = ?", (res.trace_id,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM judge_verdicts WHERE trace_id = ?", (res.trace_id,)).fetchone()[0] == len(res.control.judge.judged)
    con.close()
    rates = crecord.pass_rates("bourdain", db_path=world["db"])
    assert rates["runs"] == 1 and rates["clean_runs"] == 1 and rates["per_outcome"]["P1"]["rate"] == 1.0


def test_a_repair_round_fixes_a_failing_pick(world, monkeypatch):
    first = {"kind": "picks", "picks": [world["good"]["picks"][0], world["bad_pick"]], "recommendation": world["casa"]}
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[first, world["good"]]))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs == 1 and res.control.dropped == [] and res.control.verify.ok
    assert "Pub Grub" not in res.text and "*Casa Pasta*" in res.text
    repair_turns = [m for m in res.exec.history if m.get("role") == "user" and "verifier found" in json.dumps(m)]
    assert repair_turns and "beef" in json.dumps(repair_turns[0])     # the hint named the failure
    assert w.calls.count("answer") == 2


def test_failing_pick_is_dropped_after_the_repairs_and_the_gap_stated(world, monkeypatch):
    stuck = {"kind": "picks", "picks": [world["good"]["picks"][0], world["bad_pick"]], "recommendation": world["bad"]}
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[stuck]))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs == 2 and res.control.dropped == [world["bad"]]
    assert "Pub Grub" not in res.text and "*Casa Pasta*" in res.text
    assert res.control.verify.ok                     # the answer that went out passes every must
    assert res.answer.recommendation == world["casa"]   # the recommendation moved off the dropped pick


def test_model_down_takes_the_floor_and_only_then(world, monkeypatch):
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=[], answers=[world["good"]], errors={"understand": "ServerError: 500 internal"}))
    res = hrun.run(world["tenant"], "pasta tonight near the stay", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.floor_used and res.error == "ServerError: 500 internal"
    assert res.text.startswith("From my notes (dinner, pasta)") and "Casa Pasta" in res.text
    assert "The model is down right now" in res.text and "call first" in res.text
    assert w.calls == ["understand"]                 # nothing else was tried
    con = kb.connect(world["db"])
    assert con.execute("SELECT floor_used FROM harness_runs WHERE trace_id = ?", (res.trace_id,)).fetchone()[0] == 1
    con.close()


def test_hermetic_without_a_cassette_never_touches_the_wire(world, monkeypatch):
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: called.__setitem__("n", called["n"] + 1) or {"error": "should not be called"})
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"])
    assert called["n"] == 0 and res.floor_used and res.error == "hermetic"
    assert "not reachable here" in res.text


def test_brake_trips_and_is_disclosed(world, monkeypatch):
    # only network tools count: 14 web searches trip the 12-call brake; local reads never do
    search = {"name": "web_search", "args": {"query": "pasta", "city": "alpha city"}}
    rounds = [[search] * 7 + [{"name": "clock", "args": {}}] * 3, [search] * 7, []]
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=rounds, answers=[world["good"]]))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.exec.brake_tripped and "network calls" in res.exec.brake_tripped
    assert res.tool_calls == 17 and res.exec.rounds == 2
    assert "Stopped at the per-ask limit" in res.text and "*Casa Pasta*" in res.text


def test_unreadable_message_asks_one_question(world, monkeypatch):
    w = _wire(monkeypatch, Wire(frame={"intents": []}, rounds=[], answers=[world["good"]]))
    res = hrun.run(world["tenant"], "zzz", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.error == "unreadable" and res.text == ccontrol.UNREADABLE_LINE
    assert w.calls == ["understand", "understand"] and res.frame_problems


def test_holding_line_goes_out_once_when_research_runs_long(world, monkeypatch):
    sent = []
    tenant = world["tenant"]
    monkeypatch.setattr(tenant.latency, "holding_line_s", 0.0)
    _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"] + [[{"name": "clock", "args": {}}], []], answers=[world["good"]]))
    res = hrun.run(tenant, "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], holding_line=sent.append, judge_enabled=False)
    assert sent == ["Checking hours and menus — a minute."] and res.exec.holding_line_sent


def test_sanity_check_rereads_an_unfaithful_frame(world, monkeypatch):
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]],
                                sanity={"faithful": False, "problem": "the diners are wrong"}))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert w.calls[:3] == ["understand", "sanity", "understand"] and res.frame is not None


def test_reaction_links_the_next_message_to_the_previous_trace(world, monkeypatch):
    _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]],
                            judge={"verdicts": [{"outcome": "U1", "verdict": "pass", "line": "ok", "evidence_ids": []}]}))
    first = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"])
    verdict_frame = FRAME | {"intents": ["verdict", "eat"], "refers_to_last_answer": True,
                             "verdicts": [{"kind": "chain", "place": "Casa Pasta", "text": "it's a chain"}]}
    rounds = [[{"name": "record_verdict", "args": {"kind": "chain", "place_id": world["casa"], "text": "it's a chain"}}], []]
    _wire(monkeypatch, Wire(frame=verdict_frame, rounds=rounds, answers=[world["good"]]))
    second = hrun.run(world["tenant"], "Casa Pasta is a chain, something else", chat_id="c1", now=NOW, db_path=world["db"],
                      previous_trace=first.trace_id, judge_enabled=False)
    assert second.reaction == {"kind": "verdict", "outcomes": ["P9"]}
    con = kb.connect(world["db"])
    labels = con.execute("SELECT outcome, label FROM judge_labels WHERE trace_id = ?", (first.trace_id,)).fetchall()
    assert labels == [("P9", "fail")]
    con.close()
    rate = crecord.correction_rate("bourdain", db_path=world["db"])
    assert rate["counts"]["verdict"] == 1 and rate["correction_rate"] == 1.0
    # the chain verdict now excludes the place and the household rule promotes P9 — the pick is dropped
    assert second.control.dropped == [world["casa"]] and "P9" in second.contract.ids()
    assert "Nothing I found passed the checks" in second.text


def test_judge_validation_cache_and_authority(world, monkeypatch):
    judge_out = {"verdicts": [{"outcome": "U1", "verdict": "pass", "line": "fine", "evidence_ids": [999999]},
                              {"outcome": "D3", "verdict": "fail", "line": "no dish", "evidence_ids": []}]}
    w = _wire(monkeypatch, Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]], judge=judge_out))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"])
    rep = res.control.judge
    u1 = next(v for v in rep.verdicts if v.outcome == "U1")
    assert not u1.valid and "not in evidence" in u1.problem          # it introduced a record
    assert all(v.verdict == "unsure" for v in rep.verdicts if v.outcome not in ("U1", "D3"))
    assert not rep.cached
    res2 = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"])
    assert res2.control.judge.cached and w.calls.count("judge") == 1
    # authority is earned from labels, never from the manifest alone
    assert cjudge.authority_for("bourdain", ceiling="block", db_path=world["db"])["level"] == "shadow"
    for i in range(50):
        tid = f"t{i}"
        con = kb.connect(world["db"])
        con.execute("INSERT INTO judge_verdicts (trace_id, agent, outcome, verdict, at) VALUES (?, 'bourdain', 'D3', ?, '2026-10-01')",
                    (tid, "fail" if i % 10 else "pass"))
        con.commit(); con.close()
        cjudge.record_label(trace_id=tid, agent="bourdain", outcome="D3", label="fail", db_path=world["db"])
    auth = cjudge.authority_for("bourdain", ceiling="block", db_path=world["db"])
    assert auth["labels"] == 50 and auth["level"] == "annotate" and auth["per_outcome"]["D3"] == "block"
    assert cjudge.authority_for("bourdain", ceiling="shadow", db_path=world["db"])["level"] == "shadow"
    ag = cjudge.agreement("bourdain", db_path=world["db"])
    assert ag["overall"]["accuracy"] == 0.9 and ag["per_outcome"]["D3"]["n"] == 50
