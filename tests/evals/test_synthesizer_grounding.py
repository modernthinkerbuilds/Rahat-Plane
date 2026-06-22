"""Synthesizer grounding evals — the Bug-H + Bug-I prevention layer.

Shape borrowed from `tests/evals/test_fraser_grounding_evals.py`:
construct a facts dict, build the prompt that Gemini will actually see
(`synthesizer._build_prompt`), and assert *structural properties* of that
prompt. This is fully deterministic — we never call the LLM. (End-to-end
LLM behavior is the opt-in `RAHAT_RUN_JUDGE=1` path elsewhere.)

The synthesizer's output must be GROUNDED in the facts it was given:
  • reflect arbitration verdicts (the Bug-H fix), and
  • not invent / mis-merge tool results (the Bug-I fix).

Two tests are `xfail(strict=True)` — they pin defects the architect has
not yet fixed (PF-2026-06-10-001, -004). When the fix lands they flip to
hard failures, signalling "remove the xfail".
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.synthesizer import _build_prompt, SYSTEM_PROMPT


# ─── Bug-H prevention — arbitration verdict must surface ───────────────
class TestArbitrationVerdictGrounding:
    def test_behind_pace_verdict_is_surfaced_in_prompt(self):
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts={"recalibration": {"result": {"behind_pace": True,
                    "summary": "Ahead of pace — comfortable buffer."}}},
            arbitration={"rule": "behind_pace",
                         "guidance": "User is behind pace — do not say "
                                     "'ahead of pace' or 'comfortable buffer'."},
            fraser_text=None, recent_signals=None,
        )
        # The verdict must be present AND labelled as authoritative.
        assert "ARBITRATION VERDICT: behind_pace" in prompt
        assert "Honor this" in prompt
        assert "behind" in prompt.lower()

    def test_goal_close_verdict_is_surfaced(self):
        prompt = _build_prompt(
            user_message="how am i doing",
            facts={"active_goal": {"result": {"active": True}}},
            arbitration={"rule": "goal_close",
                         "guidance": "Goal date is < 1 week away. "
                                     "Acknowledge the deadline directly."},
            fraser_text=None, recent_signals=None,
        )
        assert "ARBITRATION VERDICT: goal_close" in prompt
        assert "deadline" in prompt.lower()

    def test_system_prompt_carries_honesty_directive(self):
        # The load-bearing Bug-H line — pinned so a prompt refactor that
        # drops it fails here rather than silently in production.
        s = SYSTEM_PROMPT.lower()
        assert "do not say" in s          # the prohibition is present
        assert "behind pace" in s         # the honesty directive is present
        assert "comfortable buffer" in s  # the exact Bug-H phrase is named


# ─── Bug-I prevention — no invented tool results ──────────────────────
class TestNoToolResultHallucination:
    def test_prompt_never_injects_not_synced_phrase(self):
        """When gym_wod is absent, the prompt must NOT itself instruct
        Gemini to say 'hasn't been synced' — that phrasing was the Bug-I
        hallucination, and it must not originate in the prompt builder."""
        prompt = _build_prompt(
            user_message="what is tomorrow's WOD",
            facts={"recalibration": {"result": {"behind_pace": False}}},
            arbitration=None, fraser_text=None, recent_signals=None,
        )
        assert "hasn't been synced" not in prompt.lower()
        assert "not been synced" not in prompt.lower()

    def test_anti_fabrication_directive_present(self):
        # Whenever a fact could be empty/error-shaped, the prompt must
        # carry the "don't invent numbers" instruction.
        prompt = _build_prompt(
            user_message="what is wednesday's WOD",
            facts={"gym_wod": {"error": "gym source unreachable", "day": "wed"}},
            arbitration=None, fraser_text=None, recent_signals=None,
        )
        low = prompt.lower()
        assert "do not fabricate" in low
        assert "never invent" in low

    def test_present_wod_marked_source_of_truth_and_read_back(self):
        wod_text = "Bench Press 7-5-3-7-5-3; then 3 RFT: 15 cal row, 12 box jumps"
        prompt = _build_prompt(
            user_message="what's wednesday's workout",
            facts={"gym_wod": {"result": {"text": wod_text}, "day": "wed"}},
            arbitration=None, fraser_text=None, recent_signals=None,
        )
        assert "SOURCE OF TRUTH" in prompt
        assert wod_text in prompt          # read back literally
        assert "do not invent" in prompt.lower()

    def test_user_question_is_quoted_in_prompt(self):
        # Grounding starts with the question — the synth must see it.
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts={}, arbitration=None, fraser_text=None, recent_signals=None,
        )
        assert 'User said: "where am I on pace"' in prompt


# ─── Fix verifications (PF-001 / PF-004 landed 2026-06-10) ────────────
# These tests previously documented pending bugs with xfail-strict. Now
# they verify the fix using the orchestrator's actual call pattern:
#   - PF-001: _build_prompt now accepts `intent`; FACTS are scoped
#   - PF-004: contradictory recalibration summary is suppressed entirely
class TestSynthGroundingFixesLanded:
    def test_wod_query_prompt_excludes_unrelated_pace_facts(self):
        """PF-001 fix: when intent='workout_lookup', pace facts no longer
        leak into the WOD-query prompt. This was the second half of Bug I
        (2026-06-09)."""
        prompt = _build_prompt(
            user_message="what is tomorrow's WOD",
            facts={"recalibration": {"result": {"behind_pace": False,
                    "summary": "1,433 kcal ahead of plan"}}},
            arbitration=None, fraser_text=None, recent_signals=None,
            intent="workout_lookup",
        )
        # The user asked ONLY about the WOD. Pace facts must be excluded.
        assert "1,433" not in prompt, (
            "Bug-I would recur: pace fact leaked into WOD-intent prompt"
        )
        assert "ahead of plan" not in prompt.lower()

    def test_contradictory_summary_not_passed_verbatim(self):
        """PF-004 fix: when arbitration verdict contradicts the summary
        text (Bug H, 2026-06-08: behind_pace verdict + 'Ahead of pace'
        summary), the misleading text is SUPPRESSED, not passed verbatim."""
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts={"recalibration": {"result": {"behind_pace": True,
                    "summary": "Ahead of pace — comfortable buffer."}}},
            arbitration={"rule": "behind_pace",
                         "guidance": "User is behind pace — be honest."},
            fraser_text=None, recent_signals=None,
            intent="pace_query",
        )
        # The contradictory summary text MUST NOT appear in the prompt.
        # If it did, Gemini Flash could paraphrase it — that's how Bug H
        # shipped. The marker "SUPPRESSED" replaces it.
        assert "ahead of pace" not in prompt.lower(), (
            "Bug-H would recur: contradictory summary text in prompt"
        )
        assert "SUPPRESSED" in prompt, (
            "expected the suppression marker so the omission stays auditable"
        )

    def test_pace_query_keeps_relevant_facts(self):
        """PF-001 must NOT over-scope: a pace query keeps recalibration
        + today_target + pace facts. (Fixes that throw away signal are
        worse than the bug they prevent.)"""
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts={"recalibration": {"result": {"behind_pace": False,
                    "summary": "On pace — 1,200 / 2,100"}}},
            arbitration=None, fraser_text=None, recent_signals=None,
            intent="pace_query",
        )
        assert "1,200" in prompt or "on pace" in prompt.lower(), (
            "pace_query intent must include recalibration fact"
        )

    def test_wod_intent_keeps_gym_wod_fact(self):
        """PF-001 must NOT over-scope on the other axis: a WOD lookup
        KEEPS the gym_wod fact (it's the answer)."""
        prompt = _build_prompt(
            user_message="what is tomorrow's WOD",
            facts={"gym_wod": {"result": {"text": "Bench Press 5x5",
                                          "day_resolved": "wed"}}},
            arbitration=None, fraser_text=None, recent_signals=None,
            intent="workout_lookup",
        )
        assert "Bench Press 5x5" in prompt
