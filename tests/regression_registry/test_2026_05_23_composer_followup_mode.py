"""Regression: composer conversational behavior — unified path (ADR-011).

The composer used to have a hardcoded two-path gate (`_is_followup_question`
+ a separate follow-up prompt + a mandatory 4-section schema). ADR-011 removed
it: ONE prompt now carries the full context — profile, Kobe's plan, pain, the
RECENT CONVERSATION, real local time — plus a directive that tells the LLM to
refine the prior session, answer a question about it, or design fresh. The
MODEL decides; no regex gate.

These tests pin that (a) the conversation threads into the prompt, (b) a
follow-up given history is answered rather than regenerated, and (c) the
directive carries the precedence + refine rules. They force the LLM via a fake
that designs when there's no history and answers concisely when there is —
simulating the model's own judgment.
"""
from __future__ import annotations

import pytest

from agents.fraser import composer


_FULL_SESSION = ("## Part 1: Warm-up\n..\n## Part 2: Strength\n"
                 "Back Squat 60 kg (132 lbs) — 60% of 150 kg\n"
                 "## Part 3: WOD\n..\n## Part 4: Cool-down\n..\n"
                 "### Coach's Note\nGo.")
_ANSWER = "Back Squat today is **60 kg (132 lbs)** — 60% of your 150 kg max."

CID = "CHAT-CONV"


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[str] = []

    def _gen(prompt, *a, **k):
        calls.append(prompt)
        # Simulate the model: with prior conversation, answer concisely;
        # otherwise design a full session.
        return _ANSWER if "RECENT CONVERSATION" in prompt else _FULL_SESSION

    from core import io as cio
    monkeypatch.setattr(cio, "llm_generate", _gen)
    return calls


def _is_4section(text: str) -> bool:
    low = text.lower()
    return all(p in low for p in ("part 1", "part 2", "part 3", "part 4"))


class TestUnifiedConversation:
    def test_first_turn_designs_full_session(self, bootstrap_substrate, fake_llm):
        out = composer.design_session("design me a session for today", chat_id=CID)
        assert _is_4section(out)
        assert "RECENT CONVERSATION" not in fake_llm[0]

    def test_followup_threads_history_and_answers(self, bootstrap_substrate,
                                                  fake_llm):
        composer.design_session("design me a session for today", chat_id=CID)
        out = composer.design_session("what weights should I follow?", chat_id=CID)
        # The 2nd prompt carries the prior session as conversation.
        assert "RECENT CONVERSATION" in fake_llm[-1]
        # And the reply is the concise answer, NOT a regenerated session.
        assert "60 kg" in out
        assert not _is_4section(out)

    def test_returns_llm_output_verbatim(self, bootstrap_substrate, fake_llm):
        # No rigid 4-section wrapping/validation any more — the model's
        # output is the athlete's reply.
        out = composer.design_session("design a session", chat_id=CID)
        assert "schema validation failed" not in out
        assert out == _FULL_SESSION

    def test_reset_intent_clears_memory_before_prompt(self, bootstrap_substrate,
                                                      fake_llm):
        from core import chat_memory
        composer.design_session("design me a session", chat_id=CID)
        assert chat_memory.recent(CID)            # turn-1 recorded
        composer.design_session("start over, design from scratch", chat_id=CID)
        # The reset turn's prompt (2nd call) must carry NO prior conversation.
        assert "RECENT CONVERSATION" not in fake_llm[1]


class TestPromptCarriesDirectives:
    def test_precedence_refine_and_clock_present(self, bootstrap_substrate):
        p = composer.build_design_prompt(
            composer.parse_request("clean-based session under 30 minutes"))
        assert "OVERRIDE" in p              # precedence over gym WOD + profile
        assert "REFINES" in p               # refine-vs-new conversation rule
        assert "Current local time" in p    # real clock, not a guess
        assert "MANDATORY" not in p          # 4-section is a default, not forced

    def test_history_block_present_only_with_chat_id(self, bootstrap_substrate):
        from core import chat_memory
        chat_memory.append("C9", chat_memory.ROLE_USER, "design a session")
        chat_memory.append("C9", chat_memory.ROLE_BOT, "## Part 1 ...")
        with_hist = composer.build_design_prompt(
            composer.parse_request("shorter"), chat_id="C9")
        without = composer.build_design_prompt(composer.parse_request("shorter"))
        assert "RECENT CONVERSATION" in with_hist
        assert "RECENT CONVERSATION" not in without
