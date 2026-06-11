"""Pin: 2026-06-10 — six fixes shipped after the autonomous test-lead shift.

SYMPTOMS (production / test-lead surfaced):
  * PF-002: "/ fix sat 407" routed to orchestrate (synth could paraphrase).
  * PF-003: "what was the workout for last Friday" routed to orchestrate.
  * PF-001: WOD-lookup prompt received unrelated pace facts (Bug-I residual).
  * PF-004: 'Ahead of pace' summary appeared in prompt even when arbitration
            verdict was 'behind_pace' (Bug-H residual).
  * PF-005: orchestrator pulled signals_recent() unscoped, leaking
            cross-agent context into the synth prompt.
  * PF-006: signals table had no chat_id column — concurrent chats leaked.

ROOT CAUSES (per `specs/test_lead/findings/PROPOSED_FIXES.md`):
  * PF-002: `_SLASH_RE` required alpha immediately after `/`.
  * PF-003: `_WOD_LOOKUP_RE` missing "what was" interrogative + relative
            qualifier ("last|this|next") in day-token branch.
  * PF-001: synthesizer `_build_prompt` rendered ALL facts regardless
            of what the user asked.
  * PF-004: contradictory `recalibration.summary` was passed verbatim;
            verdict block alone wasn't enough to prevent Gemini paraphrase.
  * PF-005: orchestrator called `signals_recent(limit=5)` with no agent
            scope; Fraser signals reached Kobe prompts.
  * PF-006: signals lacked a chat dimension; only safe under single-chat.

FIXES (this commit):
  * `delegate_classifier.py`:
      - `_SLASH_RE` widened to `^\\s*/\\s*[a-z]`.
      - `_WOD_LOOKUP_RE` adds 'what was' + optional `last|this|next`
        qualifier before the day token.
  * `synthesizer.py`:
      - `_build_prompt` accepts `intent`; `_scope_facts` filters by intent.
      - `_is_summary_contradicted_by_verdict` SUPPRESSES contradictory
        summary text (replaces with a `<SUPPRESSED ...>` marker).
  * `orchestrator.py`:
      - Adds `_primary_agent_for_intent` + `_intent_label` helpers.
      - Calls `signals_recent(agent=primary, chat_id=turn.chat_id, ...)`
        and `synthesizer.synthesize(..., intent=...)`.
      - `publish_signal(... chat_id=turn.chat_id or None)` on all 3 sites.
  * `signals/store.py`:
      - Adds `chat_id TEXT` column via additive migration.
      - `publish` and `recent` accept `chat_id`. Legacy NULL rows remain
        visible to all chats (backward compat).
  * `native_client.signals_recent` / `adapter_client.signals_recent`:
      - Forward `chat_id=` through to the store / HTTP endpoint.

THIS PIN ASSERTS (one test per PF):
  Each historically-broken phrasing or call pattern is correctly handled,
  AND the negative-guard / no-regression cases continue to behave.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_runner.synthesizer import _build_prompt
from new_plane.signals import store


# ─── PF-002: space after slash routes correctly ───────────────────────

@pytest.mark.parametrize("msg", [
    "/ fix sat 407",
    "/  pace",
    "/ plan",
    "/  today",
    # And the standard no-space form still works
    "/fix sat 407",
    "/pace",
])
def test_pf_002_slash_with_space_routes_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"{msg!r} → {path!r}"


# ─── PF-003: past-tense WOD lookup routes correctly ──────────────────

@pytest.mark.parametrize("msg", [
    "What was the workout for last Friday?",
    "what was the workout for last Monday",
    "what was todays workout",
    "what was tomorrow's WOD",
    "what was Wednesdays session",
    # Relative qualifier in day-token branch
    "workout for last Friday",
    "workout for next Monday",
    "session for this Wednesday",
])
def test_pf_003_past_tense_or_relative_day_routes_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"{msg!r} → {path!r}"


@pytest.mark.parametrize("msg", [
    "design me a workout I did last Friday",
    "create a workout for next Monday",
    "build a session for last Wednesday",
])
def test_pf_003_design_intent_with_relative_qualifier_still_orchestrates(msg):
    path, _ = classify_delegation(msg)
    assert path == "orchestrate", (
        f"design intent {msg!r} → {path!r}; Fraser path blocked"
    )


# ─── PF-001: synth prompt scoped by intent ───────────────────────────

def test_pf_001_workout_lookup_intent_excludes_pace_facts():
    prompt = _build_prompt(
        user_message="what is tomorrow's WOD",
        facts={"recalibration": {"result": {"behind_pace": False,
                "summary": "1,433 kcal ahead of plan"}}},
        arbitration=None, fraser_text=None, recent_signals=None,
        intent="workout_lookup",
    )
    assert "1,433" not in prompt
    assert "ahead of plan" not in prompt.lower()


def test_pf_001_pace_query_intent_includes_pace_facts():
    """The fix must not over-scope: pace_query intent keeps pace facts."""
    prompt = _build_prompt(
        user_message="where am I on pace",
        facts={"recalibration": {"result": {"summary": "On pace — 1,200/2,100"}}},
        arbitration=None, fraser_text=None, recent_signals=None,
        intent="pace_query",
    )
    assert "1,200" in prompt or "on pace" in prompt.lower()


def test_pf_001_no_intent_keeps_pre_fix_behavior():
    """Backward compat: when no intent is supplied (legacy callers),
    the prompt includes all facts (the pre-fix behavior)."""
    prompt = _build_prompt(
        user_message="where am I",
        facts={"recalibration": {"result": {"summary": "On pace"}}},
        arbitration=None, fraser_text=None, recent_signals=None,
        # intent omitted
    )
    assert "On pace" in prompt


# ─── PF-004: contradictory recalibration summary suppressed ──────────

def test_pf_004_contradictory_summary_suppressed_not_passed_verbatim():
    prompt = _build_prompt(
        user_message="where am I on pace",
        facts={"recalibration": {"result": {"behind_pace": True,
                "summary": "Ahead of pace — comfortable buffer."}}},
        arbitration={"rule": "behind_pace", "guidance": "be honest"},
        fraser_text=None, recent_signals=None,
        intent="pace_query",
    )
    assert "ahead of pace" not in prompt.lower(), (
        "Bug-H would recur if the contradictory summary is in the prompt"
    )
    assert "SUPPRESSED" in prompt, (
        "the suppression marker must remain so the omission stays auditable"
    )


def test_pf_004_aligned_summary_not_suppressed():
    """If verdict and summary agree, the summary stays."""
    prompt = _build_prompt(
        user_message="where am I on pace",
        facts={"recalibration": {"result": {"behind_pace": True,
                "summary": "Behind pace — need to pick up."}}},
        arbitration={"rule": "behind_pace", "guidance": "be honest"},
        fraser_text=None, recent_signals=None,
        intent="pace_query",
    )
    assert "Behind pace" in prompt
    assert "SUPPRESSED" not in prompt


# ─── PF-005 / PF-006: signal isolation by agent + chat ───────────────

@pytest.fixture
def _store_db(tmp_path):
    prev = store._path()
    store.set_db_path(tmp_path / "signals.db")
    store.init_db()
    yield
    store.set_db_path(prev)


def test_pf_005_agent_filter_excludes_other_agents(_store_db):
    store.publish(agent="fraser", type_="design_session",
                  payload={"workout": "thrusters"}, trace_id="t-1")
    store.publish(agent="kobe", type_="pace_update",
                  payload={"v": 1}, trace_id="t-2")
    kobe_view = store.recent(agent="kobe", limit=10)
    assert kobe_view
    assert all(s["agent"] == "kobe" for s in kobe_view)


def test_pf_006_chat_filter_isolates_concurrent_chats(_store_db):
    store.publish(agent="kobe", type_="pace_update", payload={"v": 1},
                  trace_id="t-A", chat_id="A")
    store.publish(agent="kobe", type_="pace_update", payload={"v": 2},
                  trace_id="t-B", chat_id="B")
    visible_to_A = store.recent(chat_id="A", limit=10)
    a_chats = {s["chat_id"] for s in visible_to_A}
    assert "B" not in a_chats, "chat A saw a signal from chat B"


def test_pf_006_legacy_null_chat_id_remains_global(_store_db):
    """Backward compat: pre-migration NULL chat_id rows are visible
    to every chat-scoped read."""
    store.publish(agent="kobe", type_="legacy_global",
                  payload={"v": 1}, trace_id="t-legacy")  # no chat_id
    visible_to_arbitrary_chat = store.recent(chat_id="any-chat", limit=10)
    assert any(s["type"] == "legacy_global" for s in visible_to_arbitrary_chat)


def test_pf_006_publish_round_trip_with_chat_id(_store_db):
    sid = store.publish(agent="miya", type_="t", payload={},
                        trace_id="t-1", chat_id="C42")
    assert sid > 0
    rows = store.recent(chat_id="C42", limit=1)
    assert rows and rows[0]["chat_id"] == "C42"
