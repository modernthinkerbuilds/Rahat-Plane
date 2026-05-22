"""chat_id ABI threading (Day-11, 2026-05-21).

Pins the route() ABI carrying per-conversation context end-to-end:

    miya poll loop  (extracts chat_id from the Telegram update)
      → miya.route(msg, chat_id=…)
        → _dispatch_to / _dispatch_multi / _route_via_triggers
          → agent.route(msg, chat_id=…)
            → Fraser handler.route(msg, chat_id=…)
              → composer.design_session(msg, chat_id=…)

Why this file exists
--------------------
Before this change `chat_id` was extracted from the Telegram update at
the poll loop (core/miya.py) but DROPPED before `route()` was called, so
Fraser's conversational memory (`core.chat_memory`) was dead-on-arrival:
the composer accepted a `chat_id` parameter that nothing ever supplied.
Every link below fails loudly if it stops forwarding `chat_id`.

It also pins the control-plane robustness contract: a specialist whose
`route()` predates the optional ABI fields (signature still
`route(self, msg)`) must STILL be dispatchable. Miya negotiates each
agent's capabilities from its signature (`core.miya._safe_route`) rather
than assuming conformance — one un-migrated agent must never crash the
orchestrator's poll loop with `TypeError: unexpected keyword 'chat_id'`.

All tests are offline. No GEMINI_API_KEY, no Telegram.
"""
from __future__ import annotations

import json

import pytest

from core.agent import Agent, Reply


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test owns the registry — clear before AND after so neither
    leftover agents from a prior test nor agents we register here leak
    into the next test."""
    from core import miya
    miya.clear_registry()
    yield
    miya.clear_registry()


def _mock_classifier(monkeypatch, scores: dict[str, float]) -> None:
    """Force Miya's LLM classifier to return fixed scores so routing is
    deterministic and offline."""
    from core import io as cio
    monkeypatch.setattr(
        cio, "llm_generate",
        lambda prompt, *, model=None: json.dumps(scores))


# ─── 1. chat_id reaches the composer through the real Fraser stack ───
class TestChatIdReachesComposer:
    def test_chat_id_threads_route_to_composer(self, fresh_db, monkeypatch):
        """miya.route(chat_id=X) must deliver X all the way to
        composer.design_session. This is the end-to-end proof that the
        conversational-memory wiring is live, not just declared."""
        from core import miya
        from agents.fraser.agent import FraserAgent
        from agents.the_scientist.agent import KobeAgent
        from agents.fraser import composer

        miya.register(KobeAgent())
        miya.register(FraserAgent())
        _mock_classifier(monkeypatch, {"fraser": 0.9, "kobe": 0.1})

        captured: dict = {}

        def _spy(msg, db_path=None, chat_id=None):
            captured["msg"] = msg
            captured["chat_id"] = chat_id
            return ("## Warm-up\n…\n## Strength\n…\n"
                    "## WOD\n…\n## Cool-down\n…")

        monkeypatch.setattr(composer, "design_session", _spy)

        reply = miya.route("design me a 60 minute WOD for today",
                           chat_id="CHAT-XYZ")

        assert reply is not None, "miya.route must produce a reply"
        assert captured.get("chat_id") == "CHAT-XYZ", (
            "chat_id must thread miya.route → FraserAgent.route → "
            "handler.route → composer.design_session. A None here means "
            "a link in the chain dropped the keyword and Fraser's "
            "conversational memory is dead-on-arrival.")

    def test_handler_route_forwards_chat_id_directly(self, monkeypatch):
        """Narrower pin on the Fraser handler→composer hop, independent
        of Miya, so a regression localises fast."""
        from agents.fraser import handler, composer

        captured: dict = {}

        def _spy(msg, db_path=None, chat_id=None):
            captured["chat_id"] = chat_id
            return "## Warm-up\n…\n## Strength\n…\n## WOD\n…\n## Cool-down\n…"

        monkeypatch.setattr(composer, "design_session", _spy)
        handler.route("give me today's workout", chat_id="CHAT-42")
        assert captured.get("chat_id") == "CHAT-42"


# ─── 2. Control-plane robustness: capability negotiation ─────────────
class TestControlPlaneRobustness:
    def test_legacy_agent_without_chat_id_still_dispatches(
            self, fresh_db, monkeypatch):
        """An agent whose route() predates the optional ABI (signature
        is still `route(self, msg)`) must still be dispatchable when the
        caller supplies chat_id. Miya must NOT raise
        `TypeError: unexpected keyword argument 'chat_id'`."""
        from core import miya

        class LegacyAgent(Agent):
            name = "legacy"
            description = "legacy agent on the old ABI"

            def route(self, msg):  # NO chat_id / db_path — pre-Day-11
                return Reply(text=f"legacy:{msg}", confidence=1.0)

        miya.register(LegacyAgent())
        _mock_classifier(monkeypatch, {"legacy": 0.95})

        reply = miya.route("hello there", chat_id="CHAT-1")
        assert reply is not None
        assert reply.text == "legacy:hello there", (
            "A legacy agent must be dispatched without its kwargs; the "
            "control plane negotiates capabilities from the signature.")

    def test_var_kwargs_agent_receives_chat_id(self, fresh_db, monkeypatch):
        """An agent that declares **kwargs is opting into every optional
        ABI field, so it must receive chat_id."""
        from core import miya

        seen: dict = {}

        class KwargsAgent(Agent):
            name = "kw"
            description = "agent that accepts **kwargs"

            def route(self, msg, **kw):
                seen.update(kw)
                return Reply(text="ok", confidence=1.0)

        miya.register(KwargsAgent())
        _mock_classifier(monkeypatch, {"kw": 0.95})

        miya.route("hi", chat_id="CHAT-2")
        assert seen.get("chat_id") == "CHAT-2"

    def test_safe_route_helper_negotiates_signature(self):
        """Unit-level pin on _safe_route: it forwards chat_id only to
        callees that accept it (explicit param OR **kwargs), and never
        to a strict `route(self, msg)`."""
        from core import miya

        class Strict(Agent):
            name = "strict"
            def route(self, msg):
                return Reply(text="strict", confidence=1.0)

        class Aware(Agent):
            name = "aware"
            def route(self, msg, *, chat_id=None, db_path=None):
                return Reply(text=f"aware:{chat_id}", confidence=1.0)

        # Strict agent: no crash, chat_id silently not forwarded.
        r1 = miya._safe_route(Strict(), "m", chat_id="C", db_path="d")
        assert r1.text == "strict"

        # Aware agent: chat_id forwarded.
        r2 = miya._safe_route(Aware(), "m", chat_id="C", db_path="d")
        assert r2.text == "aware:C"
