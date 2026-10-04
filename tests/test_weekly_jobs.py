"""The weekly jobs: the shadow diff, the weekly report and the command
screener (ADR-018 §2.9–2.10, decision D13). Synthetic rows, no model,
nothing sent."""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import decisions, judge as cjudge, ledger, manifest as cmanifest  # noqa: E402
from core.harness import record as crecord  # noqa: E402
from core import eval_outcomes as ev  # noqa: E402

NOW = datetime(2026, 10, 4, 18, 0)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, f"scripts/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", path)
    return str(path)


def test_shadow_diff_summarizes_the_day(tmp_path, monkeypatch):
    log = tmp_path / "shadow.jsonl"
    recs = [
        {"at": "2026-10-04 12:01:00", "chat_id": "c1", "ask": "pasta tonight near the stay", "legacy_reply": "Dinner · 3 picks\n1. A",
         "harness_reply": "_dinner_\n*Casa Pasta*", "header": "dinner · primary + spouse", "pick_names": ["Casa Pasta"], "musts_failed": [],
         "repairs": 1, "dropped": [], "cost_usd": 0.031, "search_queries": 2, "latency_ms": 18000, "floor_used": False, "error": None},
        {"at": "2026-10-04 19:40:00", "chat_id": "c1", "ask": "bagels", "legacy_reply": "Bagels · 2 picks", "harness_reply": "x",
         "header": "breakfast", "pick_names": ["Bagel Corner"], "musts_failed": ["E2", "P10"], "judge_fails": ["D3"], "repairs": 2,
         "dropped": ["p1"], "cost_usd": 0.05, "search_queries": 3, "latency_ms": 41000, "floor_used": False, "error": None},
        {"at": "2026-10-03 09:00:00", "chat_id": "c1", "ask": "yesterday", "legacy_reply": "y", "error": "crash: boom"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in recs))
    monkeypatch.setenv("RAHAT_BOURDAIN_SHADOW_LOG", str(log))
    mod = _load("harness_shadow_diff")
    from agents.bourdain import harness_bridge
    today = harness_bridge.read_shadow_log(log, day="2026-10-04")
    assert len(today) == 2
    text = mod.summarize(today, "2026-10-04")
    assert "2 asks · harness clean on 1/2 · 3 repairs" in text and "$0.081" in text and "5 search queries" in text
    assert "musts failed E2" in text and "judge doubts D3" in text and "dropped 1" in text
    assert "legacy:  Dinner · 3 picks" in text and "harness: [dinner · primary + spouse] Casa Pasta · clean" in text
    assert mod.summarize([], "2026-10-05").endswith("no asks went through the harness.")
    assert mod.main(["--day", "2026-10-04", "--path", str(log)]) == 0


def test_weekly_report_reads_every_table(db, tmp_path):
    ledger.record(agent="bourdain", event_type="model", cost_usd=0.12, search_queries=7, now=NOW, db_path=db)
    ledger.record(agent="genie", event_type="model", cost_usd=0.02, now=NOW, db_path=db)
    crecord.record_run(trace_id="t1", agent="bourdain", chat_id="c1", mode="trip", serve_mode="shadow", ask="pasta", header="dinner",
                       kind="picks", picks=["p1"], musts_total=18, musts_failed=0, repairs=1, dropped=0, tool_calls=4, search_queries=2,
                       cost_usd=0.03, latency_ms=12000, floor_used=False, error=None, outcomes={"P1": "pass", "E2": "fail"}, now=NOW, db_path=db)
    crecord.record_reaction(trace_id="t2", previous_trace="t1", agent="bourdain", chat_id="c1", frame=None, text="great, going",
                            judged_outcomes=["U1"], now=NOW, db_path=db)
    cjudge.record_label(trace_id="t1", agent="bourdain", outcome="U1", label="pass", now=NOW, db_path=db)
    con = cjudge._connect(db)
    con.execute("INSERT INTO judge_verdicts (trace_id, agent, outcome, verdict, at) VALUES ('t1', 'bourdain', 'U1', 'pass', '2026-10-04')")
    con.commit(); con.close()
    rep = ev.EvalReport(agent="bourdain", mode="stub", set_path="x", cases=[ev.CaseResult(id="T01", passed=True), ev.CaseResult(id="T02", passed=False, failed=["E2"])],
                        finished="2026-10-04 03:10:00")
    ev.save_report(rep, tmp_path / "runs")
    mod = _load("weekly_report")
    text = mod.build(now=NOW, db_path=db, runs_dir=tmp_path / "runs")
    assert "Spend: this week $0.14 (bourdain $0.12, genie $0.02)" in text
    assert "Searches this month: 7 of 5000 free" in text and "No daily cap" in text
    assert "Harness bourdain: 1 asks, 1 with every must passing, 1 repairs" in text and "E2 ×1" in text
    assert "Golden bourdain (stub, 2026-10-04): 1/2 green; failing: E2 ×1." in text
    # two labels: the explicit one plus the acceptance's label on the judged outcome
    assert "Judge bourdain: 2 labels, agreement 1.0" in text and "earned level shadow" in text
    assert "Reactions bourdain to the legacy replies (harness in shadow): 1 linked (1 accepted" in text
    assert "Knowledge base:" in text


def test_command_screener_proposes_only_recurring_model_path_shapes(db):
    for i, text in enumerate(["pasta tonight near the stay", "Pasta tonight near the stay!", "pasta tonight near the stay please",
                              "/cost", "more", "going", "going", "going", "coffee near me", "coffee near me", "ok"]):
        crecord.record_run(trace_id=f"t{i}", agent="bourdain", chat_id="c1", mode="trip", serve_mode="shadow", ask=text, header="",
                           kind="picks", picks=[], musts_total=1, musts_failed=0, repairs=0, dropped=0, tool_calls=0, search_queries=0,
                           cost_usd=0.0, latency_ms=0, floor_used=False, error=None, outcomes={}, now=NOW, db_path=db)
    decisions.log("bourdain", "bourdain_bot.turn", trace_id="legacy-1", input="where should we eat tonight", db_path=db)
    mod = _load("command_screener")
    grammar = cmanifest.load("bourdain").grammar
    msgs = mod.gather("bourdain", since=datetime(2026, 10, 1), db_path=db)
    assert len(msgs) == 12
    result = mod.screen(msgs, grammar, min_count=3)
    assert result["grammar_hits"] == 5                       # /cost, more, going ×3 are already in the grammar
    assert [c["command"] for c in result["commands"]] == ["/pasta_tonight_near_stay"]
    assert result["commands"][0]["count"] == 3 and len(result["commands"][0]["examples"]) == 3
    assert result["replies"] == []                            # "coffee near me" ×2 is under the bar
    text = mod.render("bourdain", result, since=datetime(2026, 9, 27), until=NOW)
    assert "/pasta_tonight_near_stay  ×3" in text and "Nothing changes until the manifest is edited" in text
    assert mod.normalize("Give me 2 bagel places at 7:30!") == "# bagel places #"
