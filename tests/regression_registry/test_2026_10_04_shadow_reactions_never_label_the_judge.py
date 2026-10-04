"""Regression pin (2026-10-04): in shadow mode the household reacts to
the legacy reply, so a reaction is recorded (the legacy baseline) but
never becomes a label for the harness's judge.

Before the fix every reaction linked to the previous harness trace was
turned into judge labels. In shadow the household never saw that
trace's reply: "that's a chain" about a legacy pick would have marked
the harness's P9 as failed, and the judge's agreement figures (which
decide its authority) would have been computed against the wrong answers.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import judge as cjudge, llm_chat  # noqa: E402
from core.harness import record as crecord  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)

NOW = datetime(2026, 9, 22, 18, 40)


def _pair(world, monkeypatch, mode):  # noqa: F811
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]]))
    first = hrun.run(world["tenant"], "pasta tonight", chat_id=f"c-{mode}", now=NOW, db_path=world["db"],
                     serve_mode=mode, judge_enabled=False)
    verdict_frame = FRAME | {"intents": ["verdict", "eat"], "refers_to_last_answer": True,
                             "verdicts": [{"kind": "chain", "place": "Casa Pasta", "text": "it's a chain"}]}
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=verdict_frame, rounds=[[]], answers=[world["good"]]))
    second = hrun.run(world["tenant"], "that's a chain", chat_id=f"c-{mode}", now=NOW, db_path=world["db"],
                      serve_mode=mode, previous_trace=first.trace_id, judge_enabled=False)
    con = cjudge._connect(world["db"])          # creates the label table if nothing was ever labelled
    labels = con.execute("SELECT outcome, label FROM judge_labels WHERE trace_id = ?", (first.trace_id,)).fetchall()
    con.close()
    return first, second, labels


def test_shadow_reaction_is_kept_but_labels_nothing(world, monkeypatch):  # noqa: F811
    first, second, labels = _pair(world, monkeypatch, "shadow")
    assert second.reaction == {"kind": "verdict", "outcomes": ["P9"]}
    assert labels == []
    assert crecord.correction_rate("bourdain", serve_mode="shadow", db_path=world["db"])["counts"] == {"verdict": 1}
    assert crecord.correction_rate("bourdain", serve_mode="serve", db_path=world["db"])["total"] == 0


def test_serve_reaction_labels_the_judge(world, monkeypatch):  # noqa: F811
    first, second, labels = _pair(world, monkeypatch, "serve")
    assert labels == [("P9", "fail")]
    assert crecord.correction_rate("bourdain", serve_mode="serve", db_path=world["db"])["counts"] == {"verdict": 1}
