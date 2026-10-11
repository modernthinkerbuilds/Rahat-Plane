"""Regression (2026-10-11): the partner's-profile rule reads a phone's apostrophe and leaves a couple alone.

Follow-up to 1a4b602 ("match my wife's profile" means her rules). The
household types on phones, which send a curly apostrophe ("wife’s"); the
rule matched only the straight one. And the live ask of Oct 9 ("suggest
a cuisine and a restaurant per me and my wife’s profiles") is both
adults: "me and my wife" now keeps the couple, as does "our".
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire, _wire, world  # noqa: E402,F401

NONE_FOUND = [{"kind": "none", "none_claim": {"area": "the stay", "hours": "18:40", "searched": ["the knowledge base"]}}]
EVERYONE = dict(FRAME, cuisines=["vegan"], meal="lunch", assumptions=[])


def _diners(world, monkeypatch, text: str) -> set[str]:
    _wire(monkeypatch, Wire(frame=EVERYONE, rounds=[[]], answers=NONE_FOUND))
    res = hrun.run(world["tenant"], text, chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    return set(res.frame.diners)


def test_a_phones_apostrophe_still_names_her_rules(world, monkeypatch):
    assert _diners(world, monkeypatch, "Can you find the best bagel spot that matches my wife’s profile near me ?") == {"spouse"}


def test_me_and_my_wife_is_the_couple(world, monkeypatch):
    assert _diners(world, monkeypatch, "dinner ideas per me and my wife’s profile") == {"primary", "spouse", "infant"}


def test_our_profile_is_everyone(world, monkeypatch):
    assert _diners(world, monkeypatch, "a place that suits our profile and my wife's diet") == {"primary", "spouse", "infant"}
