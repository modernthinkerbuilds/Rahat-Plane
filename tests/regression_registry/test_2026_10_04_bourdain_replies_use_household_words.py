"""Regression pin (2026-10-04): the harness's replies call people "you",
"your partner", "baby" — never the member ids.

Found asking the harness a real household coffee question before serving
it: the answer was right, but it read "_primary + spouse + baby · in <the
area>_",
"primary: Roast coffee and gluten-free pastry", and "Assuming: The party
consists of primary, spouse, and infant". Member ids are for code and
tests; the household reads words.

THE PINS.
  * profile.labels: the speaker is "you"; a member's own "label" in the
    profile wins; otherwise a word for the role; two seniors are told
    apart; a guest who isn't a member is "guest".
  * render() with labels prints the words in the header and diner lines,
    and without labels still prints the ids (logs and tests).
  * End to end, the tenant passes the words to the renderer and to the
    executor (the moment's `call_them`), so the reply carries no id.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import profile as cprofile  # noqa: E402
from core import llm_chat  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)

HOUSEHOLD = {"members": {"primary": {"role": "primary"}, "spouse": {"role": "spouse"},
                         "infant": {"role": "infant"}, "senior_1": {"role": "senior"},
                         "senior_2": {"role": "senior", "label": "Grandpa"}}}


def test_labels_name_the_speaker_you_and_everyone_else_by_a_word():
    words = cprofile.labels(HOUSEHOLD, "primary", ["primary", "spouse", "infant", "guest_1"])
    assert words["primary"] == "you" and words["spouse"] == "your partner" and words["infant"] == "baby"
    assert words["senior_1"] == "grandparent 1" and words["senior_2"] == "Grandpa"
    assert words["guest_1"] == "guest"
    assert cprofile.labels(HOUSEHOLD, "spouse")["primary"] == "your partner"
    assert cprofile.labels(HOUSEHOLD, "spouse")["spouse"] == "you"


def test_the_reply_carries_words_not_member_ids(world, monkeypatch):  # noqa: F811
    seen = []
    wire = Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]])

    def spy(req):
        seen.append(str(req))
        return wire(req)

    monkeypatch.setattr(llm_chat, "_call_wire", spy)
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"],
                   judge_enabled=False)
    assert res.text, res.error
    head = res.text.splitlines()[0]
    assert "you + your partner + baby" in head and "primary" not in head
    assert "\n   you: " in res.text and "\n   your partner: " in res.text
    assert "primary:" not in res.text and "spouse:" not in res.text
    assert any('"call_them": {"primary": "you", "spouse": "your partner"' in s for s in seen), \
        "the executor was not told the words"
    # logs and tests still read ids
    assert res.frame.header().startswith("dinner · primary + spouse + baby")
