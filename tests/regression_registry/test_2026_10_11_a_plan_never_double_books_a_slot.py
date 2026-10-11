"""Regression (2026-10-11): a weekend plan never puts two things in one time slot.

LIVE shape (QA-2026-10-04-05): the default household has a toddler and a
newborn, so it plans at low energy. sequence_day appended "Afternoon
(3:30–5:30): home — quiet play, early wind-down" after the day's outing,
whatever the outing's slot. An afternoon outing shared 3:30–5:30 with the
home block, and an evening outing came before it. At medium and high
energy, two finds for the same slot were both kept. A swap sorted the
windowed lines as if every one were morning ("Morning (9" was read as the
slot), so a swapped-in afternoon outing went last, after the home block
that held its slot.

Fix (agents/genie/live_plan.py; agents/genie/handler.handle_swap):
  * one outing per slot; a second find for a slot is an alternate;
  * the home block takes the afternoon when it is free (before any
    evening outing), else the evening;
  * a swap keeps the time window on the swapped-in line, replaces the
    outing in its slot first, sorts by the real slot, and moves the home
    block out of the way. The displaced outing goes back to the pool
    with its own slot.

The plan cards are scored with the Genie eval's own P2 check. Invented
places throughout.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

from tests.evals import genie_outcomes as go

WEDNESDAY = datetime(2026, 10, 7, 9, 0)


def _p2(text: str) -> list[str]:
    cr = go.CaseResult(id="pin", text=text)
    go.score({"id": "pin", "when": "2026-10-07T09:00", "expect": {"outcomes": ["P2"]}}, cr)
    return cr.reasons


def _day(lines: list[str], name: str = "Sunday") -> str:
    return "\n".join([f"*{name}*", *lines])


@pytest.fixture
def lp():
    from agents.genie import live_plan
    return live_plan


def test_an_afternoon_outing_moves_the_home_block_to_the_evening(lp):
    lines, _, _ = lp.sequence_day([lp.LiveOption(time="afternoon", activity="Science museum")],
                                  energy="low", protect_nap=False)
    assert lines == ["  • Afternoon (3:30–5:30): Science museum",
                     "  • Evening (6:00–9:00): home — early wind-down"]
    assert _p2(_day(lines)) == []


def test_an_evening_outing_comes_after_the_home_block(lp):
    lines, _, _ = lp.sequence_day([lp.LiveOption(time="evening", activity="Night market")],
                                  energy="low", protect_nap=False)
    assert lines == ["  • Afternoon (3:30–5:30): home — quiet play, a rest before the evening",
                     "  • Evening (6:00–9:00): Night market"]
    assert _p2(_day(lines)) == []


def test_a_morning_outing_keeps_the_day_it_always_had(lp):
    lines, _, _ = lp.sequence_day([lp.LiveOption(time="morning", activity="Farm visit")],
                                  energy="low", protect_nap=True)
    assert lines == ["  • Morning (9:00–11:30): Farm visit",
                     "  • Midday (12:30–3:00): naps protected at home (toddler/newborn)",
                     "  • Afternoon (3:30–5:30): home — quiet play, early wind-down"]


def test_a_second_find_for_a_slot_is_an_alternate(lp):
    opts = [lp.LiveOption(time="morning", activity="Farm visit"),
            lp.LiveOption(time="morning", activity="Creek walk"),
            lp.LiveOption(time="afternoon", activity="Puppet show")]
    lines, alternates, violations = lp.sequence_day(opts, energy="medium", protect_nap=False)
    assert [a.activity for a in alternates] == ["Creek walk"] and violations == []
    assert lines == ["  • Morning (9:00–11:30): Farm visit", "  • Afternoon (3:30–5:30): Puppet show"]
    assert _p2(_day(lines)) == []


# ── the plan card and a swap, end to end ──────────────────────────────
_DISCOVERY = {
    "weather": {"saturday": "mild", "sunday": "sunny"},
    "options": {
        "saturday": [
            {"time": "morning", "activity": "Farm visit", "place": "Hill Farm", "why": "animals", "source": "farm site"},
            {"time": "afternoon", "activity": "Puppet show", "place": "Town Hall", "why": "indoor", "source": "hall site"},
        ],
        "sunday": [
            {"time": "afternoon", "activity": "Science museum", "place": "Discovery Hall", "why": "cool inside",
             "source": "museum site"},
        ],
    },
}


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


def test_the_plan_card_holds_one_item_per_slot(genie):
    out = genie.handle_weekend_plan(now=WEDNESDAY, commit=False, llm=lambda p: json.dumps(_DISCOVERY))
    sunday = out.split("*Sunday*", 1)[1].split("\n\n", 1)[0]
    assert "Afternoon (3:30–5:30): Science museum" in sunday
    assert "Evening (6:00–9:00): home — early wind-down" in sunday
    assert _p2(out) == [], out


def test_a_swapped_in_afternoon_outing_moves_the_home_block(genie):
    from agents.genie import live_plan, state
    genie.handle_weekend_plan(now=WEDNESDAY, llm=lambda p: json.dumps(_DISCOVERY),
                              audience_text="protect the naps")
    out = genie.route("swap in puppet show")
    assert "Swapped in" in out and "✅ Plan saved." in out, out
    saturday = state.latest_weekend_plan().saturday
    assert saturday == ["Midday (12:30–3:00): naps protected at home (toddler/newborn)",
                        "Afternoon (3:30–5:30): Puppet show at Town Hall — indoor (hall site)",
                        "Evening (6:00–9:00): home — early wind-down"], saturday
    slots = [live_plan.slot_of(ln) for ln in saturday]
    assert slots == ["midday", "afternoon", "evening"]
    assert _p2(_day([f"  • {ln}" for ln in saturday], "Saturday")) == []
    # The farm visit went back to the pool as the morning outing it was.
    back = [a for a in state.last_alternates() if a.get("activity") == "Farm visit"]
    assert back and back[0]["time"] == "morning", state.last_alternates()


def test_a_swap_replaces_the_outing_in_its_own_slot(genie):
    from agents.genie import state
    opts = {"weather": {}, "options": {"saturday": [
        {"time": "morning", "activity": "Farm visit", "place": "Hill Farm"},
        {"time": "afternoon", "activity": "Puppet show", "place": "Town Hall"},
        {"time": "afternoon", "activity": "Kite day", "place": "Meadow"},
    ], "sunday": []}}
    genie.handle_weekend_plan(now=WEDNESDAY, llm=lambda p: json.dumps(opts), energy_override="medium")
    genie.route("swap in kite day")
    saturday = state.latest_weekend_plan().saturday
    assert saturday == ["Morning (9:00–11:30): Farm visit at Hill Farm",
                        "Afternoon (3:30–5:30): Kite day at Meadow"], saturday
