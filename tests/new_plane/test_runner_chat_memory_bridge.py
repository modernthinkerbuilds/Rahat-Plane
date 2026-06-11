"""Chat-memory bridge tests.

Pins that:
  - When RAHAT_XAGENT_MEMORY=1, the orchestrator appends user + bot turns
    to core.chat_memory after every turn.
  - When the flag is OFF (default), no chat_memory writes happen.
  - The synthesizer receives the chat_memory_block when present.
  - The "Yes" follow-up bug from the old Miya chat is fixed: a "Yes"
    reply with chat history gets routed correctly because the synth
    prompt includes the previous bot turn for context.
  - chat_memory failures do NOT crash the turn (best-effort).

Each test uses an isolated test DB to avoid polluting the live vault.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from new_plane.miya_runner.orchestrator import Turn, handle


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")


def test_flag_off_no_chat_memory_writes(monkeypatch):
    """With RAHAT_XAGENT_MEMORY=0, no chat_memory calls happen.

    Note: the user's .env has RAHAT_XAGENT_MEMORY=1 as a live flag, so
    we use setenv to explicitly turn it off rather than delenv (which
    can be a no-op if the env was already set process-wide).
    """
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "0")
    monkeypatch.setattr(
        "agents.the_scientist.handler.route",
        lambda msg: "Today: 1,200 kcal target",
    )

    append_calls = []

    def fake_append(*args, **kwargs):
        append_calls.append((args, kwargs))

    with patch("core.chat_memory.append", fake_append):
        handle(Turn(user_message="/today", chat_id="c1"))
    assert append_calls == []


def test_flag_on_records_user_and_bot_turn_on_delegation(monkeypatch):
    """When flag is ON, both the user message and the bot reply are
    appended (kobe_route path)."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    monkeypatch.setattr(
        "agents.the_scientist.handler.route",
        lambda msg: "Today: 1,200 kcal target",
    )

    append_calls = []

    def fake_append(chat_id, role, text):
        append_calls.append((chat_id, role, text))

    with patch("core.chat_memory.append", fake_append):
        handle(Turn(user_message="/today", chat_id="c1"))

    assert len(append_calls) == 2
    assert append_calls[0] == ("c1", "user", "/today")
    assert append_calls[1][0] == "c1"
    assert append_calls[1][1] == "bot"
    assert "1,200 kcal" in append_calls[1][2]


def test_flag_on_records_turns_on_orchestrate_path(monkeypatch):
    """When flag is ON, orchestrate path also records turns (non-delegation)."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_active_goal",
        lambda: {"active": False},
    )
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_recalibration",
        lambda: {"behind_pace": False, "summary": "On pace"},
    )

    append_calls = []

    def fake_append(chat_id, role, text):
        append_calls.append((chat_id, role, text))

    with patch("core.chat_memory.append", fake_append):
        resp = handle(Turn(user_message="what's my plan today",
                            chat_id="c1"))

    # User and bot both appended
    roles = [role for _, role, _ in append_calls]
    assert "user" in roles
    assert "bot" in roles


def test_chat_memory_failure_does_not_crash_turn(monkeypatch):
    """If core.chat_memory.append blows up, the turn still completes."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    monkeypatch.setattr(
        "agents.the_scientist.handler.route",
        lambda msg: "Today: 1,200 kcal target",
    )

    def boom(*args, **kwargs):
        raise RuntimeError("chat_memory DB locked")

    with patch("core.chat_memory.append", boom):
        resp = handle(Turn(user_message="/today", chat_id="c1"))
    # Turn still succeeds
    assert resp.trace_id
    assert resp.sent is True


def test_synthesizer_receives_chat_memory_block(monkeypatch):
    """When flag is ON, the synthesizer is called with the chat_memory_block."""
    monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_active_goal",
        lambda: {"active": False},
    )
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_recalibration",
        lambda: {"behind_pace": False, "summary": "On pace"},
    )

    captured_kwargs = {}
    from new_plane.miya_runner import synthesizer

    real_synth = synthesizer.synthesize

    def capture(**kw):
        captured_kwargs.update(kw)
        return real_synth(**kw)

    fake_block = (
        "═══ RECENT CONVERSATION ═══\n"
        "Bot: would you like me to schedule that for Saturday?\n"
        "User: Yes"
    )
    monkeypatch.setattr(synthesizer, "synthesize", capture)
    monkeypatch.setattr(
        "core.chat_memory.to_prompt_block",
        lambda chat_id: fake_block,
    )

    handle(Turn(user_message="Yes", chat_id="c1"))
    assert "chat_memory_block" in captured_kwargs
    assert captured_kwargs["chat_memory_block"] == fake_block


def test_yes_follow_up_with_history_includes_block_in_prompt(monkeypatch):
    """The 'Yes' routing bug: when the user replies 'Yes' to a question,
    the synth prompt must include the previous bot turn so Gemini knows
    what they're agreeing to.

    This is the fix for the old Miya chat bug where 'Yes' produced
    'I'm not sure how to route that'.
    """
    from new_plane.miya_runner.synthesizer import _build_prompt

    fake_block = (
        "═══ RECENT CONVERSATION ═══\n"
        "Bot: Looks like you're on track. Want me to plan the remaining days?\n"
        "User: Yes"
    )
    prompt = _build_prompt(
        user_message="Yes",
        facts={},
        arbitration=None,
        fraser_text=None,
        recent_signals=None,
        chat_memory_block=fake_block,
    )
    assert "CONVERSATION SO FAR" in prompt
    assert "Looks like you're on track" in prompt
    # The prompt explicitly tells the model how to handle short confirmations
    assert "Yes" in prompt
    assert "previous bot turn" in prompt.lower() or "last bot turn" in prompt.lower()
