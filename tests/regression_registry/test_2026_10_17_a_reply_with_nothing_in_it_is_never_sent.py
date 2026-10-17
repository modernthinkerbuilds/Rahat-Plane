"""Regression pin — 2026-10-17: an answer that says nothing is not an
answer, and a reply that is only a header and a time is never sent.

LIVE 2026-10-17 12:11: the final call came back kind "answer" with no
lines, no picks, no question. It validated — `coherence_problems` only
asked a "picks" answer for picks, a "plan" for a plan, a "question" for
a question — every check passed, and the household received

    _you + your partner + baby · in <town>_
    Checked 12:11 PM

Now an "answer", "ack" or "check" with nothing in `lines`, no picks, no
plan, no question and no none-claim is a problem on the way back (the
executor is asked again, then the gap line), and the renderer marks a
body-less reply so the controller sends the gap line instead of it.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import answer as ans  # noqa: E402
from core.harness import control as ccontrol  # noqa: E402
from core.harness import evidence as hev  # noqa: E402
from core.harness import render as hr  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from core.kb import schema as kb  # noqa: E402
from tests.test_harness_loop import NOW, Wire, _wire, world  # noqa: E402,F401


@pytest.mark.parametrize("data", [
    {"kind": "answer"},
    {"kind": "answer", "lines": []},
    {"kind": "answer", "lines": ["", "  "]},
    {"kind": "ack"},
    {"kind": "check", "assumptions": ["the baby is along"]},
])
def test_an_answer_with_nothing_in_it_does_not_validate(data):
    with pytest.raises(ans.AnswerError) as e:
        ans.parse_answer(data)
    assert any("says nothing" in p and "lines" in p for p in e.value.problems)


@pytest.mark.parametrize("data", [
    {"kind": "answer", "lines": ["It opens at 7."]},
    {"kind": "ack", "lines": ["Saved."]},
    {"kind": "answer", "question": "Which town?"},
    {"kind": "answer", "none_claim": {"area": "the town", "hours": "12–14", "searched": ["the knowledge base"]}},
    {"kind": "question", "question": "Which town?"},
])
def test_an_answer_that_says_something_still_validates(data):
    assert ans.parse_answer(data).kind == data["kind"]


def test_the_renderer_marks_a_reply_with_no_body(world):  # noqa: F811
    con = kb.connect(world["db"])
    try:
        ev = hev.Evidence(con, now=NOW, mode="home")
        frame = Frame.model_validate({"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse"],
                                      "area": {"label": "the town", "kind": "area", "source": "message"}})
        empty = hr.render(frame, ans.Answer(kind="answer"), ev, now=NOW)
        assert empty.empty and any("no body" in p for p in empty.problems)
        assert empty.text.splitlines()[0].startswith("_") and "Checked" in empty.text
        said = hr.render(frame, ans.Answer(kind="answer", lines=["It opens at 7."]), ev, now=NOW)
        assert not said.empty and "It opens at 7." in said.text
    finally:
        ev.close()


def test_end_to_end_the_gap_line_goes_out_not_the_header(world, monkeypatch):  # noqa: F811
    frame = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"],
             "area": {"label": "the town", "kind": "area", "source": "message"}}
    _wire(monkeypatch, Wire(frame=frame, rounds=world["rounds"], answers=[{"kind": "answer"}]))
    res = hrun.run(world["tenant"], "the town", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.error is None
    body = [ln for ln in res.text.splitlines() if ln.strip() and not ln.startswith("_") and not ln.startswith("Checked")]
    assert body, f"a header and a time went out: {res.text!r}"
    assert res.text.strip() == ccontrol.GAP_LINE
    assert res.control is not None and res.control.repairs >= 1          # the executor was asked again first
