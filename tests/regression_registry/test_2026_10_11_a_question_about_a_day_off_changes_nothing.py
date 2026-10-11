"""Regression (2026-10-11): a question about skipping or moving a day changes nothing.

Found while fixing QA-2026-10-04-11 (a what-if is not a lookup), tried
on a sandboxed plan: Kobe's deterministic plan edits acted on questions.
"should I skip Friday?", "what if I skip Friday?", "is it ok to skip the
WOD today?" and "should I skip the run tomorrow?" each marked the day
unavailable and replanned the week; "do you think I should move Friday's
session to Saturday?" swapped the days. A household question about the
weekend ("what if we skip the zoo on Saturday") took Saturday off the
training plan.

Fix (agents/the_scientist/handler._try_plan_mutation): swaps and days
off take the question guard that rest days and picks already had
(`_looks_like_question`: a "?" at the end, or an opening "should",
"what", "is", "can"…). A question goes to Kobe's reasoner, which answers
it and has the swap and pick tools if it is really a request.
Instructions ("skip friday", "move friday to saturday") still edit.
"""
from __future__ import annotations

import pytest

from agents.the_scientist import handler as h
from agents.the_scientist import state as st

QUESTIONS = [
    "should I skip friday?",
    "what if I skip friday?",
    "is it ok to skip the WOD today?",
    "do you think I should move friday's session to saturday?",
    "should I skip the run tomorrow?",
    "what if we skip the zoo on saturday",
]


@pytest.mark.parametrize("q", QUESTIONS)
def test_a_question_changes_nothing(q):
    monday, _ = st.week_bounds()
    before = st.get_prefs(monday)
    assert h._try_plan_mutation(q) is None
    assert st.get_prefs(monday) == before


@pytest.mark.parametrize("cmd,reply", [
    ("skip friday", "✅ Marked Fri unavailable"),
    ("friday unavailable", "✅ Marked Fri unavailable"),
    ("move friday to saturday", "✅ Swapped: Fri → Sat"),
])
def test_an_instruction_still_edits(cmd, reply):
    out = h._try_plan_mutation(cmd)
    assert out and out.startswith(reply), out
