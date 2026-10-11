"""Regression (2026-10-11): "match my wife's profile" means her rules, and only hers.

LIVE (the trip, 2026-09-21/22; the golden cases T08 and T11 failed FRAME
in today's recording): "Give me vegan places that match my wife's
profile for lunch tomorrow" and "Can you find the best bagel spot that
matches my wife's profile near me?". The model read the spouse alone,
then the household's usual party came back, twice over: the cheap
second reader called the narrow reading unfaithful and the re-read named
everyone (T08), or the QA-09 rule restored the party because the message
had no party word it knew (T11). Every check then ranked her lunch under
his rules too, which is the case's trap.

Fix (agents/bourdain/harness.refine_frame, deterministic, after the
model): "my wife's / husband's / partner's profile, diet, preferences,
rules…" narrows the eaters to the other adult, whatever the model read,
unless the message also says the whole party ("for all of us"). The
QA-09 restore no longer undoes it. No prompt changed.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire, _wire, world  # noqa: E402,F401

NONE_FOUND = [{"kind": "none", "none_claim": {"area": "the stay", "hours": "18:40", "searched": ["the knowledge base"]}}]
JUST_HER = dict(FRAME, diners=["spouse"], cuisines=["vegan"], meal="lunch", assumptions=[])
EVERYONE = dict(FRAME, cuisines=["vegan"], meal="lunch", assumptions=[])


def _read(world, monkeypatch, frame: dict, text: str):
    _wire(monkeypatch, Wire(frame=frame, rounds=[[]], answers=NONE_FOUND))
    res = hrun.run(world["tenant"], text, chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.frame is not None
    return res.frame.diners


def test_the_narrow_reading_stays(world, monkeypatch):
    assert _read(world, monkeypatch, JUST_HER,
                 "Can you find the best bagel spot that matches my wife's profile near me ?") == ["spouse"]


def test_a_reading_of_everyone_is_narrowed(world, monkeypatch):
    assert _read(world, monkeypatch, EVERYONE,
                 "Give me vegan places that match my wife's profile fore lunch tommorow") == ["spouse"]


def test_the_whole_party_said_keeps_everyone(world, monkeypatch):
    assert set(_read(world, monkeypatch, EVERYONE,
                     "lunch for all of us that matches my wife's diet")) == {"primary", "spouse", "infant"}


def test_a_message_that_names_no_one_still_keeps_the_usual_party(world, monkeypatch):
    """QA-09 is unchanged: "near me" is a place, not a party."""
    solo = dict(FRAME, diners=["primary"], meal="coffee", cuisines=[], assumptions=[])
    assert set(_read(world, monkeypatch, solo, "coffee and pastries near me")) == {"primary", "spouse", "infant"}
