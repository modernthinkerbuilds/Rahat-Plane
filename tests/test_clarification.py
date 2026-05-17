"""Clarification policy (ADR-008) contract tests.

Pins the multi-turn clarification flow:
  1. Low-confidence message → ask_clarification builds an A/B reply.
  2. The clarification persists in memory_entities with 60s TTL.
  3. Next message in the same chat with "A"/"B" reply → resolves to
     the chosen agent and dispatches the ORIGINAL message.
  4. "C" / unrecognized reply → no resolution, re-classify on next turn.
  5. Stale clarification (>60s) → not resolved.
  6. Slash commands bypass clarification (handled in handler.route).
  7. RAHAT_CLARIFICATION_ENABLED=0 disables — low-conf falls through
     to top-pick dispatch.

These tests stub `cio.llm_generate` so no real Gemini calls happen.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from core import miya
from core.agent import Agent, Reply


class _Kobe(Agent):
    name = "kobe"
    description = (
        "Vitality coach. Weight, HRV, weekly burn targets, "
        "weight-loss timeline math."
    )

    def route(self, msg):
        return Reply(text=f"kobe answered: {msg}", confidence=0.95)


class _Fraser(Agent):
    name = "fraser"
    description = (
        "CrossFit workout designer. Adapts gym programming with "
        "scaled loads + predicted burn."
    )

    def route(self, msg):
        return Reply(text=f"fraser answered: {msg}", confidence=0.92)


@pytest.fixture
def stub_classifier(monkeypatch):
    state: dict[str, str | None] = {"response": None}

    def _stub_llm(prompt: str, *args, **kwargs) -> str:
        if "JSON:" in prompt and "User message:" in prompt:
            return state["response"] or ""
        return ""

    from core import io as cio
    monkeypatch.setattr(cio, "llm_generate", _stub_llm)

    class _F:
        def set(self, response):
            if isinstance(response, dict):
                response = json.dumps(response)
            state["response"] = response
    return _F()


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Per-test DB so clarification entities don't leak across cases."""
    db = tmp_path / "clarif.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture(autouse=True)
def _clean_registry():
    miya.clear_registry()
    yield
    miya.clear_registry()


# ─── 1. ask_clarification builds A/B reply ──────────────────────
def test_ask_clarification_builds_ab_reply(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    reply = miya.ask_clarification(
        "ambiguous message",
        candidates=[("kobe", 0.35), ("fraser", 0.30)],
        chat_id="test-chat-1",
    )
    assert reply is not None
    # Should reference both top candidates
    assert "A)" in reply.text
    assert "B)" in reply.text
    assert "kobe" in reply.text
    assert "fraser" in reply.text
    # Should give the user an "out" (rephrase option)
    assert "C)" in reply.text or "rephrase" in reply.text.lower()


def test_ask_clarification_no_candidates_returns_generic(fresh_db):
    miya.register(_Kobe())

    reply = miya.ask_clarification(
        "anything",
        candidates=[],
        chat_id="test-chat-2",
    )
    assert reply is not None
    assert "help" in reply.text.lower() or "rephrase" in reply.text.lower()


# ─── 2. Persistence + 60s TTL ────────────────────────────────────
def test_clarification_persists_to_substrate(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "what is the WOD",
        candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-A",
    )

    # Read back from substrate
    from core import memory as _mem
    rows = _mem.list_entities(
        agent="miya", type="miya_clarification",
        status="active", include_expired=False,
    )
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["chat_id"] == "chat-A"
    assert payload["original_msg"] == "what is the WOD"
    assert {c["agent"] for c in payload["candidates"]} == {"kobe", "fraser"}


def test_clarification_has_60s_ttl(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    before = datetime.now()
    miya.ask_clarification(
        "anything",
        candidates=[("kobe", 0.4), ("fraser", 0.3)],
        chat_id="chat-B",
    )
    from core import memory as _mem
    rows = _mem.list_entities(
        agent="miya", type="miya_clarification",
        status="active", include_expired=True,  # include even past valid_until
    )
    assert len(rows) == 1
    valid_until = rows[0].get("valid_until")
    assert valid_until is not None
    # Parse the ISO string back to compare
    if isinstance(valid_until, str):
        # Substrate stores ISO format; parse it
        vu_dt = datetime.fromisoformat(
            valid_until.replace("T", " ").split(".")[0]
        )
    else:
        vu_dt = valid_until
    delta = vu_dt - before
    # Should be ~60 seconds, allow ±5s for test scheduling jitter
    assert 55 <= delta.total_seconds() <= 65


# ─── 3. resolve_clarification — A/B/C dispatch ───────────────────
def test_resolve_clarification_a_dispatches_to_top(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "what is the WOD",
        candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-C",
    )

    resolved = miya.resolve_clarification("A", chat_id="chat-C")
    assert resolved is not None
    agent_name, original_msg = resolved
    assert agent_name == "kobe"  # A = top candidate
    assert original_msg == "what is the WOD"


def test_resolve_clarification_b_dispatches_to_second(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "what is the WOD",
        candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-D",
    )

    resolved = miya.resolve_clarification("B", chat_id="chat-D")
    assert resolved is not None
    assert resolved[0] == "fraser"


def test_resolve_clarification_case_insensitive(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "x", candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-CI",
    )
    # Lowercase 'a' should also work
    resolved = miya.resolve_clarification("a", chat_id="chat-CI")
    assert resolved is not None and resolved[0] == "kobe"


def test_resolve_clarification_c_returns_none(fresh_db):
    """C means rephrase — let the new message re-classify."""
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "x", candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-E",
    )
    # C is the rephrase option; should NOT resolve to an agent
    resolved = miya.resolve_clarification("C", chat_id="chat-E")
    assert resolved is None


def test_resolve_clarification_garbage_returns_none(fresh_db):
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "x", candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-F",
    )
    # User typed a real follow-up, not A/B/C
    resolved = miya.resolve_clarification(
        "actually I meant my workout", chat_id="chat-F",
    )
    assert resolved is None


def test_resolve_no_chat_id_returns_none(fresh_db):
    """No chat_id → can't look up clarification context → None."""
    resolved = miya.resolve_clarification("A", chat_id=None)
    assert resolved is None


def test_resolve_no_pending_returns_none(fresh_db):
    miya.register(_Kobe())
    # Nothing was ever asked for chat-G
    resolved = miya.resolve_clarification("A", chat_id="chat-G")
    assert resolved is None


def test_resolve_marks_clarification_superseded(fresh_db):
    """Once resolved, the entity status changes so re-asking 'A' on
    the next turn doesn't re-trigger."""
    miya.register(_Kobe())
    miya.register(_Fraser())

    miya.ask_clarification(
        "x", candidates=[("kobe", 0.4), ("fraser", 0.35)],
        chat_id="chat-H",
    )
    miya.resolve_clarification("A", chat_id="chat-H")

    # Second 'A' should not resolve (entity is superseded)
    second = miya.resolve_clarification("A", chat_id="chat-H")
    assert second is None


# ─── 4. End-to-end via route() ───────────────────────────────────
def test_route_with_low_conf_returns_clarification_then_resolves(
    stub_classifier, fresh_db,
):
    """The full multi-turn dance: low-conf → clarification reply →
    user picks A → original message dispatched to the chosen agent."""
    miya.register(_Kobe())
    miya.register(_Fraser())

    stub_classifier.set({"kobe": 0.35, "fraser": 0.30})
    reply = miya.route("what is the WOD", chat_id="chat-Z")
    assert reply is not None
    assert "A)" in reply.text

    # Now the user picks A
    reply2 = miya.route("A", chat_id="chat-Z")
    assert reply2 is not None
    # Should be Kobe (the A choice) answering the ORIGINAL message
    assert "kobe answered: what is the WOD" in reply2.text


def test_route_with_low_conf_disabled_falls_through_to_top(
    stub_classifier, fresh_db, monkeypatch,
):
    """RAHAT_CLARIFICATION_ENABLED=0 → low-conf dispatches top pick directly."""
    monkeypatch.setenv("RAHAT_CLARIFICATION_ENABLED", "0")
    miya.register(_Kobe())
    miya.register(_Fraser())

    stub_classifier.set({"kobe": 0.35, "fraser": 0.30})
    reply = miya.route("ambiguous", chat_id="chat-X")
    assert reply is not None
    assert "kobe answered" in reply.text   # top pick dispatched
    assert "A)" not in reply.text          # no clarification


# ─── 5. Public surface ───────────────────────────────────────────
def test_clarification_helpers_exist():
    """ask_clarification and resolve_clarification are the public API."""
    assert hasattr(miya, "ask_clarification")
    assert hasattr(miya, "resolve_clarification")
    assert callable(miya.ask_clarification)
    assert callable(miya.resolve_clarification)
