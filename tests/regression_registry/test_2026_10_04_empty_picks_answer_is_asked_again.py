"""Regression pin (2026-10-04): an answer that says "picks" and names no
pick is sent back to the model, never rendered.

Found replaying a real household coffee ask through the harness before
serving it.
One run gathered two good candidates, then the final schema-bound call
came back {"kind": "picks", "recommendation": "<a place id>"} with no
picks array. It validated (picks defaults to empty), nothing named a
place so no place check ran, and the reply was a header and an
assumption: an empty answer to a family waiting on a coffee.

THE PINS.
  * parse_answer refuses the shapes that can't be true: "picks" with no
    picks, a recommendation that isn't a pick, an empty plan, an empty
    question.
  * End to end, the refusal goes back to the model once (the existing
    validation retry) and the corrected answer is the one rendered.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import answer as ans  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)


@pytest.mark.parametrize("payload,needle", [
    ({"kind": "picks", "recommendation": "p1"}, "picks is empty"),
    ({"kind": "picks", "picks": [{"place_id": "p1", "name": "X"}], "recommendation": "p9"}, "not one of the picks"),
    ({"kind": "plan"}, "plan is empty"),
    ({"kind": "question"}, "question is empty"),
])
def test_incoherent_answers_are_refused(payload, needle):
    with pytest.raises(ans.AnswerError) as e:
        ans.parse_answer(payload)
    assert any(needle in p for p in e.value.problems), e.value.problems


def test_coherent_answers_pass():
    assert ans.parse_answer({"kind": "picks", "picks": [{"place_id": "p1", "name": "X"}], "recommendation": "p1"})
    assert ans.parse_answer({"kind": "none", "none_claim": {"area": "the area", "hours": "now", "searched": ["kb"]}})
    assert ans.parse_answer({"kind": "ack", "lines": ["noted"]})


def test_the_empty_answer_goes_back_and_the_fix_is_rendered(world, monkeypatch):  # noqa: F811
    empty = {"kind": "picks", "recommendation": world["casa"], "assumptions": ["a solo diner"]}
    wire = Wire(frame=FRAME, rounds=world["rounds"], answers=[empty, world["good"]])
    monkeypatch.setattr(llm_chat, "_call_wire", wire)
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert wire.calls.count("answer") == 2
    assert res.answer is not None and res.answer.picks
    assert "*Casa Pasta*" in res.text
