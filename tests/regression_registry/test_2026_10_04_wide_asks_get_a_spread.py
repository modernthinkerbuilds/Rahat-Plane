"""Regression pin (2026-10-04): a wide ask gets a spread, not one answer.

LIVE (2026-10-04): "a coffee shop … I can drive up to 45 min", then
"Check across the whole area", "I can travel up to 60 minutes, expand
search" — and, replayed, "coffee and a board game space anywhere in the
area, I can drive 50 minutes" — each came back with one café or
none. Every ask had the same twelve-call brake and the same "at most
three picks", so a whole-area ask was researched like "coffee near me".

THE PINS (BourdainTenant.is_wide, the run's breadth step).
  * Wide: a drive of half an hour or more, or words asking for range
    ("options", "anywhere", "across", "expand"…). Never a day plan or a
    check on one named place.
  * A wide ask gets the manifest's wide brake, "at most five picks" in
    the brief, the moment's wide_ask, and room on the screen for five.
  * Anything else is unchanged: the twelve-call brake, three picks.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.harness.frame import parse_frame  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)


@pytest.mark.parametrize("extra,text,wide", [
    ({"radius": {"minutes": 50}}, "coffee and board games, I can drive 50 minutes", True),
    ({}, "coffee anywhere in the area", True),
    ({"refers_to_last_answer": True}, "Other options please?", True),
    ({}, "Check across the whole area", True),
    ({"radius": {"minutes": 15}}, "coffee near me", False),
    ({}, "pasta tonight", False),
    ({"intents": ["plan"], "radius": {"minutes": 50}}, "plan our day, we can drive 50 minutes", False),
    ({"named_places": ["Casa Pasta"]}, "is Casa Pasta open across from the park?", False),
])
def test_what_is_wide(world, extra, text, wide):  # noqa: F811
    assert world["tenant"].is_wide(parse_frame(FRAME | extra), text, {}) is wide


class Spy(Wire):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.execute_reqs: list[str] = []

    def __call__(self, req):
        if self.kind_of(req) == "execute":
            self.execute_reqs.append("\n".join(p.get("text") or "" for m in req.get("messages") or []    # the brief,
                                                for p in m.get("parts") or []))                          # not the playbook
        return super().__call__(req)


def _run(world, monkeypatch, frame, text):  # noqa: F811
    w = Spy(frame=frame, rounds=world["rounds"], answers=[world["good"]])
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    res = hrun.run(world["tenant"], text, chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    return res, w.execute_reqs[0]


def test_a_wide_ask_gets_the_wide_brake_five_picks_and_room(world, monkeypatch):  # noqa: F811
    res, brief = _run(world, monkeypatch, FRAME | {"radius": {"minutes": 50}}, "pasta anywhere, we can drive 50 minutes")
    wide = world["tenant"].budgets.per_ask_wide
    assert wide is not None and wide.tool_calls > world["tenant"].budgets.per_ask.tool_calls
    step = next(s for s in res.steps if s.get("step") == "breadth")
    assert step == {"step": "breadth", "wide": True, "max_picks": 5, "tool_calls": wide.tool_calls}
    assert "at most five picks" in brief and "wide_ask" in brief and f'"tool_calls": {wide.tool_calls}' in brief
    assert "*Casa Pasta*" in res.text


def test_a_near_ask_is_unchanged(world, monkeypatch):  # noqa: F811
    res, brief = _run(world, monkeypatch, FRAME, "pasta tonight")
    assert not any(s.get("step") == "breadth" for s in res.steps)
    assert "at most three picks" in brief and "wide_ask" not in brief
    assert f'"tool_calls": {world["tenant"].budgets.per_ask.tool_calls}' in brief
