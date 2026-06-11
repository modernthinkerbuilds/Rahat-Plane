"""Snapshot tests for `synthesizer._build_prompt`.

The synth prompt is a load-bearing string. If a refactor drops the
"source of truth / do not paraphrase" line or the arbitration block, the
grounding evals MAY still pass while Gemini starts hallucinating in
production. These tests pin the canonical prompt structure per
(intent, facts-shape) tuple, by asserting required and forbidden
substrings of the EXACT prompt the model will see.

These pin the real strings in `synthesizer.SYSTEM_PROMPT` /
`_build_prompt` (verified against the source), not the placeholder
wording in the 15HR plan sketch.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.synthesizer import _build_prompt


# Each scenario: build the prompt, assert required substrings present and
# forbidden substrings absent.
SCENARIOS = [
    # ── Bug-H pattern: arbitration verdict must dominate ──────────────
    {
        "id": "bugH_pace_query_with_behind_pace_arbitration",
        "user_message": "where am I on pace",
        "facts": {"recalibration": {"result": {"behind_pace": True,
                  "summary": "Ahead of pace — comfortable buffer."}}},
        "arbitration": {"rule": "behind_pace",
                        "guidance": "User is behind pace — do not say "
                                    "'ahead of pace' or 'comfortable buffer'."},
        "expected": ["ARBITRATION VERDICT: behind_pace",
                     "Honor this", "do not contradict it",
                     'Do not say "ahead of',   # honesty line (Bug-H)
                     "behind pace"],
        "forbidden": ["ignore the arbitration", "feel free to ignore"],
    },
    # ── Bug-I pattern: synced WOD is source of truth, no paraphrase ───
    {
        "id": "bugI_wod_lookup_with_gym_data",
        "user_message": "what's the workout for Wednesday",
        "facts": {"gym_wod": {"result": {"text": "Bench Press 5x5; 3 RFT"},
                  "day": "wed"}},
        "arbitration": None,
        "expected": ["SOURCE OF TRUTH", "Bench Press 5x5; 3 RFT",
                     "do not invent",
                     "paraphrase it into something else"],
        "forbidden": ["feel free to paraphrase", "hasn't been synced",
                      "you may invent"],
    },
    # ── System prompt invariants present on every call ────────────────
    {
        "id": "system_prompt_invariants_present",
        "user_message": "hello",
        "facts": {},
        "arbitration": None,
        "expected": ["You are Miya", "Do not fabricate", "never invent numbers",
                     "Synced WOD is the source of truth",
                     "Now write Miya's response to the user."],
        "forbidden": [],
    },
    # ── goal_close arbitration ────────────────────────────────────────
    {
        "id": "goal_close_arbitration",
        "user_message": "how am i doing",
        "facts": {"active_goal": {"result": {"active": True}}},
        "arbitration": {"rule": "goal_close",
                        "guidance": "Goal date is < 1 week away. "
                                    "Acknowledge the deadline directly."},
        "expected": ["ARBITRATION VERDICT: goal_close", "deadline"],
        "forbidden": [],
    },
    # ── chat-memory block when memory is on (Bug-J "Yes" follow-up) ────
    {
        "id": "chat_memory_block_present",
        "user_message": "Yes",
        "facts": {},
        "arbitration": None,
        "chat_memory_block": "user: my hip hurts\nbot: want me to swap squats?",
        "expected": ["CONVERSATION SO FAR", "swap squats",
                     "they are confirming a question YOU asked"],
        "forbidden": [],
    },
    # ── Fraser draft is labelled ──────────────────────────────────────
    {
        "id": "fraser_draft_labelled",
        "user_message": "design me a metcon",
        "facts": {},
        "arbitration": None,
        "fraser_text": "21-15-9 thrusters + pull-ups",
        "expected": ["FRASER'S DRAFT:", "21-15-9 thrusters + pull-ups"],
        "forbidden": [],
    },
    # ── recent cross-agent signals are flagged as maybe-irrelevant ────
    {
        "id": "recent_signals_flagged_maybe_irrelevant",
        "user_message": "where am I on pace",
        "facts": {},
        "arbitration": None,
        "recent_signals": [{"agent": "fraser", "type": "design_done",
                            "payload": {"workout": "Cindy"}}],
        "expected": ["RECENT CROSS-AGENT SIGNALS", "may or may not be relevant"],
        "forbidden": [],
    },
    # ── recalibration summary rendered under FACTS ────────────────────
    {
        "id": "recalibration_summary_rendered",
        "user_message": "how's my week",
        "facts": {"recalibration": {"result": {"summary": "On pace, 60% of target"}}},
        "arbitration": None,
        "expected": ["FACTS FROM SPECIALISTS:", "recalibration.summary",
                     "On pace, 60% of target"],
        "forbidden": [],
    },
    # ── empty facts: no FACTS block, but scaffolding intact ───────────
    {
        "id": "empty_facts_no_facts_block",
        "user_message": "yo",
        "facts": {},
        "arbitration": None,
        "expected": ['User said: "yo"', "Now write Miya's response"],
        "forbidden": ["FACTS FROM SPECIALISTS:"],
    },
    # ── direct-question-first directive present ───────────────────────
    {
        "id": "answer_direct_question_first_directive",
        "user_message": "when is my next session",
        "facts": {},
        "arbitration": None,
        "expected": ["If the user asked a direct question",
                     "answer\n    it first"],
        "forbidden": [],
    },
]


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s["id"])
def test_prompt_snapshot(sc):
    prompt = _build_prompt(
        user_message=sc["user_message"],
        facts=sc["facts"],
        arbitration=sc["arbitration"],
        fraser_text=sc.get("fraser_text"),
        recent_signals=sc.get("recent_signals"),
        chat_memory_block=sc.get("chat_memory_block"),
    )
    for sub in sc["expected"]:
        assert sub in prompt, (
            f"{sc['id']}: prompt MISSING required substring {sub!r}")
    for sub in sc["forbidden"]:
        assert sub not in prompt, (
            f"{sc['id']}: prompt CONTAINS forbidden substring {sub!r}")


def test_at_least_one_bugH_and_one_bugI_scenario():
    ids = {s["id"] for s in SCENARIOS}
    assert any("bugH" in i for i in ids)
    assert any("bugI" in i for i in ids)
