"""Orchestrator + delegate-classifier integration tests.

Verifies that:
  - When delegate_classifier returns "kobe_route", the orchestrator
    calls native_client.kobe_route, returns its text as-is, skips
    the lookup/design/synth flow, publishes a "miya_delegated" signal,
    and reports the path correctly in routing metadata.
  - Same for "fraser_route".
  - When delegate_classifier returns "orchestrate", the normal
    lookup/design/synth flow runs (the existing 9-test suite covers this;
    here we just smoke-test that the path is NOT triggered).

Uses real Kobe imports under RAHAT_TEST_MODE so we exercise the actual
agents.the_scientist.handler.route() function end-to-end.
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


# ─── Slash commands take the kobe_route path ──────────────────────────

def test_slash_pace_goes_to_kobe_route(monkeypatch):
    """Verify /pace is delegated to Kobe's route() and the result returned."""
    call_args = []

    def fake_route(msg):
        call_args.append(msg)
        return "Pace verdict: on track. 1,200 kcal behind."

    monkeypatch.setattr(
        "agents.the_scientist.handler.route", fake_route,
    )
    resp = handle(Turn(user_message="/pace", chat_id="c1"))
    assert resp.routing["path"] == "kobe_route"
    assert "Pace verdict" in resp.text
    assert call_args == ["/pace"]
    assert "kobe_route" in resp.used_tools
    # Skipped the lookup/design/synth path:
    assert "kobe_active_goal" not in resp.used_tools
    assert "kobe_recalibration" not in resp.used_tools


def test_slash_plan_returns_kobe_plan_output(monkeypatch):
    """Verify /plan delegates to Kobe and returns the weekly grid."""
    def fake_route(msg):
        return "This week — Jun 8 – Jun 14\nTier hammer, target 6,000 kcal..."

    monkeypatch.setattr("agents.the_scientist.handler.route", fake_route)
    resp = handle(Turn(user_message="/plan", chat_id="c1"))
    assert resp.routing["path"] == "kobe_route"
    assert "hammer" in resp.text


def test_recaliberate_command_delegates(monkeypatch):
    def fake_route(msg):
        return "You're ahead of pace. Burned 306 / 6,000 kcal target."

    monkeypatch.setattr("agents.the_scientist.handler.route", fake_route)
    resp = handle(Turn(user_message="/recaliberate", chat_id="c1"))
    assert resp.routing["path"] == "kobe_route"
    assert "ahead of pace" in resp.text


# ─── Plan mutations ────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "Wed for CrossFit",
    "Rest on Monday",
    "pick Tue for CrossFit",
    "tolerate partner",
    "replan",
    "/replan rest Mon, CF Tue Wed Thu",
])
def test_plan_mutations_delegate_to_kobe(msg, monkeypatch):
    call_args = []

    def fake_route(m):
        call_args.append(m)
        return f"OK — applied: {m}"

    monkeypatch.setattr("agents.the_scientist.handler.route", fake_route)
    resp = handle(Turn(user_message=msg, chat_id="c1"))
    assert resp.routing["path"] == "kobe_route"
    assert call_args == [msg]


# ─── @-address routing ────────────────────────────────────────────────

def test_at_kobe_strips_prefix_before_delegation(monkeypatch):
    call_args = []

    def fake_route(m):
        call_args.append(m)
        return "Today: 1,200 kcal target."

    monkeypatch.setattr("agents.the_scientist.handler.route", fake_route)
    resp = handle(Turn(user_message="@kobe what's my plan today",
                       chat_id="c1"))
    assert resp.routing["path"] == "kobe_route"
    # Kobe sees the message WITHOUT the @kobe prefix
    assert call_args == ["what's my plan today"]


def test_at_fraser_delegates_to_fraser_route(monkeypatch):
    call_args = []

    def fake_fraser(msg, chat_id=None):
        call_args.append(msg)
        return "Workout: 5x5 back squat at 70%..."

    monkeypatch.setattr("agents.fraser.handler.route", fake_fraser)
    resp = handle(Turn(user_message="@fraser design me a wod",
                       chat_id="c1"))
    assert resp.routing["path"] == "fraser_route"
    assert "fraser_route" in resp.used_tools
    # Kobe/Fraser strip the @-prefix; Fraser's route() saw the bare body.
    assert call_args == ["design me a wod"]
    # NOTE (2026-06-13): the raw "back squat" text no longer survives to
    # resp.text — the fraser_route branch re-voices delegated output through
    # the Miya synth (NEW_MIYA_REVOICE=1 default), which returns the
    # hermetic [LLM-FALLBACK] sentinel offline. The delegation *contract*
    # (correct route + stripped body reached Fraser) is what this test pins;
    # re-voice text quality is covered by the synth-grounding evals.


# ─── Open-ended falls through to orchestrate path ─────────────────────

def test_open_ended_query_uses_orchestrate_path(monkeypatch):
    """Pin the orchestrate path is reachable when delegation is bypassed.

    Bug 2026-06-09 added _WOD_LOOKUP_RE so 'what's the workout for tomorrow'
    now routes to kobe_route (not orchestrate). To keep the orchestrate-
    path WOD lookup mechanism under test, this test uses an open-ended
    coaching query that does NOT contain a WOD/workout lookup pattern.
    """
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_active_goal",
        lambda: {"active": False},
    )
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_recalibration",
        lambda: {"behind_pace": False, "summary": "On pace"},
    )

    # Open-ended query — no WOD/workout noun, no slash, no plan mutation.
    # Falls through to orchestrate path's lookup/design/synth flow.
    resp = handle(Turn(user_message="how am I feeling about this week",
                       chat_id="c1"))
    # NOT kobe_route — it's the orchestrate path
    assert resp.routing.get("path") != "kobe_route", (
        f"open-ended coaching query unexpectedly delegated: {resp.routing}"
    )


def test_wod_lookup_delegates_to_kobe_route(monkeypatch):
    """Bug 2026-06-09 fix: WOD lookup must delegate to kobe_route.

    Verifies the new contract: WOD/workout lookup queries bypass the
    orchestrate path's synth layer entirely, going straight to Kobe's
    full route() so there's no chance for Gemini to paraphrase.
    """
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_active_goal",
        lambda: {"active": False},
    )
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_recalibration",
        lambda: {"behind_pace": False, "summary": "On pace"},
    )
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_gym_wod_on",
        lambda day: "Tomorrow's WOD: Front Squat 5x5",
    )

    resp = handle(Turn(user_message="what's the workout for tomorrow",
                       chat_id="c1"))
    assert resp.routing.get("path") == "kobe_route", (
        f"WOD lookup must delegate to kobe_route; got {resp.routing}"
    )
    assert "kobe_route" in resp.used_tools


# ─── Signal publication ────────────────────────────────────────────────

def test_delegated_turn_publishes_miya_delegated_signal(monkeypatch):
    monkeypatch.setattr(
        "agents.the_scientist.handler.route",
        lambda msg: "Pace verdict: on track",
    )
    resp = handle(Turn(user_message="/pace", chat_id="c1"))
    assert len(resp.signals) == 1

    # Verify signal payload
    from new_plane.signals.store import recent
    sigs = recent(agent="miya", type_="miya_delegated", limit=1)
    assert sigs
    payload = sigs[0]["payload"]
    assert payload["delegation_path"] == "kobe_route"
    assert payload["stripped_message"] == "/pace"


# ─── Failure handling ──────────────────────────────────────────────────

def test_kobe_route_raises_handled_gracefully(monkeypatch):
    """When Kobe's route() raises, native_client wraps it as
    AdapterResult.error. Orchestrator returns the error message rather
    than crashing — the runner must keep serving."""
    def boom(msg):
        raise RuntimeError("kobe internals crashed")

    monkeypatch.setattr("agents.the_scientist.handler.route", boom)
    resp = handle(Turn(user_message="/pace", chat_id="c1"))
    assert resp.trace_id
    # Got the error string as the text
    assert "RuntimeError" in resp.text or "crashed" in resp.text
    # Still "sent" because Kobe-route delegation doesn't gate on charter
    assert resp.sent is True
