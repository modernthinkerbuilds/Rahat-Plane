"""Feature pin (2026-08-10) — the Genie concierge (model-first layer).

Owner verdict on the deterministic surface, live: "Genie is failing,
it's likely too deterministic … I want it to start asking how many
people, who all, what time will you start, what time do you want to be
back, what are your preferences … then build a plan … I want it to be
probabilistic … forget toddler sleep time unless I say."

Same arc as the Miya plane (ADR-013): dispatcher → reasoner. The pins:

  * The LLM drives the conversation: missing slots → it ASKS (party,
    timing, preferences); enough known → GROUNDED search + a timed
    plan inside the stated window.
  * Session continuity per chat: slots gathered across turns persist
    (TTL'd) so the second answer doesn't restart the interview.
  * The composed plan is charter-gated on save, and the deterministic
    iteration surface (swap / why-not / replan) keeps working on it.
  * Failure ladder: concierge unavailable (hermetic, flag off, LLM
    down, garbage JSON) → deterministic fallback routes, never a crash,
    never empty.
  * Nap guard: opt-in everywhere (also pinned in the journeys file) —
    the concierge may ask about naps; nothing imposes them.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler, concierge
    importlib.reload(state)
    importlib.reload(handler)
    importlib.reload(concierge)
    return handler


_NOW = datetime(2026, 8, 14, 18, 0)     # a Friday evening

_ASK = {"mode": "ask",
        "reply": ("Sounds fun! Three quick ones: who's coming (how many "
                  "of you)? When do you want to head out and be back by? "
                  "And what's the mood — hiking, dinner, sightseeing?"),
        "slots": {"date": "2026-08-15"}}

_PLAN_DECISION = {"mode": "plan", "reply": "",
                  "slots": {"party": "2 adults + toddler",
                            "start_time": "9 AM",
                            "return_time": "4 PM",
                            "preferences": "short hike then good lunch",
                            "date": "2026-08-15"},
                  "search_brief": ("Sat 2026-08-15, 2 adults + toddler, "
                                   "9am-4pm from Testville CA, short hike "
                                   "+ great lunch")}

_PLAN = {"title": "Saturday Aug 15 — hike & lunch",
         "timeline": [
             {"time": "9:15 AM", "what": "Creekside loop hike",
              "where": "Alum Rock Park", "why": "shaded, toddler-doable",
              "source": "sanjoseca.gov"},
             {"time": "12:15 PM", "what": "Lunch",
              "where": "Luna Mexican Kitchen", "why": "patio, fast with kids",
              "source": "lunamexicankitchen.com"},
             {"time": "2:00 PM", "what": "Gelato + stroll",
              "where": "Santana Row", "why": "easy wind-down",
              "source": "santanarow.com"}],
         "notes": ["Parking fills by 10 at Alum Rock — arrive early"],
         "backups": [{"what": "Japanese Friendship Garden",
                      "where": "Kelley Park", "source": "sj.gov"}]}


def _script(*responses):
    """An LLM seam that plays scripted JSON responses in order."""
    queue = list(responses)

    def _llm(prompt: str) -> str:
        return json.dumps(queue.pop(0)) if queue else json.dumps(_ASK)

    return _llm


# ─────────────── the interview → plan loop ───────────────
def test_concierge_asks_the_right_questions_first(genie):
    from agents.genie import concierge
    out = concierge.step("111", "plan something for tomorrow",
                         now=_NOW, llm=_script(_ASK))
    assert "who's coming" in out
    assert "be back" in out
    assert "hiking" in out


def test_slots_persist_across_turns(genie):
    from agents.genie import concierge
    concierge.step("111", "plan something for tomorrow",
                   now=_NOW, llm=_script(_ASK))
    seen = {}

    def _spy(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps(_ASK)

    concierge.step("111", "the three of us, out by 9",
                   now=_NOW, llm=_spy)
    assert '"date": "2026-08-15"' in seen["prompt"]       # carried over
    assert "plan something for tomorrow" in seen["prompt"]  # history too


def test_sessions_are_per_chat(genie):
    from agents.genie import concierge
    concierge.step("111", "plan something", now=_NOW, llm=_script(_ASK))
    seen = {}

    def _spy(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps(_ASK)

    concierge.step("222", "hello", now=_NOW, llm=_spy)
    assert "plan something for tomorrow" not in seen.get("prompt", "")


def test_enough_slots_produces_grounded_timed_plan(genie):
    from agents.genie import concierge
    out = concierge.step(
        "111", "2 adults and the toddler, 9 to 4, hike then a good lunch",
        now=_NOW, llm=_script(_PLAN_DECISION), search_llm=_script(_PLAN))
    assert "9:15 AM" in out and "Creekside loop hike" in out
    assert "Luna Mexican Kitchen" in out
    assert "Backups" in out
    assert "Parking fills by 10" in out
    assert "✅ Plan saved" in out                          # charter-gated


def test_concierge_plan_feeds_the_iteration_surface(genie):
    from agents.genie import concierge, state
    concierge.step("111", "just plan it", now=_NOW,
                   llm=_script(_PLAN_DECISION), search_llm=_script(_PLAN))
    plan = state.latest_weekend_plan()
    assert plan is not None
    assert any("Alum Rock" in l for l in plan.saturday)
    assert "concierge plan" in plan.notes


def test_household_context_reaches_the_model(genie):
    from agents.genie import concierge
    seen = {}

    def _spy(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps(_ASK)

    concierge.step("111", "hi", now=_NOW, llm=_spy)
    assert "Toddler" in seen["prompt"]                    # roster present
    assert "Testville, CA" in seen["prompt"]              # location present


# ─────────────── failure ladder ───────────────
def test_plan_search_failure_degrades_gracefully(genie):
    from agents.genie import concierge
    out = concierge.step("111", "just plan it", now=_NOW,
                         llm=_script(_PLAN_DECISION),
                         search_llm=lambda p: "not json at all")
    assert "couldn't pull live options" in out
    assert "/weekend_plan" in out                          # escape hatch


def test_unparseable_conversation_returns_none_for_fallback(genie):
    from agents.genie import concierge
    assert concierge.step("111", "hi", now=_NOW,
                          llm=lambda p: "garbage") is None


def test_hermetic_no_seam_returns_none(genie):
    from agents.genie import concierge
    assert concierge.step("111", "hi", now=_NOW) is None


def test_route_falls_back_to_deterministic_when_unavailable(genie):
    """Under RAHAT_TEST_MODE with no seam the concierge yields None and
    the old deterministic surface answers — nothing crashes, nothing
    goes silent."""
    out = genie.route("Weekend_plan")
    assert "Weekend plan — week of" in out                # old path alive


def test_route_uses_concierge_when_available(genie, monkeypatch):
    from agents.genie import concierge
    monkeypatch.setattr(concierge, "step",
                        lambda cid, msg, **kw: "🧞 concierge says hi")
    out = genie.route("what should we do tomorrow?", chat_id="111")
    assert out == "🧞 concierge says hi"


def test_flag_off_skips_concierge(genie, monkeypatch):
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    from agents.genie import concierge
    monkeypatch.setattr(concierge, "step",
                        lambda cid, msg, **kw: "🧞 should not appear")
    out = genie.route("Weekend_plan")
    assert "🧞" not in out
