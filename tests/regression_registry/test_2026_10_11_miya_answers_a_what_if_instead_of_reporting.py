"""Regression (2026-10-11): Miya answers a what-if about his numbers instead of reporting the week.

LIVE (QA-2026-10-04-11; vault/miya_v2.log 2026-10-03 20:07): "If I were
to hit … calories a day, how much should I burn?" matched the status rung,
went to Kobe's dispatcher as 'weekly_remaining', and came back as the
canned "Week so far" report, which answers a different question. The
same shapes reach rungs that write: "how much should I burn a day to
reach 187 by Thanksgiving" read as a goal statement to set.

Fix (new_plane/miya_runner/delegate_classifier, rung 2b): a what-if
marker ("if I were to", "what if", "suppose", "if I need to … from here
on", "how much should I … to reach") together with his numbers (burn,
calories, weight, target, goal, pace…) goes to the orchestrator. It
fetches Kobe's goal and week and the model does the arithmetic; nothing
is written. Slash commands and @-addresses still decide first, plain
status questions keep their lookups, and Kobe's writes stay Kobe's.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_sim.orchestrator import classify_intent

WHAT_IFS = [
    "If I were to average 900 calories a day, how much should I burn?",            # the live shape
    "If I need to burn an average of 950 calories a day this year, how many should I burn a day from here on?",
    "how much should I burn a day to reach 187 by thanksgiving",
    "what if I only burn 600 on rest days, am I still on pace?",
    "Suppose I take Friday off, what's my weekly target then?",
]


@pytest.mark.parametrize("ask", WHAT_IFS)
def test_a_what_if_goes_to_the_model_with_kobes_facts(ask):
    assert classify_delegation(ask) == ("orchestrate", ask)
    assert classify_intent(ask)["needs_kobe"] is True        # the goal and the week are fetched first


@pytest.mark.parametrize("msg,path", [
    ("how much should I burn today", "kobe_route"),           # status: the lookup answers
    ("how many did I burn this week", "kobe_route"),
    ("187 by thanksgiving", "kobe_route"),                    # Kobe's writes stay Kobe's
    ("Can you update my weekly target to 7000 kcal", "kobe_route"),
    ("7000 per week from now on", "kobe_route"),
    ("burned 800 calories", "kobe_route"),
    ("@kobe if I were to burn 900 a day, how much is left?", "kobe_route"),   # an address decides
    ("/week", "kobe_route"),
    ("what if it rains saturday, plan our weekend", "genie_route"),          # no numbers: not his
])
def test_what_decides_first_still_decides(msg, path):
    assert classify_delegation(msg)[0] == path, msg
