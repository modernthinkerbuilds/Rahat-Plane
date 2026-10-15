"""Regression pin — 2026-10-15: the household can ask for a search in so
many words, and a conversational message never runs the research loop.

Owner decision (Oct 15), pulled forward from the DB-first design (D):

  * "search", "look online", "use Gemini", "use AI", "broaden", "wider",
    "more options" say: go beyond the knowledge base. "something new",
    "new places", "haven't tried", "not the usual" say: leave the
    household's regulars and saves out. Both are read in code, whatever
    the model read, and reach the executor as a how-line in the moment.
  * A message with no eating or planning in it (a save, a verdict, a
    profile change, a question, a reply) is answered without the
    network tools and in at most two rounds. Seven of the nineteen
    serve-mode asks were such messages; each ran the full research
    loop (3–12 tool calls, $0.03–0.12).
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import run as hrun  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from tests.test_harness_loop import NOW, Wire, _wire, world  # noqa: E402,F401


def _frame(**kw) -> Frame:
    base = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse"], "meal": "dinner"}
    base.update(kw)
    return Frame.model_validate(base)


def _ctx(ask: str, **kw) -> dict:
    c = {"ask": ask, "diners": ["primary", "spouse"], "last_answer": {}, "now": NOW, "mode": "home",
         "leg": {"leg_id": "home", "kind": "home", "city": "alpha_city", "minutes_mode": "drive"}}
    c.update(kw)
    return c


@pytest.mark.parametrize("ask", [
    "search online for a good taqueria near us",
    "use gemini and find a ramen place",
    "use AI for this one: dessert tonight",
    "broaden it, anything within 40 minutes",
    "give me more options for lunch",
    "look it up, is there a Burmese place in town",
])
def test_the_search_words_are_read_in_code(ask):
    t = bh.BourdainTenant(profile={})
    ctx = _ctx(ask)
    frame, notes = t.refine_frame(_frame(), ctx)
    assert ctx.get("search_asked") is True and "search_asked" in frame.attributes
    assert any("search" in n for n in notes)
    m = t.moment(ctx, frame)
    assert "search_asked" in m and "knowledge base" in m["search_asked"]["how"]


@pytest.mark.parametrize("ask", ["something new for dinner", "new places only", "somewhere we haven't tried",
                                 "not the usual, surprise us"])
def test_the_new_place_words_leave_the_regulars_out(ask):
    t = bh.BourdainTenant(profile={})
    ctx = _ctx(ask)
    frame, _ = t.refine_frame(_frame(), ctx)
    assert ctx.get("new_asked") is True and "new_places" in frame.attributes
    m = t.moment(ctx, frame)
    assert "regulars" in m["new_places"]["how"]


def test_an_ordinary_ask_asks_for_neither():
    t = bh.BourdainTenant(profile={})
    ctx = _ctx("pasta tonight near the stay")
    frame, _ = t.refine_frame(_frame(), ctx)
    assert not ctx.get("search_asked") and not ctx.get("new_asked")
    assert "search_asked" not in frame.attributes and "new_places" not in frame.attributes


@pytest.mark.parametrize("intents, needed", [
    (["eat"], True), (["plan"], True), (["place_check"], True), (["eat", "verdict"], True),
    (["save"], False), (["verdict"], False), (["profile_change"], False), (["question"], False),
    (["chat"], False), (["recall"], False), (["plan_edit"], False), (["logistics"], False),
])
def test_research_is_needed_only_when_something_is_to_be_found(intents, needed):
    t = bh.BourdainTenant(profile={})
    assert t.research_needed(_frame(intents=intents), _ctx("…")) is needed


def test_a_search_asked_in_words_needs_research_whatever_the_intents():
    t = bh.BourdainTenant(profile={})
    ctx = _ctx("search online, anything else nearby?")
    frame, _ = t.refine_frame(_frame(intents=["question"]), ctx)
    assert t.research_needed(frame, ctx) is True


def test_without_research_the_registry_has_no_network_tool():
    t = bh.BourdainTenant(profile={})
    names = {d["name"] for d in t.registry(_ctx("…", no_research=True, chat_id="c1", db_path=":memory:")).declarations()}
    assert names and not (names & set(bh.NETWORK_TOOLS))
    full = {d["name"] for d in t.registry(_ctx("…", chat_id="c1", db_path=":memory:")).declarations()}
    assert "maps_search" in full and "web_search" in full


def test_a_save_runs_without_the_network_and_in_two_rounds(world, monkeypatch):  # noqa: F811
    frame = {"speaker": "primary", "intents": ["save"], "diners": ["primary", "spouse"],
             "saves": [{"place": "Casa Pasta", "heard_from": "a friend"}]}
    seen: list[dict] = []
    w = Wire(frame=frame, rounds=[[{"name": "resolve_name", "args": {"text": "Casa Pasta"}}],
                                  [{"name": "maps_search", "args": {"query": "Casa Pasta"}}], []],
             answers=[{"kind": "ack", "picks": [], "assumptions": [], "note": "saved"}])
    real = w.__call__

    def spy(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(hrun.llm_chat, "_call_wire", spy)
    res = hrun.run(world["tenant"], "save Casa Pasta, a friend loved it", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.error is None
    execs = [r for r in seen if r.get("tools")]
    assert execs, "the model never got the tools"
    declared = {d["name"] for r in execs for d in r["tools"]}
    assert "maps_search" not in declared and "web_search" not in declared and "resolve_name" in declared
    assert res.exec.rounds <= 2
    assert any(s.get("step") == "understand" for s in res.steps)
    assert any(s.get("step") == "breadth" and s.get("research") is False for s in res.steps)
    assert res.text and "didn't follow" not in res.text
