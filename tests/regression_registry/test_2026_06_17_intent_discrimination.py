"""Round-2 P1-1 — intent-vs-statement discrimination for the NL intent layer.

Round 2 showed the layer is read-only-safe (0/10 mutation paraphrases claimed)
but mis-routes keyword-only shapes on a FRESH corpus: a past-tense LOG
statement ("I did crossfit today" → workout_today) and a TIMING question
("when should I weigh in" → current_weight) are not value/state reads.

Fix: classify() abstains on those two shapes BEFORE scoring (core/intent_layer
._is_log_or_timing). This file is the HELD-OUT corpus (not the tuner) and pins
mis-route < 5% with the shipped thresholds — so it's tuned, not overfit.
"""
from __future__ import annotations

import pytest

from core import intent_layer as il


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    il._clear_registry()
    il._REGISTERED = False
    il.ensure_registered()
    yield
    il._clear_registry()
    il._REGISTERED = False


# (text, expected_intent_or_None). None => must ABSTAIN.
_HELD_OUT = [
    # past-tense LOG statements → abstain (not a read)
    ("I did crossfit today", None),
    ("I just finished my run", None),
    ("I hit a deadlift PR yesterday", None),
    ("we already trained this morning", None),
    ("I logged my weight earlier", None),
    ("I went for a 10k", None),
    # TIMING questions → abstain (not a value lookup)
    ("when should I weigh in", None),
    ("when can I do my next session", None),
    ("what time should I train", None),
    ("how often should I deload", None),
    # genuine READS → must route
    ("what does my week look like", "show_plan_this_week"),
    ("hows my pace looking", "pace"),
    ("am I keeping pace", "pace"),
    ("what movements am I avoiding", "list_dislikes"),
    ("how did last week go", "last_week"),   # past tense but NOT first-person action
    ("whats my plan for next week", "show_plan_next_week"),
    ("what are my dislikes", "list_dislikes"),
    # mutations → abstain (read-only boundary)
    ("log my weight as 198", None),
    ("set my deadlift to 200", None),
    ("tolerate snatch", None),
]


def _name(m):
    return None if m is None else m.name


@pytest.mark.parametrize("text,expected", _HELD_OUT)
def test_discrimination_cases(text, expected):
    got = _name(il.classify(text))
    if expected is None:
        assert got is None, f"{text!r} should abstain, got {got!r}"
    else:
        assert got == expected, f"{text!r} → {got!r}, expected {expected!r}"


def test_held_out_misroute_under_5pct():
    mis = 0
    for text, expected in _HELD_OUT:
        got = _name(il.classify(text))
        ok = (got == expected) if expected else (got is None)
        if not ok:
            mis += 1
    rate = mis / len(_HELD_OUT)
    print(f"\n[intent_layer] held-out mis-route: {rate:.0%} "
          f"({mis}/{len(_HELD_OUT)})")
    assert rate < 0.05, f"mis-route {rate:.0%} exceeds the 5% generalization bar"


def test_specific_round2_misroutes_now_abstain():
    """The two named round-2 mis-routes specifically."""
    assert il.classify("when should I weigh in") is None
    assert il.classify("I did crossfit today") is None
