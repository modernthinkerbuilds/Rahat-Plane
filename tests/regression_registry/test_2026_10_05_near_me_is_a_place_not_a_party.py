"""Regression pin (2026-10-05): "near me" is a place, not a party.

QA-2026-10-04-09 (specs/test_lead/PROPOSED_FIXES.md): the model read
"coffee and pastries near me" — and, in the replays of the morning's
asks, "I can drive 50 minutes" — as the speaker alone, and nothing
restored the household's usual party (BR-P4: both adults and the baby
when the ask doesn't say). The other adult's diet rules then dropped out
of every check, and the eval now scores exactly this (FRAME).

THE PINS (BourdainTenant.refine_frame, with the ask in ctx).
  * A message that names no one keeps the usual party; an assumption that
    read the speaker as solo goes with the narrowing.
  * A party word keeps the model's narrower reading: "just me", "for my
    wife", "with friends", "date night", "alone".
  * A follow-up of an ask that had narrowed the party keeps it narrow.
  * Guests are the model's to read: a frame with guests is left alone.
The QA pin, tests/quality_gate/test_reading_and_routing_gaps.py::
test_near_me_is_a_place_not_a_party, passes with this (its xfail is gone).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness.frame import parse_frame  # noqa: E402
from tests.quality_gate.test_reading_and_routing_gaps import test_near_me_is_a_place_not_a_party  # noqa: E402,F401
from tests.test_harness_loop import FRAME, world  # noqa: E402,F401  (the fixture)

USUAL = ["primary", "spouse", "infant"]


def _refine(world, ask, diners, **kw):  # noqa: F811
    ctx = {"ask": ask, "diners": USUAL, "last_answer": kw.pop("last_answer", {})}
    frame = parse_frame(dict(FRAME, diners=diners, **kw))
    return world["tenant"].refine_frame(frame, ctx)[0]


@pytest.mark.parametrize("ask", ["coffee and pastries near me", "I can drive 50 minutes", "gelato downtown"])
def test_a_message_that_names_no_one_keeps_the_usual_party(world, ask):  # noqa: F811
    f = _refine(world, ask, ["primary"], assumptions=["Interpreted 'I can drive' as a solo visit by you"])
    assert f.diners == USUAL and f.assumptions == []


@pytest.mark.parametrize("ask,diners", [
    ("just me — coffee near the stay", ["primary"]),
    ("coffee for my wife", ["spouse"]),
    ("date night, somewhere quiet", ["primary", "spouse"]),
    ("I'm alone this afternoon, a café?", ["primary"]),
    ("lunch for the two of us", ["primary", "spouse"]),
])
def test_a_party_word_keeps_the_narrower_reading(world, ask, diners):  # noqa: F811
    assert _refine(world, ask, diners).diners == diners


def test_a_follow_up_of_a_narrowed_ask_stays_narrow(world):  # noqa: F811
    last = {"frame": dict(FRAME, diners=["primary"])}
    f = _refine(world, "other options please?", ["primary"], refers_to_last_answer=True, last_answer=last)
    assert f.diners == ["primary"]


def test_guests_are_left_to_the_model(world):  # noqa: F811
    f = _refine(world, "where should I take my mom", ["primary"], guests=[{"label": "mom"}])
    assert f.diners == ["primary"]
