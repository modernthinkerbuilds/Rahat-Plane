"""Regression: composer answers follow-ups instead of regenerating
(2026-05-23).

The bug
-------
The composer had ONE mode: design a full 4-section session. Every message
re-ran that path — so a narrow follow-up like "what weights should I
follow?" or "how many calories will I burn in this WOD?" produced a
DIFFERENT workout with DIFFERENT numbers. To the user that reads as
hallucination / hardcoding: you ask about the session you were just given
and get a different one back.

The fix
-------
composer._is_followup_question detects a follow-up (prior conversation in
chat_memory + question/refinement phrasing, NOT an explicit design
request) and routes to _answer_followup, which prompts the LLM to ANSWER
against the session already in the conversation — no 4-section schema, no
invented workout.

These tests force the LLM via a fake so they're hermetic, and assert on
WHICH prompt was built (design vs follow-up) plus the routing predicate.
"""
from __future__ import annotations

import pytest

from agents.fraser import composer


@pytest.fixture
def fake_llm(monkeypatch):
    """Capture prompts and return scripted responses based on which
    schema the composer asked for."""
    calls: list[str] = []

    def _gen(prompt, *a, **k):
        calls.append(prompt)
        if "OUTPUT (FOLLOW-UP ANSWER)" in prompt:
            return ("Back Squat is **60 kg (132 lbs)** — 60% of your 102 kg "
                    "max. Heels on 2.5 lb plates, exhale on the drive up.")
        return ("## Part 1: Warm-up (10 min)\n- cat-cow\n"
                "## Part 2: Strength (20 min)\n- Back Squat 60 kg (132 lbs) "
                "— 60% of 102 kg\n## Part 3: WOD / Metcon (20 min)\n- row\n"
                "## Part 4: Cool-down (10 min)\n- legs up the wall\n"
                "### Coach's Note\nGo get it.")

    from core import io as cio
    monkeypatch.setattr(cio, "llm_generate", _gen)
    return calls


CID = "CHAT-FOLLOWUP"


class TestFollowupRouting:
    def test_design_then_followup_does_not_regenerate(self, bootstrap_substrate,
                                                       fake_llm):
        # Turn 1: design — full 4-section session.
        out1 = composer.design_session(
            "design me a 60 minute session for today", chat_id=CID)
        assert composer._looks_like_4_section(out1)
        assert "OUTPUT FORMAT (MANDATORY)" in fake_llm[-1]

        # Turn 2: the exact failing query.
        out2 = composer.design_session(
            "what weights should I follow?", chat_id=CID)
        assert "OUTPUT (FOLLOW-UP ANSWER)" in fake_llm[-1], (
            "a follow-up must build the follow-up prompt, not the "
            "4-section design prompt")
        assert not composer._looks_like_4_section(out2), (
            "a follow-up must NOT come back as a regenerated 4-section "
            "session — that's the hallucination/hardcoding symptom")
        assert "60 kg" in out2

    def test_calorie_followup_routes_to_answer(self, bootstrap_substrate,
                                               fake_llm):
        composer.design_session("design a session for today", chat_id=CID)
        composer.design_session(
            "how many calories will I burn in this WOD?", chat_id=CID)
        assert "OUTPUT (FOLLOW-UP ANSWER)" in fake_llm[-1]

    def test_explicit_new_design_still_builds_session(self, bootstrap_substrate,
                                                       fake_llm):
        """Even WITH history, an explicit design request must rebuild —
        not be mistaken for a follow-up."""
        composer.design_session("design a session for today", chat_id=CID)
        composer.design_session(
            "design me a new session for tomorrow", chat_id=CID)
        assert "OUTPUT FORMAT (MANDATORY)" in fake_llm[-1]


class TestFollowupPredicate:
    def test_no_history_is_never_followup(self, bootstrap_substrate):
        assert composer._is_followup_question(
            "what weights should I follow?", "FRESH-CHAT") is False

    def test_no_chat_id_is_never_followup(self):
        assert composer._is_followup_question(
            "what weights should I follow?", None) is False

    def test_design_signal_overrides_short_message(self, bootstrap_substrate):
        # Seed history so the predicate has something to resolve against.
        from core import chat_memory
        chat_memory.append("C1", chat_memory.ROLE_USER, "design a session")
        chat_memory.append("C1", chat_memory.ROLE_BOT, "## Part 1 ...")
        assert composer._is_followup_question("design a wod", "C1") is False

    def test_question_with_history_is_followup(self, bootstrap_substrate):
        from core import chat_memory
        chat_memory.append("C2", chat_memory.ROLE_USER, "design a session")
        chat_memory.append("C2", chat_memory.ROLE_BOT, "## Part 1 ...")
        assert composer._is_followup_question(
            "what weights should I follow?", "C2") is True

    def test_short_refinement_is_followup(self, bootstrap_substrate):
        from core import chat_memory
        chat_memory.append("C3", chat_memory.ROLE_USER, "design a session")
        chat_memory.append("C3", chat_memory.ROLE_BOT, "## Part 1 ...")
        assert composer._is_followup_question("make it shorter", "C3") is True
