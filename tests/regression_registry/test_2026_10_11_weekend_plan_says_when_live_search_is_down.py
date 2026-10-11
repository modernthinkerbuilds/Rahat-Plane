"""Regression (2026-10-11): /weekend_plan says when live search is down.

LIVE shape (QA-2026-10-04-08; the 2026-09-17 spend cap): when live
discovery failed, /weekend_plan sent the generic offline menu ("Slow
morning at home…") with no word that nothing had been looked up. The
same reply was then saved over any plan the family had already saved for
that weekend, which swaps and /replan_day read.

Fix:
  * live_plan.discover() says why discovery came back empty: "down" (the
    call failed: budget, quota, wire) or "unusable" (two answers with no
    usable options). discover_options() is unchanged for its callers.
  * The card's third line says so: "Live search is down right now, so
    this is the offline plan." or "Live search found nothing usable just
    now…", with `/whatson` (the verified feeds) outside the italic.
  * A fallback plan is saved only when nothing is saved for that weekend
    yet; otherwise the reply says the saved plan stays.
Live search switched off, or no home area, is not a failure and reads as
before. Invented places throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.telegram_checks import reply_problems

WEDNESDAY = datetime(2026, 10, 7, 9, 0)
DOWN = ("_Live search is down right now, so this is the offline plan._ "
        "`/whatson` has the verified events.")
UNUSABLE = ("_Live search found nothing usable just now, so this is the offline plan._ "
            "`/whatson` has the verified events.")
_GOOD = {"weather": {"saturday": "mild", "sunday": "sunny"},
         "options": {"saturday": [{"time": "morning", "activity": "Farm visit", "place": "Hill Farm"}],
                     "sunday": [{"time": "morning", "activity": "Creek walk", "place": "Creek Park"}]}}


class _Model:
    def __init__(self, answer=None, error: str | None = None):
        self.answer, self.error, self.calls = answer, error, 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Alpha Town, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "1")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


def test_a_failed_live_search_says_so_in_one_line(genie):
    model = _Model(error="429 RESOURCE_EXHAUSTED: monthly spending cap")
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, llm=model)
    lines = out.split("\n")
    assert lines[0] == "*Weekend plan — week of 2026-10-10*" and lines[2] == DOWN, out
    assert out.count("Live search") == 1
    assert "Slow morning at home" in out              # the offline plan still comes
    assert model.calls == 1                           # a failed call is not retried
    assert reply_problems(out) == [], out


def test_answers_with_nothing_usable_say_so(genie):
    model = _Model(answer="no json here")
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, llm=model)
    assert out.split("\n")[2] == UNUSABLE, out
    assert model.calls == 2                           # an unusable answer is asked once more
    assert reply_problems(out) == [], out


def test_option_sets_on_a_failed_search_say_so_too(genie):
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, want_options=True,
                                    llm=_Model(error="429 RESOURCE_EXHAUSTED"))
    assert DOWN in out and "go with A" not in out, out


def test_a_working_live_search_adds_no_line(genie):
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, llm=_Model(answer=_GOOD))
    assert "Live search" not in out and "Farm visit at Hill Farm" in out, out


def test_live_search_switched_off_reads_as_before(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "0")
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, llm=_Model(answer=_GOOD))
    assert "Live search" not in out and "Slow morning at home" in out, out


def test_a_fallback_plan_does_not_replace_the_saved_one(genie):
    from agents.genie import state
    genie.handle_weekend_plan(now=WEDNESDAY, llm=_Model(answer=_GOOD))
    saved = state.latest_weekend_plan()
    out = genie.handle_weekend_plan(now=WEDNESDAY, llm=_Model(error="429 RESOURCE_EXHAUSTED"))
    assert "_Your saved plan for this weekend stays as it was._" in out and "✅ Plan saved." not in out, out
    assert state.latest_weekend_plan() == saved
    assert any("Farm visit" in ln for ln in state.latest_weekend_plan().saturday)


def test_a_fallback_plan_is_saved_when_none_was(genie):
    from agents.genie import state
    out = genie.handle_weekend_plan(now=WEDNESDAY, llm=_Model(error="429 RESOURCE_EXHAUSTED"))
    assert "✅ Plan saved." in out and state.latest_weekend_plan() is not None


def test_discover_says_why_it_came_back_empty():
    from agents.genie import live_plan as lp
    kw = dict(location="Alpha Town, CA", sat_iso="2026-10-10", sun_iso="2026-10-11",
              energy="low", roles=[], constraints=[])
    assert lp.discover(llm=_Model(error="429 RESOURCE_EXHAUSTED"), **kw) == (None, "down")
    assert lp.discover(llm=_Model(answer="no json here"), **kw) == (None, "unusable")
    disc, why = lp.discover(llm=_Model(answer=_GOOD), **kw)
    assert why == "" and disc.saturday[0].activity == "Farm visit"
    assert lp.discover_options(llm=_Model(error="429"), **kw) is None
