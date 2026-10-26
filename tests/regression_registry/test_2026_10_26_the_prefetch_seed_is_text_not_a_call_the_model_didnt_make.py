"""2026-10-26 — the first live pipeline run with a prefetch (the running
coach's analysis of one stored run) failed in one second with Gemini's
400 "Function call is missing a thought_signature in functionCall
parts": the seed opened the conversation with a model turn of function
calls the model never made, and Gemini 3 refuses those. The seed is now
text in the first user turn: no synthesized model turn, no function
call part, each result bounded like a tool turn, and the records'
ids are in it so the model can cite them."""
from __future__ import annotations

import json
import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import execute as cexecute  # noqa: E402
from core.tools import Brake, ToolRegistry  # noqa: E402


def test_the_seed_opens_the_conversation_as_text_in_one_user_turn(monkeypatch):
    seen: list[dict] = []

    def wire(req: dict) -> dict:
        seen.append(json.loads(json.dumps(req, default=str)))          # the history list grows after the call
        base = {"stop_reason": "stop", "citations": [], "search_queries": 0, "error": None,
                "usage": {"in": 100, "out": 10, "thought": 0, "tool_prompt": 0}}
        if req.get("tools") is not None and not req.get("schema"):
            return base | {"text": "READY", "parts": [{"text": "READY"}]}
        t = json.dumps({"kind": "answer", "headline": "ok"})
        return base | {"text": t, "parts": [{"text": t}]}

    monkeypatch.setattr(llm_chat, "_call_wire", wire)
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    big = {"ok": True, "result": {"records": [{"id": f"run:X:r{i}", "value": i} for i in range(2000)]}}
    seed = [("analyze_run", {"which": "uuid", "value": "X"}, big), ("get_athlete", {}, {"ok": True, "result": {"records": [{"id": "athlete:hr:cap"}]}})]
    cexecute.execute("test", system="s", prompt="the analysis of run X", registry=ToolRegistry(), ctx={}, brake=Brake(),
                     now=datetime(2026, 10, 26, 9, 0), seed=seed, max_rounds=1)
    first = next(r for r in seen if r.get("messages"))["messages"]
    assert len(first) == 1 and first[0]["role"] == "user"
    parts = first[0]["parts"]
    assert all(set(p) == {"text"} for p in parts), parts                       # no function_call, no function_response
    seed_text = parts[1]["text"]
    assert seed_text.startswith(cexecute.SEED_HEAD)
    assert "### analyze_run(" in seed_text and "### get_athlete(" in seed_text and "athlete:hr:cap" in seed_text
    assert len(seed_text) < 2 * cexecute.TOOL_PAYLOAD_LIMIT + 1000              # each result bounded like a tool turn


def test_without_a_seed_the_first_turn_is_the_prompt_alone(monkeypatch):
    seen: list[dict] = []

    def wire(req: dict) -> dict:
        seen.append(json.loads(json.dumps(req, default=str)))          # the history list grows after the call
        return {"stop_reason": "stop", "citations": [], "search_queries": 0, "error": None, "text": "READY",
                "parts": [{"text": "READY"}], "usage": {"in": 1, "out": 1, "thought": 0, "tool_prompt": 0}}

    monkeypatch.setattr(llm_chat, "_call_wire", wire)
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    cexecute.execute("test", system="s", prompt="hello", registry=ToolRegistry(), ctx={}, brake=Brake(),
                     now=datetime(2026, 10, 26, 9, 0), max_rounds=1)
    first = seen[0]["messages"]
    assert first == [{"role": "user", "parts": [{"text": "hello"}]}]
