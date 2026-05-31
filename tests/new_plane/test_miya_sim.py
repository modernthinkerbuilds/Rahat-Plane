"""Tests for the Python new_miya simulator.

Pin the orchestration contract: intent classification, autonomy budget,
signal publication, charter gate.
"""
from __future__ import annotations

import pytest

from new_plane.miya_sim.orchestrator import (
    Turn, handle, classify_intent, arbitrate, TurnBudget,
)


@pytest.fixture(autouse=True)
def isolated_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(tmp_path / "sim.db"))
    from new_plane.signals import store
    store.set_db_path(tmp_path / "sim.db")
    store.init_db()
    yield


# ─── intent classification ─────────────────────────────────────────────────
@pytest.mark.parametrize("msg,kobe,fraser,design", [
    ("what's my plan today",                       True,  False, False),
    ("when will I hit 196",                        True,  False, False),
    # "design me a workout" — kobe-context (workout) AND design intent → both
    ("design me a workout",                        True,  True,  True),
    # "scale this WOD" — same pattern: WOD pulls kobe; scale → design
    ("scale this WOD",                             True,  True,  True),
    # "session" + "scale" → design intent; no kobe hint words
    ("how should I scale today's session",         False, True,  True),
    ("hello",                                      False, False, False),
])
def test_classify_intent(msg, kobe, fraser, design):
    out = classify_intent(msg)
    assert out["needs_kobe"] == kobe, out
    assert out["needs_fraser"] == fraser, out
    assert out["is_design_request"] == design, out


# ─── arbitration ───────────────────────────────────────────────────────────
def test_arbitrate_behind_pace():
    facts = {"recalibration": {"result": {"behind_pace": True}}}
    v = arbitrate(facts)
    assert v and v["rule"] == "behind_pace"


def test_arbitrate_goal_close():
    facts = {"active_goal": {"result": {"active": True, "weeks_to_target": 0.5}}}
    v = arbitrate(facts)
    assert v and v["rule"] == "goal_close"


def test_arbitrate_no_rule():
    facts = {"active_goal": {"result": {"active": False}}}
    assert arbitrate(facts) is None


# ─── budget ────────────────────────────────────────────────────────────────
def test_budget_caps_at_3_tool_calls():
    b = TurnBudget(trace_id="x")
    for _ in range(3):
        assert b.can_call()
        b.record()
    assert not b.can_call()


def test_budget_caps_design_at_1():
    b = TurnBudget(trace_id="x")
    assert b.can_call("design")
    b.record("design")
    assert not b.can_call("design")
    # but the general budget still has room
    assert b.can_call()


def test_budget_caps_pro_at_1():
    b = TurnBudget(trace_id="x")
    assert b.can_call("pro")
    b.record("pro")
    assert not b.can_call("pro")


# ─── end-to-end (uses real Kobe code) ──────────────────────────────────────
def test_handle_kobe_intent_produces_response_and_signal(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resp = handle(Turn(user_message="what's my plan today"))
    assert resp.trace_id
    assert resp.text  # non-empty (fallback synthesis)
    assert "kobe_active_goal" in resp.used_tools
    assert "kobe_recalibration" in resp.used_tools
    assert "kobe_charter_check" in resp.used_tools
    assert resp.sent in (True, False)  # charter result
    assert len(resp.signals) >= 1  # the miya_synthesized signal


def test_handle_non_kobe_intent_skips_kobe_tools(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    resp = handle(Turn(user_message="hello there"))
    # Only the charter check should run; no Kobe tools needed
    assert "kobe_active_goal" not in resp.used_tools
    assert "kobe_charter_check" in resp.used_tools


def test_handle_respects_autonomy_budget(monkeypatch):
    """Even on a design request that needs Kobe + Fraser, total tool calls
    cannot exceed 3 (active_goal + recalibration + fraser_design)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    # Stub Fraser to avoid hitting LLM
    from agents.fraser import composer
    monkeypatch.setattr(composer, "design_session",
                        lambda msg, chat_id=None: "fake design output")
    resp = handle(Turn(user_message="design my workout today"))
    # Count tool calls (excluding charter_check which is mandatory and not budgeted in our list)
    budgeted = [t for t in resp.used_tools if t != "kobe_charter_check"]
    assert len(budgeted) <= 3
