"""Regression (2026-08-09, live) — free-form proposals are captured, not
hijacked.

THE INCIDENT (first real household message, 09:49 PT). The spouse's
opening message was a six-weekend plan proposal ending with a Friday
date-night list. Two failures at once:

  1. HIJACK: keyword intents fired on words buried in the text —
     "those adults only exp in Japan" matched the couple-only route and
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
Alameda - can ask Ravi uncle and fam to join

August 22nd: Mt. Madonna

Husband and wife outing - Every Friday
1. Mt Hamilton for a drive followed by Mexican dinner in SJ area
2. Midnight Tea in SF
3. let's find some hot spring kinda experience - those adults only exp in Japan that we missed out on."""


def _next_saturday_iso() -> str:
    # Anchored, not wall-clock (datefreeze convention): computed from the
    # live incident's Sunday. Wall-clock next-Saturday collided with the
    # fixture's hardcoded 2026-08-22 once real time reached the week of
    # 08-17, making the merge test's two weekends the same weekend.
    now = datetime(2026, 8, 9, 12, 0)
    return (now + timedelta(days=(5 - now.weekday()) % 7)).strftime("%Y-%m-%d")


def _elicited() -> dict:
    return {
        "weekends": [
            {"weekend_of": _next_saturday_iso(), "label": "Alameda",
             "items": ["Alameda outing"],
             "companions": "Ravi uncle and fam", "notes": ""},
            {"weekend_of": "2026-08-22", "label": "Mt. Madonna",
             "items": ["Mt. Madonna"], "companions": "", "notes": ""},
        ],
        "date_nights": [
            "Mt Hamilton drive + Mexican dinner in SJ",
            "Midnight Tea in SF",
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
    # Freeze to the live incident's date (Sun 2026-08-09, datefreeze
    # convention) so "next Saturday" resolves to 08-15 in both the fixture
    # helper and the plan sequencer — rollover collision caught 2026-08-17.
    from tests.datefreeze import freeze
    from datetime import date as _date
    freeze(monkeypatch, _date(2026, 8, 9),
           modules=("agents.genie.handler", "agents.genie.calendar",
                    "agents.genie.concierge", "agents.genie.protocols",
                    "agents.genie.live_plan", "agents.genie.ideas"))
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
    assert "Mt. Madonna" in out
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
    assert "Alameda" in out
    assert "Ravi uncle and fam" in out
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
    assert "Mt Hamilton drive" in out


# ─────────────────── never lose their words ───────────────────
def test_elicitation_failure_saves_raw_text(genie, monkeypatch):
    from agents.genie import ideas
    monkeypatch.setattr(ideas, "elicit",
                        lambda text, today_iso, llm=None: None)
    out = genie.route(LIVE_MESSAGE)
    assert "word-for-word" in out
    from agents.genie import state
    notes = state.household_ideas().get("notes", [])
    assert any("Mt Hamilton" in n for n in notes)


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
    revised = {
        "weekends": [{"weekend_of": "2026-08-22", "label": "Big Basin",
                      "items": ["Big Basin instead"], "companions": "",
                      "notes": ""}],
        "date_nights": ["Midnight Tea in SF",        # dup — must not double
                        "Lands End sunset hike"],
        "notes": [],
    }
    state.save_household_ideas(revised, by_role="primary")
    ideas = state.household_ideas()
    aug22 = [w for w in ideas["weekends"]
             if w.get("weekend_of") == "2026-08-22"]
    assert len(aug22) == 1 and aug22[0]["label"] == "Big Basin"
    dn = ideas["date_nights"]
    assert dn.count("Midnight Tea in SF") == 1
    assert "Lands End sunset hike" in dn


def test_elicit_module_never_raises():
    from agents.genie import ideas

    def _boom(prompt):
        raise RuntimeError("wire down")

    assert ideas.elicit("some text", today_iso="2026-08-09",
                        llm=_boom) is None
    assert ideas.elicit("some text", today_iso="2026-08-09",
                        llm=lambda p: "not json") is None
