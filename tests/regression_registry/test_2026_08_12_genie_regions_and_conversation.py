"""Feature + bug pins (2026-08-12) — regions everywhere, conversations
that finish, destination-aware plans.

TWO LIVE FAILURES, one owner directive.

Failure 1 (Telegram, 2026-08-12 00:11): mid-concierge, the owner
answered Genie's three numbered questions with a three-line numbered
reply — and _is_freeform's ≥2-newline check fired, capture hijacked
the ANSWER as a "proposal", and the conversation died with no plan.
Fix: an active concierge session claims the next non-slash message
BEFORE the freeform preempt.

Failure 2: every discovered event and every plan stop was San Jose.
The registry was South-Bay-only and the plan prompt anchored on the
home area. Owner: "I want genie to recommend plans everywhere —
northern SF, SF, East Bay, South Bay, Marin to Walnut Creek, Moraga
to Big Sur … make plans based on the place I want to go, hour by
hour, e.g. if there's something in Marin."

THE PINS.
  * route(): active session → concierge gets the message first; the
    numbered answer is NOT captured. Slash commands still win (exit
    hatch). Concierge down (None) → the old fallthrough still captures
    (words are never lost).
  * has_active_session: false when fresh, true mid-conversation,
    false after TTL expiry.
  * Registry: one anchor source per region — SF, North Bay/Marin,
    East Bay (Oakland/Berkeley + Walnut Creek/Lamorinda), Peninsula,
    Santa Cruz, Monterey/Big Sur — each region-tagged.
  * Conversation prompt: destination is a slot; the whole Bay Area +
    coast is stated range; never silently substitute the home area;
    ask budget capped at 2 rounds (the conversation must END in a
    plan, not a fourth question).
  * Plan prompt: named destination → every stop AT the destination,
    hour-by-hour, drive-out first / drive-home last, lands before
    be-back-by; no destination → home area with permission to roam.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta

import pytest

_NOW = datetime(2026, 8, 12, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("GENIE_PRIMARY_CHAT", "111")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler, concierge
    importlib.reload(state)
    importlib.reload(handler)
    importlib.reload(concierge)
    return tmp_path


def _ask(reply="And what time will you head out?"):
    return json.dumps({"mode": "ask", "reply": reply,
                       "slots": {"party": "whole family"}})


def _seed_session(chat_id="111", now=None):
    """Start a live concierge conversation (Genie has asked a question).
    Defaults to the REAL clock because route() checks session freshness
    against datetime.now() — a frozen-clock seed would look expired."""
    from agents.genie import concierge
    out = concierge.step(chat_id, "plan something for Saturday",
                         now=now or datetime.now(), llm=lambda p: _ask())
    assert out and "what time" in out.lower()


# ───────────────── the mid-conversation hijack (bug) ─────────────────
def test_numbered_answer_continues_the_conversation_not_capture(
        env, monkeypatch):
    _seed_session()
    from agents.genie import handler, concierge
    # The concierge layer is up: route() must hand the multi-line
    # numbered ANSWER to it, not to freeform capture.
    monkeypatch.setattr(
        concierge, "_llm_call",
        lambda prompt, *, search, llm: _ask("Got it — one more thing?"))
    out = handler.route("1 lively market or festival\n"
                        "2. Nice to have dedicated stop\n"
                        "3. Doesn't matter", chat_id="111")
    assert "Captured" not in out, "answer hijacked by capture (the bug)"
    assert "one more thing" in out


def test_slash_commands_still_win_over_an_active_session(env, monkeypatch):
    _seed_session()
    from agents.genie import handler, concierge
    monkeypatch.setattr(
        concierge, "_llm_call",
        lambda prompt, *, search, llm: _ask("should not be reached"))
    out = handler.route("/family", chat_id="111")
    assert "should not be reached" not in out


def test_concierge_down_still_never_loses_their_words(env):
    _seed_session()
    from agents.genie import handler
    # Hermetic + no seam through route() → concierge returns None →
    # the freeform fallthrough captures. Degraded, but nothing lost.
    out = handler.route("1 lively market\n2. dedicated stop\n3. whatever",
                        chat_id="111")
    assert out and len(out) > 0


def test_has_active_session_lifecycle(env):
    from agents.genie import concierge
    real_now = datetime.now()
    assert concierge.has_active_session("111", now=real_now) is False
    _seed_session(now=real_now)
    assert concierge.has_active_session("111", now=real_now) is True
    assert concierge.has_active_session(
        "111", now=real_now + timedelta(hours=3)) is False   # TTL expired
    assert concierge.has_active_session("999", now=real_now) is False


# ───────────────── regions in the event finder ─────────────────
def test_registry_covers_the_whole_bay_and_coast(env):
    from bridges.events import registry
    importlib.reload(registry)
    by_id = {s["id"]: s for s in registry.load_sources()}
    expected = {"funcheap-sf": "sf", "sf-rec-parks": "sf",
                "marin-events": "north-bay",
                "eastbay-510": "east-bay", "eastbay-diablo": "east-bay",
                "peninsula-events": "peninsula",
                "santa-cruz": "santa-cruz",
                "monterey-bigsur": "monterey"}
    for sid, region in expected.items():
        assert sid in by_id, f"missing regional source {sid}"
        assert by_id[sid].get("region") == region
    # The owner's originals are still there.
    assert {"linden-tree", "home-depot-kids", "sjpl"} <= set(by_id)
    # Walnut Creek / Moraga / Big Sur are literally in scope.
    blob = json.dumps(list(by_id.values())).lower()
    for place in ("walnut creek", "moraga", "big sur", "marin",
                  "santa cruz", "berkeley"):
        assert place in blob, f"{place} not covered by any source"


# ───────────────── destination-aware planning ─────────────────
def test_conversation_prompt_owns_destination_and_ask_budget(env):
    from agents.genie import concierge
    p = concierge._conversation_prompt("ctx", [], {}, "hi", _NOW)
    assert "destination" in p
    assert "never silently substitute the home area" in p
    assert 'at most 2 "ask" rounds' in p
    assert "Monterey/Carmel/Big Sur" in p


def test_plan_prompt_builds_the_day_at_the_destination(env):
    from agents.genie import concierge
    p = concierge._plan_prompt("ctx", {"destination": "Marin"},
                               "family day", _NOW)
    assert "DESTINATION: Marin" in p
    assert "drive out from the home area" in p
    assert "drive home" in p and "be-back-by" in p
    assert "HOUR-BY-HOUR" in p


def test_plan_prompt_without_destination_stays_local_but_may_roam(env):
    from agents.genie import concierge
    p = concierge._plan_prompt("ctx", {}, "family day", _NOW)
    assert "No destination named" in p
    assert "DESTINATION:" not in p


def test_destination_flows_from_conversation_into_the_search(env):
    """End-to-end: the conversation says plan-in-Marin; the grounded
    search prompt must carry Marin as the destination."""
    from agents.genie import concierge
    seen = {}

    def conversation_llm(prompt):
        return json.dumps({
            "mode": "plan", "reply": "",
            "slots": {"destination": "Marin", "party": "family",
                      "date": "2026-08-15"},
            "search_brief": "family Saturday in Marin 10am-8pm"})

    def search_llm(prompt):
        seen["plan_prompt"] = prompt
        return json.dumps({"title": "Marin day",
                           "timeline": [{"time": "9:00 AM",
                                         "what": "Drive to Marin",
                                         "where": "US-101 N"}],
                           "notes": [], "backups": []})

    out = concierge.step("111", "somewhere in Marin on Saturday",
                         now=_NOW, llm=conversation_llm,
                         search_llm=search_llm)
    assert "Marin" in out
    assert "DESTINATION: Marin" in seen["plan_prompt"]
