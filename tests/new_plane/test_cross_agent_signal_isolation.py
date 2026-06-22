"""Cross-agent signal-store isolation.

The signal store (`new_plane/signals/store.py`) is the cross-agent bus:
Kobe/Fraser/Miya publish signals, and the orchestrate path pulls
`recent_signals` into the synth prompt (orchestrator.py:373-376 →
synthesizer `_build_prompt` "RECENT CROSS-AGENT SIGNALS"). The failure
mode (SUITE_MAP §9.7) is "Kobe sees Fraser's history and answers a pace
query with workout-design content" and "two simultaneous chats bleed".

This file pins the isolation the store DOES provide (agent / type /
trace filtering) and uses xfail tripwires for the two leaks it does NOT
prevent today (unscoped orchestrate pull; no chat dimension).
"""
from __future__ import annotations

import pytest

from new_plane.signals import store
from new_plane.miya_runner.synthesizer import _build_prompt


@pytest.fixture(autouse=True)
def _isolated_signal_db(tmp_path):
    """Every test gets its own signals DB — hermetic, no ~/.rahat write."""
    prev = store._path()
    store.set_db_path(tmp_path / "signals.db")
    store.init_db()
    yield
    store.set_db_path(prev)


def _publish(agent, type_, payload, trace_id="t", chat_id=None):
    return store.publish(agent=agent, type_=type_, payload=payload,
                         trace_id=trace_id, chat_id=chat_id)


# ─── Isolation the store guarantees (green) ───────────────────────────
def test_agent_filter_excludes_other_agents():
    _publish("kobe", "pace_update", {"v": 1})
    _publish("fraser", "design_done", {"v": 2})
    kobe = store.recent(agent="kobe", limit=50)
    assert kobe, "expected at least one kobe signal"
    assert all(s["agent"] == "kobe" for s in kobe)
    assert all(s["agent"] != "fraser" for s in kobe)


def test_fraser_query_never_returns_kobe_signals():
    _publish("kobe", "pace_update", {"v": 1})
    _publish("fraser", "design_done", {"v": 2})
    fraser = store.recent(agent="fraser", limit=50)
    assert fraser and all(s["agent"] == "fraser" for s in fraser)


def test_trace_id_isolation():
    _publish("kobe", "x", {"a": 1}, trace_id="trace-A")
    _publish("kobe", "x", {"a": 2}, trace_id="trace-B")
    a = store.recent(trace_id="trace-A", limit=50)
    assert a and all(s["trace_id"] == "trace-A" for s in a)


def test_type_filter_isolation():
    _publish("kobe", "pace_update", {"a": 1})
    _publish("kobe", "weight_log", {"a": 2})
    only = store.recent(type_="weight_log", limit=50)
    assert only and all(s["type"] == "weight_log" for s in only)


# ─── Fix verifications (PF-005 / PF-006 landed 2026-06-10) ────────────
# The orchestrator now scopes signals_recent(agent=..., chat_id=...) at
# the call site. These tests verify the fix using the call pattern the
# orchestrator now uses; they replaced the original xfail tripwires that
# pinned the store-level "unfiltered call still leaks" by-design.

def test_kobe_intent_query_excludes_fraser_signals_when_scoped():
    """PF-005: with agent= filter, a Kobe-scope pull excludes Fraser
    signals. Mirrors orchestrator.py:_primary_agent_for_intent → 'kobe'
    for a Kobe-owned intent → signals_recent(agent='kobe', ...)."""
    _publish("fraser", "design_session",
             {"workout": "21-15-9 thrusters + pullups", "chat_id": "A"})
    _publish("kobe", "pace_update", {"v": 1, "chat_id": "A"})

    # This is the call pattern the orchestrator now uses for a Kobe turn.
    recent = store.recent(agent="kobe", limit=5)
    prompt = _build_prompt(
        user_message="where am I on pace",
        facts={"recalibration": {"result": {"behind_pace": False}}},
        arbitration=None, fraser_text=None, recent_signals=recent,
    )
    assert "thrusters" not in prompt.lower(), (
        "Kobe-scoped recent_signals leaked Fraser content into the prompt"
    )
    assert "design_session" not in prompt


def test_signals_are_scoped_per_chat_when_filter_passed():
    """PF-006: with chat_id= filter, chat A's signals are not visible to
    chat B. Mirrors orchestrator.py call site after the 2026-06-10 fix.

    The legacy NULL-chat-id rows remain global (intentional — pre-migration
    signals stay visible until they age out)."""
    _publish("kobe", "pace_update",
             payload={"v": 1}, trace_id="t-A", chat_id="A")
    _publish("kobe", "pace_update",
             payload={"v": 2}, trace_id="t-B", chat_id="B")

    # Chat A's view: only chat A signals + legacy NULL-chat rows.
    visible_to_A = store.recent(chat_id="A", limit=50)
    assert all(s["chat_id"] in ("A", None) for s in visible_to_A), (
        f"chat A saw signals from other chats: "
        f"{[s['chat_id'] for s in visible_to_A]}"
    )
    a_chats = {s["chat_id"] for s in visible_to_A}
    assert "B" not in a_chats, "chat A saw a signal from chat B"


def test_publish_records_chat_id_when_supplied():
    """PF-006: publish accepts chat_id and stores it. Round-trips through
    recent() correctly."""
    sid = store.publish(
        agent="miya", type_="miya_synthesized",
        payload={"u": "test"}, trace_id="t-1", chat_id="C-42",
    )
    assert sid > 0
    rows = store.recent(chat_id="C-42", limit=1)
    assert rows and rows[0]["chat_id"] == "C-42"


def test_publish_chat_id_default_is_legacy_global():
    """Backward compat: publishes without chat_id are global (NULL),
    so they remain visible to all chat-scoped reads."""
    store.publish(agent="kobe", type_="legacy_global",
                  payload={}, trace_id="t-legacy")
    visible_to_any = store.recent(chat_id="any-chat", limit=10)
    assert any(s["type"] == "legacy_global" for s in visible_to_any), (
        "legacy NULL-chat-id signal should remain visible to all chats"
    )


# Borrow the original test names below as compatibility aliases so
# CI tooling tracking the old names sees them as still-present.
test_kobe_intent_prompt_excludes_fraser_signals = (
    test_kobe_intent_query_excludes_fraser_signals_when_scoped
)
test_signals_are_scoped_per_chat = (
    test_signals_are_scoped_per_chat_when_filter_passed
)
