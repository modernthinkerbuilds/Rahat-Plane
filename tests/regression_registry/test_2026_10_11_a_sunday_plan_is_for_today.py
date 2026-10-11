"""Regression (2026-10-11): a Sunday /weekend_plan plans today.

LIVE shape (QA-2026-10-04-07; decision D2, agreed): on a Sunday,
/weekend_plan planned the NEXT weekend, six days out, while /whatson and
the digest showed today. The household asks on a Sunday morning for that
day.

Fix (agents/genie/handler.handle_weekend_plan), the digest's rule: on a
Sunday the window is today alone.
  * Discovery asks for that one day; finds under either day key are
    today's. Commitments are today's.
  * The card says "Weekend plan — Sunday <date> (today)", has only the
    Sunday section, and ends with how to plan next weekend:
    `/weekend_plan next`.
  * The plan is still keyed by the weekend's Saturday (yesterday), where
    a plan saved in the week and the family's proposal live, so
    /replan_day reads today as its Sunday.
  * Option sets and `go with` show the Sunday only.
Monday to Saturday read as before. Invented places throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import date, datetime

import pytest

from tests.telegram_checks import reply_problems

SUNDAY_9AM = datetime(2026, 10, 4, 9, 0)
NEXT_LINE = "_It's Sunday, so this plans today._ `/weekend_plan next` plans next weekend."


class _Model:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.answer)


def _today(*options, key="sunday"):
    return {"weather": {key: "sunny, 72"}, "options": {key: list(options)}}


FARM = {"time": "morning", "activity": "Farm visit", "place": "Hill Farm", "why": "animals", "source": "farm site"}
KITES = {"time": "afternoon", "activity": "Kite day", "place": "Meadow", "why": "open field", "source": "parks site"}


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Alpha Town, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "1")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_NAP_GUARD", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


def test_a_sunday_plan_is_today_alone(genie):
    model = _Model(_today(FARM))
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, commit=False, llm=model)
    assert out.startswith("*Weekend plan — Sunday 2026-10-04 (today)* · live options for Alpha Town, CA"), out
    assert "*Sunday*" in out and "*Saturday*" not in out
    assert "Morning (9:00–11:30): Farm visit at Hill Farm" in out
    assert "Weather today: sunny, 72" in out
    assert NEXT_LINE in out
    assert "Date: Sunday 2026-10-04, this one day only" in model.prompts[0]
    assert reply_problems(out) == [], out


def test_finds_under_either_day_key_are_todays(genie):
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, commit=False, llm=_Model(_today(FARM, key="saturday")))
    assert "Farm visit at Hill Farm" in out and "*Saturday*" not in out, out


def test_only_todays_commitments_are_shown(genie):
    from agents.genie import state
    for title, day in (("Brunch with friends", "2026-10-03"), ("Swim class", "2026-10-04")):
        assert state.add_calendar_entry({"title": title, "date": day, "start": "11:00", "end": "12:00",
                                         "kind": "commitment"})[0]
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, commit=False, llm=_Model(_today(KITES)))
    assert "📌 Sun 11:00–12:00 — Swim class" in out and "Brunch with friends" not in out, out


def test_the_plan_keeps_the_weekend_key_and_replans_as_today(genie):
    from agents.genie import state
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, llm=_Model(_today(FARM, KITES)), energy_override="medium")
    assert "✅ Plan saved." in out, out
    plan = state.latest_weekend_plan()
    assert plan.weekend_of == "2026-10-03" and plan.saturday == []
    assert plan.sunday == ["Morning (9:00–11:30): Farm visit at Hill Farm — animals (farm site)",
                           "Afternoon (3:30–5:30): Kite day at Meadow — open field (parks site)"]
    replan = genie.handle_replan_today(now=datetime(2026, 10, 4, 13, 0))
    assert replan.startswith("🕐 Day-of replan — Sunday") and "Kite day" in replan, replan


def test_the_familys_proposal_for_this_weekend_still_anchors_it(genie):
    from agents.genie import state
    ok, _ = state.save_household_ideas({"weekends": [{"weekend_of": "2026-10-03", "label": "Lake day",
                                                       "items": ["Picnic by the lake"]}]})
    assert ok
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, commit=False, llm=_Model(_today(FARM)))
    assert "Your plan on file" in out and "Picnic by the lake" in out, out


def test_option_sets_and_go_with_show_the_sunday_only(genie):
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, want_options=True, llm=_Model(_today(FARM, KITES)))
    assert "*Option A — closest fit*\n*Sunday*" in out and "*Option B — change of pace*\n*Sunday*" in out, out
    assert "*Saturday*" not in out
    chosen = genie.handle_go_with("B")
    assert "*Saturday*" not in chosen and "*Sunday*" in chosen and "Kite day" in chosen, chosen


def test_the_offline_sunday_plan_is_sundays_menu(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "0")
    out = genie.handle_weekend_plan(now=SUNDAY_9AM, commit=False)
    assert "Rest-and-reset day" in out and "Slow morning at home" not in out and "*Saturday*" not in out, out


def test_next_plans_the_following_weekend(genie, monkeypatch):
    from tests.datefreeze import freeze
    freeze(monkeypatch, date(2026, 10, 4), modules=("agents.genie.handler",))
    out = genie.route("/weekend_plan next")
    assert out.startswith("*Weekend plan — week of 2026-10-10*"), out
    assert "*Saturday*" in out and NEXT_LINE not in out


def test_saturday_still_plans_the_whole_weekend(genie):
    out = genie.handle_weekend_plan(now=datetime(2026, 10, 3, 9, 0), commit=False,
                                    llm=_Model({"weather": {}, "options": {"saturday": [FARM], "sunday": [KITES]}}))
    assert out.startswith("*Weekend plan — week of 2026-10-03*") and "*Saturday*" in out and "*Sunday*" in out, out
    assert NEXT_LINE not in out
