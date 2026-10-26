"""A pipeline run reads its records before the model's first turn (A6
addition 3, 2026-10-25): the tool calls it knows it needs run in the
run's own context, their records are the evidence and their results
open the conversation as text (no call the model didn't make). A conversation
run without `prefetch` is unchanged (the Bourdain gate)."""
from __future__ import annotations

import json
import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import execute as cexecute  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from agents.kipchoge import pipeline, shapes  # noqa: E402
from tests.kipchoge.test_tenant import Wire, world  # noqa: E402, F401

NOW = datetime(2026, 10, 24, 9, 30)


def test_a_pipeline_run_opens_with_its_records_in_hand(world, monkeypatch):  # noqa: F811
    seen: list[dict] = []
    answer = {"kind": "analysis", "headline": "Grade {{r:run:R-C:grade}}: {{r:run:R-C:pace_s_per_mi}} at {{r:run:R-C:hr_avg_moving}}.",
              "bottom_line": ["Halves {{r:run:R-C:run_halves.first.hr_avg}} → {{r:run:R-C:run_halves.second.hr_avg}}."], "sections": [],
              "records_used": ["run:R-C:grade", "run:R-C:pace_s_per_mi"], "normalized": False}
    w = Wire(frame={"intent": "analyze"}, rounds=[[]], answers=[answer])
    orig = w.__call__

    def spy(req):
        if req.get("tools"):
            seen.append(json.loads(json.dumps(req, default=str)))        # the history as sent (the list grows after)
        return orig(req)
    monkeypatch.setattr(llm_chat, "_call_wire", spy)
    tenant = world["tenant"]
    ctx = tenant.build_ctx("pipeline", NOW, "", db_path=world["db"], cassette_dir=None, trace_id="t")
    pre = pipeline.analysis_prefetch(ctx, "R-C", NOW)
    ctx["con"].close()
    frame = shapes.Frame(intent="analyze", run_ref=shapes.RunRef(which="uuid", value="R-C"), wants_thorough=True)
    res = hrun.run_pipeline(tenant, "the analysis of run R-C", frame=frame, chat_id="pipeline", now=NOW, db_path=world["db"],
                            serve_mode="shadow", prefetch=pre)
    assert res.error is None and not res.floor_used, (res.error, res.text)
    assert [s["step"] for s in res.steps if s["step"] == "prefetch"] == ["prefetch"]
    assert {"step": "breadth", "research": False, "rounds_max": hrun.NO_RESEARCH_ROUNDS} in res.steps   # no research loop
    assert res.tool_calls == len(pre) and res.exec.rounds == 1                  # one round: READY, no calls of its own
    assert "Grade C" in res.text and "[no record]" not in res.text
    first = seen[0]["messages"]
    assert len(first) == 1 and first[0]["role"] == "user" and len(first[0]["parts"]) == 2
    seed = first[0]["parts"][1]["text"]
    assert seed.startswith(cexecute.SEED_HEAD)
    for name, _ in pre:
        assert f"### {name}(" in seed
    assert "run:R-C:grade" in seed                                   # the records themselves, citable by id
    assert not any("function_call" in p for m in first for p in m["parts"])   # no call the model didn't make
    assert res.outcome_results().get("E1") == "pass"


def test_the_week_review_tool_is_the_review_records(world):  # noqa: F811
    from agents.kipchoge import tools as ktools
    tenant = world["tenant"]
    ctx = tenant.build_ctx("pipeline", datetime(2026, 10, 25, 19, 0), "", db_path=world["db"], cassette_dir=None, trace_id="t")
    try:
        out = ktools.build_registry(ctx).execute("week_review", {}, ctx)
        assert out.ok
        ids = {r["id"] for r in out.payload()["result"]["records"]}
        assert any(i.startswith("review:w1:session:") for i in ids) and "review:w1:done" in ids
        assert ctx["evidence"].get("review:w1:planned") is not None
    finally:
        ctx["con"].close()


def test_a_conversation_ask_keeps_the_full_loop(world):  # noqa: F811
    tenant = world["tenant"]
    ctx = tenant.build_ctx("c", NOW, "how was my run", db_path=world["db"], cassette_dir=None, trace_id="t")
    try:
        assert tenant.research_needed(None, ctx) is True
        ctx["prefetched"] = ["analyze_run"]
        assert tenant.research_needed(None, ctx) is False
    finally:
        ctx["con"].close()
