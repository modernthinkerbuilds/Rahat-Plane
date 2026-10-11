"""Regression (2026-08-09, live) — free-form proposals are captured, not
hijacked.

THE INCIDENT (first real household message, 09:49 PT). The spouse's
opening message was a six-weekend plan proposal ending with a Friday
date-night list. Two failures at once:

  1. HIJACK: keyword intents fired on words buried in the text —
     "those adults only exp abroad" matched the couple-only route and
     "high level thoughts" matched the energy override — so Genie
     answered a rich human proposal with a wrong date-night plan.
  2. NO CAPTURE PATH: the message was PRD core-loop step 4 (the humans
     hand back a rough idea) and Genie had no way to receive it.

THE PINS.
  * A long / multi-line non-command message NEVER routes to keyword
    intents — it is captured (LLM elicits typed structure; the save is
    deterministic + charter-gated; the raw text is never lost).
  * Captured weekends anchor /weekend_plan for that weekend ("Your plan
    on file" renders before discovery); the date-night rotation feeds
    date-night plans.
  * Elicitation failure falls back to storing the words verbatim —
    capture must never lose the humans' input.
  * Short commands still work exactly as before.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta

import pytest


# Abridged but faithful to the live message — keeps both hijack traps
# ("high level thoughts", "adults only") and the multi-weekend shape.
LIVE_MESSAGE = """Here are some high level thoughts for us

Subject to your approval :
With parents and kids :
Aug 16th weekend:
Lake Town - can ask the cousins and fam to join

August 22nd: Pine Ridge

Husband and wife outing - Every Friday
1. Hill road for a drive followed by taco dinner downtown
2. Late tea in the city
3. let's find some hot spring kinda experience - those adults only exp abroad that we missed out on."""


def _next_saturday_iso(weeks_out: int = 0) -> str:
    """Rollover-proof date helper. NEVER hardcode a weekend date in this
    file: on 2026-08-17 the dynamic 'next Saturday' rolled onto a
    hardcoded '2026-08-22' and two entries collided (the exact
    date-rollover failure class the 2026-08-01 re-entry audit found in
    the May tests)."""
    now = datetime.now()
    days = (5 - now.weekday()) % 7 + 7 * weeks_out
    return (now + timedelta(days=days)).strftime("%Y-%m-%d")


def _elicited() -> dict:
    return {
        "weekends": [
            {"weekend_of": _next_saturday_iso(), "label": "Lake Town",
             "items": ["Lake Town outing"],
             "companions": "the cousins and fam", "notes": ""},
            {"weekend_of": _next_saturday_iso(1), "label": "Pine Ridge",
             "items": ["Pine Ridge"], "companions": "", "notes": ""},
        ],
        "date_nights": [
            "Hill road drive + taco dinner downtown",
            "Late tea in the city",
            "Hot spring experience",
        ],
        "notes": ["Subject to approval"],
    }


def _fake_elicit_llm(prompt: str) -> str:
    return json.dumps(_elicited())


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    from tests.datefreeze import off_sunday
    off_sunday(monkeypatch)           # a weekday-shaped pin (QA-2026-10-04-07)
    return handler


# ─────────────────── the hijack can never recur ───────────────────
def test_live_message_is_captured_not_hijacked(genie, monkeypatch):
    from agents.genie import ideas
    monkeypatch.setattr(ideas, "elicit",
                        lambda text, today_iso, llm=None: _elicited())
    out = genie.route(LIVE_MESSAGE)
    assert "Captured your plan" in out
    # The two live mis-routes must be impossible:
    assert "Date night" not in out            # 'adults only' trap
    assert "(energy: high)" not in out        # 'high level thoughts' trap
    assert "Childcare" not in out
    # And the content actually landed:
    assert "Pine Ridge" in out
    assert "date-night rotation: 3 ideas" in out


def test_freeform_guard_is_about_shape_not_keywords(genie, monkeypatch):
    """Short, command-ish asks still route to their intents."""
    out = genie.route("plan a date night saturday just us")
    assert "Date night" in out or "Weekend plan" in out


# ─────────────────── capture → anchor the next plan ───────────────────
def test_captured_weekend_anchors_weekend_plan(genie, monkeypatch):
    from agents.genie import ideas
    monkeypatch.setattr(ideas, "elicit",
                        lambda text, today_iso, llm=None: _elicited())
    genie.route(LIVE_MESSAGE)

    fam = {"weather": {"saturday": "sunny", "sunday": "mild"},
           "options": {"saturday": [
               {"time": "morning", "activity": "Farmers market",
                "place": "Main St", "why": "strollable", "source": "cm"}],
               "sunday": []}}
    out = genie.handle_weekend_plan(llm=lambda p: json.dumps(fam))
    assert "Your plan on file" in out
    assert "Lake Town" in out
    assert "the cousins and fam" in out
    assert "discovery adds around it" in out
    assert "Farmers market" in out            # discovery still decorates


def test_rotation_feeds_date_night(genie, monkeypatch):
    from agents.genie import ideas
    monkeypatch.setattr(ideas, "elicit",
                        lambda text, today_iso, llm=None: _elicited())
    genie.route(LIVE_MESSAGE)
    eve = {"weather": {"saturday": "clear", "sunday": "mild"},
           "options": {"saturday": [
               {"time": "evening", "activity": "Wine bar",
                "place": "Vintage Lane", "why": "quiet", "source": "vl"}],
               "sunday": []}}
    out = genie.handle_weekend_plan(llm=lambda p: json.dumps(eve),
                                    audience_text="just us tonight")
    assert "Your date-night rotation" in out
    assert "Hill road drive" in out


# ─────────────────── never lose their words ───────────────────
def test_elicitation_failure_saves_raw_text(genie, monkeypatch):
    from agents.genie import ideas
    monkeypatch.setattr(ideas, "elicit",
                        lambda text, today_iso, llm=None: None)
    out = genie.route(LIVE_MESSAGE)
    assert "word-for-word" in out
    from agents.genie import state
    notes = state.household_ideas().get("notes", [])
    assert any("Hill road" in n for n in notes)


def test_capture_is_charter_gated(genie, monkeypatch):
    from agents.genie.protocols import KIND_IDEAS_CAPTURE, ALL_CHARTER_KINDS
    assert KIND_IDEAS_CAPTURE in ALL_CHARTER_KINDS
    from agents.genie import state
    ok, reason = state.save_household_ideas(_elicited(), by_role="spouse")
    assert ok


# ─────────────────── merge semantics ───────────────────
def test_recapture_updates_same_weekend_and_dedups_rotation(genie):
    from agents.genie import state
    state.save_household_ideas(_elicited(), by_role="spouse")
    wk2 = _next_saturday_iso(1)
    revised = {
        "weekends": [{"weekend_of": wk2, "label": "Fern Hollow",
                      "items": ["Fern Hollow instead"], "companions": "",
                      "notes": ""}],
        "date_nights": ["Late tea in the city",        # dup — must not double
                        "Sunset hike on the bluffs"],
        "notes": [],
    }
    state.save_household_ideas(revised, by_role="primary")
    ideas = state.household_ideas()
    aug22 = [w for w in ideas["weekends"]
             if w.get("weekend_of") == wk2]
    assert len(aug22) == 1 and aug22[0]["label"] == "Fern Hollow"
    dn = ideas["date_nights"]
    assert dn.count("Late tea in the city") == 1
    assert "Sunset hike on the bluffs" in dn


def test_elicit_module_never_raises():
    from agents.genie import ideas

    def _boom(prompt):
        raise RuntimeError("wire down")

    assert ideas.elicit("some text", today_iso="2026-08-09",
                        llm=_boom) is None
    assert ideas.elicit("some text", today_iso="2026-08-09",
                        llm=lambda p: "not json") is None
