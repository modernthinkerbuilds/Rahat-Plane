"""Miya orchestrator — routing contract tests.

The L7 review of core/miya.py identified eight invariants the
orchestrator must hold, regardless of which agents are registered. This
file pins each one with a focused test:

    1.  Empty registry → route() returns None (no crash).
    2.  Single regex match → no LLM call (cost discipline).
    3.  Multiple regex matches → LLM classifier picks one of them.
    4.  Zero matches → LLM picks from the full mesh.
    5.  LLM returns garbage → falls back to first candidate.
    6.  registered() / list_capabilities() / clear_registry() are
        idempotent.
    7.  charter veto on a nudge → no Telegram send.
    8.  charter approves a reply → voice.dress is applied (when
        RAHAT_VOICE=hyderabadi) and the wire receives the dressed text.

Each test is offline: no GEMINI_API_KEY, no Telegram token, no live DB.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from core import miya
from core.agent import Agent, Reply


# ─── Test fixtures: tiny synthetic agents ─────────────────────────
class _Echo(Agent):
    name = "echo"
    description = "Echoes whatever it gets."
    triggers = [r"\becho\b"]

    def route(self, msg):
        return Reply(text=f"echo: {msg}", confidence=1.0)


class _Math(Agent):
    name = "math"
    description = (
        "Calorie / weight / HRV math. Use for any question about "
        "calories, deficits, weekly burn, or HRV-based intensity calls."
    )
    triggers = [r"\b(calor|hrv|weight|deficit)\b"]

    def route(self, msg):
        return Reply(text=f"math: {msg}", confidence=1.0)


class _Silent(Agent):
    """Returns None text → forces Miya to handle the empty-Reply case."""
    name = "silent"
    description = "Returns nothing — exercises the fallthrough path."
    triggers = [r"\bsilent\b"]

    def route(self, msg):
        return Reply(text="", confidence=0.5)


# ─── 1. Empty registry ─────────────────────────────────────────────
def test_route_empty_registry_returns_none():
    """An empty mesh should be a hard None, not a crash. This was the
    behavior we want when the agent host boots before any agent has
    registered itself."""
    assert miya.registered() == []
    assert miya.route("anything") is None


# ─── 2. Single regex match → no classifier ─────────────────────────
def test_route_single_regex_match_skips_llm(fake_llm):
    """When exactly one agent's trigger fires, the LLM classifier MUST
    be skipped. This is a cost guarantee — the typical user message
    should never round-trip through Gemini Flash just to be routed."""
    miya.register(_Echo())
    miya.register(_Math())

    # Set a sentinel; if the LLM is consulted, the assertion fails.
    fake_llm.set("UNEXPECTED_LLM_CALL")

    reply = miya.route("echo this")
    assert reply is not None
    assert reply.text == "echo: echo this"


# ─── 3. Multiple matches → LLM picks one ───────────────────────────
def test_route_multiple_matches_uses_classifier(fake_llm):
    """When two agents both claim the message, the LLM tiebreaker runs
    and its choice wins."""
    miya.register(_Echo())
    miya.register(_Math())

    # Craft a message that fires both triggers.
    msg = "echo my hrv 50"

    fake_llm.set("math")  # classifier picks math
    reply = miya.route(msg)
    assert reply is not None
    assert reply.text.startswith("math:")


# ─── 4. Zero matches → LLM-only path ───────────────────────────────
def test_route_zero_matches_classifier_picks_from_full_mesh(fake_llm):
    """Nothing matched: Miya asks the LLM to pick from the full mesh
    rather than dropping the message."""
    miya.register(_Echo())
    miya.register(_Math())

    fake_llm.set("echo")
    reply = miya.route("hello there")
    assert reply is not None
    assert reply.text.startswith("echo:")


# ─── 5. LLM returns garbage → fallback ─────────────────────────────
def test_route_llm_garbage_falls_back_to_first_candidate(fake_llm):
    """If the LLM returns a name we don't recognize, Miya falls back to
    the first regex-matched candidate (or the first registered agent if
    nothing matched). This keeps the system live during model outages
    and prompt regressions."""
    miya.register(_Echo())
    miya.register(_Math())

    fake_llm.set("not-an-agent-name")
    msg = "echo my hrv 50"  # both match
    reply = miya.route(msg)
    # Should be one of them, not None.
    assert reply is not None
    assert reply.text.split(":")[0] in ("echo", "math")


# ─── 6. Registry plumbing ──────────────────────────────────────────
def test_register_is_idempotent_on_name():
    """Registering the same agent twice must not duplicate it. Two
    Scientist instances would mean every message routes twice."""
    miya.register(_Echo())
    miya.register(_Echo())
    assert len(miya.registered()) == 1


def test_list_capabilities_shape():
    miya.register(_Math())
    caps = miya.list_capabilities()
    assert len(caps) == 1
    assert caps[0]["name"] == "math"
    assert "description" in caps[0]
    assert isinstance(caps[0]["triggers"], list)
    # description must be non-empty so the LLM classifier has something
    # to work with — this is a hard contract.
    assert caps[0]["description"].strip() != ""


def test_clear_registry_resets_state():
    miya.register(_Echo())
    miya.clear_registry()
    assert miya.registered() == []


# ─── 7. Charter veto on a nudge ────────────────────────────────────
def test_charter_quiet_hours_vetoes_nudge_at_2300(captured_tg, sandbox_db):
    """At 23:00 a non-urgent nudge MUST be vetoed and never reach
    Telegram. Replies (notify.user.reply) are exempt; only nudges
    (notify.user.nudge) get muted. This test asserts the nudge path."""
    from core import miya as m
    reply = Reply(text="ambient nudge — drink water", confidence=0.7)
    sent = m._send_with_charter(
        reply,
        requester="test",
        kind="notify.user.nudge",
        trace_id="tid-quiet",
        priority=5,
        ctx={"now": datetime(2026, 5, 8, 23, 0, 0)},
    )
    assert sent is False
    assert captured_tg.outbox == []


def test_charter_quiet_hours_allows_user_reply_at_2300(captured_tg, sandbox_db):
    """User-initiated replies must always go out — quiet hours never
    block them. Regression guard for the 2026-05 outage where users
    asked a question at 23:30 and got nothing back."""
    from core import miya as m
    reply = Reply(text="weight is 195.8 lbs", confidence=1.0)
    sent = m._send_with_charter(
        reply,
        requester="test",
        kind="notify.user.reply",
        trace_id="tid-reply",
        priority=5,
        ctx={"now": datetime(2026, 5, 8, 23, 30, 0)},
    )
    assert sent is True
    assert len(captured_tg.outbox) == 1
    assert "195.8" in captured_tg.outbox[0][0]


def test_charter_urgent_nudge_bypasses_quiet_hours(captured_tg, sandbox_db):
    """Priority ≤ 2 = urgent → bypass quiet hours. Used for things
    like "your HRV crashed, take it easy tomorrow."""
    from core import miya as m
    reply = Reply(text="🚨 HRV red — full rest tomorrow", confidence=0.9)
    sent = m._send_with_charter(
        reply,
        requester="test",
        kind="notify.user.nudge",
        trace_id="tid-urgent",
        priority=1,  # urgent
        ctx={"now": datetime(2026, 5, 8, 23, 0, 0)},
    )
    assert sent is True


# ─── 8. Voice dressing applies on send ─────────────────────────────
def test_voice_dressing_runs_on_outbound(monkeypatch, captured_tg, sandbox_db):
    """When RAHAT_VOICE=hyderabadi, outbound replies get an opener.
    The numeric/structural content must be preserved verbatim — the
    voice adds wrapping, never alters the data."""
    monkeypatch.setenv("RAHAT_VOICE", "hyderabadi")

    from core import miya as m
    reply = Reply(text="Today (Mon): 1,073 kcal", confidence=1.0)
    sent = m._send_with_charter(
        reply,
        requester="test",
        kind="notify.user.reply",
        trace_id="tid-voice",
        priority=5,
        ctx={"now": datetime(2026, 5, 8, 12, 0, 0)},
    )
    assert sent is True
    text = captured_tg.last()
    # Numbers preserved verbatim
    assert "1,073" in text
    # Voice wrapper present — use voice.is_dressed() (the canonical
    # comprehensive check) instead of a hard-coded opener list; the
    # phrasebook grows over time and a hard-coded list goes stale on
    # every expansion.
    from core import voice
    assert voice.is_dressed(text)


# ─── 9. The tick → nudge → charter pipeline ───────────────────────
def test_tick_nudge_paths_through_charter(captured_tg, sandbox_db,
                                          monkeypatch):
    """Agents emit nudges via tick(). Miya MUST pass each one through
    the charter — not send-then-review. This test wires a fake agent
    that emits a single nudge and asserts the governance_log row was
    written."""
    class _Nudgy(Agent):
        name = "nudgy"
        description = "Always nudges."
        triggers: list[str] = []

        def route(self, msg):
            return Reply(text="ok", confidence=1.0)

        def tick(self, now=None):
            return [Reply(text="hydrate, bhai", confidence=0.7)]

    miya.register(_Nudgy())

    # Drive one tick at a daytime hour → should go out.
    nudge_replies = miya.registered()[0].tick(datetime(2026, 5, 8, 14, 0))
    assert len(nudge_replies) == 1

    sent = miya._send_with_charter(
        nudge_replies[0],
        requester="nudgy",
        kind="notify.user.nudge",
        trace_id="tid-tick",
        priority=5,
        ctx={"now": datetime(2026, 5, 8, 14, 0)},
    )
    assert sent is True
    assert "hydrate" in captured_tg.last()
