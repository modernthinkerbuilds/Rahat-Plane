"""The goldens runner (scripts/kipchoge_goldens.py) on invented cases, the
synthetic store and a fake wire: it copies the store without writing to
the source, runs each ask through the harness, grades the musts with the
eval grader, reports outcomes the coach doesn't have as not built, and
prints pass/fail by slice."""
from __future__ import annotations

import json
import os
import sqlite3

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from tests.kipchoge.test_tenant import Wire, world  # noqa: E402, F401

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location("kipchoge_goldens", Path(__file__).resolve().parents[2] / "scripts/kipchoge_goldens.py")
goldens = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402
sys.modules["kipchoge_goldens"] = goldens
_spec.loader.exec_module(goldens)


class GradingWire(Wire):
    def __init__(self, *a, grades: dict, **k):
        super().__init__(*a, **k)
        self.grades = grades

    def __call__(self, req):
        props = ((req.get("schema") or {}).get("properties") or {})
        if "items" in props:
            self.calls.append("grade")
            ask = next(p["text"] for m in req["messages"] for p in m["parts"] if "text" in p)
            met = self.grades["fail" if "Mars" in ask else "pass"]
            t = json.dumps({"items": [{"text": "x", "kind": "must", "met": met, "why": "because"}]})
            return {"stop_reason": "stop", "citations": [], "search_queries": 0, "error": None, "text": t, "parts": [{"text": t}],
                    "usage": {"in": 50, "out": 20, "thought": 0, "tool_prompt": 0}}
        return super().__call__(req)


def test_the_runner_grades_by_slice_on_a_copy_of_the_store(world, monkeypatch, tmp_path):  # noqa: F811
    answer = {"kind": "answer", "headline": "Your cap is {{r:athlete:hr:cap}}.", "bottom_line": ["Ease off above {{r:athlete:hr:cap}}."],
              "sections": [], "records_used": ["athlete:hr:cap"]}
    w = GradingWire(frame={"intent": "zones"}, rounds=[[{"name": "get_athlete", "args": {}}], []], answers=[answer],
                    grades={"pass": True, "fail": False})
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(json.dumps(c) for c in [
        {"id": "T01", "slice": "S1", "journey": "A", "ask": "what's my cap", "context": "", "must": ["say the cap"], "must_not": [], "outcomes": ["E1"]},
        {"id": "T02", "slice": "S1", "journey": "H", "ask": "zone 2 pace on Mars?", "context": "", "must": ["say no data"], "must_not": [], "outcomes": ["E1"]},
        {"id": "T03", "slice": "S2", "journey": "E", "ask": "what's my cap again", "context": "", "must": ["say the cap"], "must_not": [], "outcomes": ["L2"]},
    ]) + "\n")
    before = sqlite3.connect(world["db"]).execute("select count(*) from sqlite_master").fetchone()[0]
    out = tmp_path / "rows.jsonl"
    assert goldens.main(["--cases", str(cases), "--db", world["db"], "--now", "2026-10-24T09:30", "--out", str(out)]) == 0
    rows = {r["id"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert rows["T01"]["verdict"] == "pass" and rows["T01"]["outcomes"]["E1"].startswith("pass")
    assert rows["T02"]["verdict"] == "fail" and rows["T02"]["unmet"]
    assert rows["T03"]["outcomes"] == {"L2": "not built"}
    assert "Your cap is" in rows["T01"]["reply"]
    assert sqlite3.connect(world["db"]).execute("select count(*) from sqlite_master").fetchone()[0] == before   # the source untouched
    text = goldens.report(list(rows.values()))
    assert "S1 | 1 | 1" in text and "S2 | 1 | 0" in text
