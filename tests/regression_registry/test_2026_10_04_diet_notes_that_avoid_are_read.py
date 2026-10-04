"""Regression pin (2026-10-04): a diner line that says what it avoids is
not an order of it.

LIVE (2026-10-04, replaying a whole-area follow-up): the model wrote a
diner's line as house-roasted coffee and a waffle, with a note that the
waffle avoided the meats that diner avoids — explaining that the line
keeps the rule — and the diet check read those meats as ordered, a hard
failure that dropped the café. Fictional households below: one that
avoids pork and lamb.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import checks  # noqa: E402
from core.harness import answer as ans  # noqa: E402
from tests.test_verify_render import PROFILE, _add_place, _con, _ctx, _frame, _run  # noqa: E402
from tests.test_verify_render import db  # noqa: E402,F401  (the fixture)

AVOIDS = checks.PORK_HARD | {"lamb"}


@pytest.mark.parametrize("line,ordered", [
    ("coffee and waffle ; sweet waffle option avoiding pork and lamb", []),
    ("the salad ; no pork, lamb or bacon", []),
    ("the plate ; free of pork", []),
    ("tofu bowl ; instead of the bacon", []),
    ("chicken and lamb plate", ["lamb"]),
    ("gluten free pork belly", ["pork"]),
    ("pork dumplings without the lamb", ["pork"]),
    ("the pork chop, not the lamb", ["pork"]),
])
def test_avoided_words_are_not_ordered(line, ordered):
    assert checks._hits(line, AVOIDS, set())[0] == ordered


def test_the_live_line_passes_the_diet_check(db):  # noqa: F811
    household = json.loads(json.dumps(PROFILE))
    household["members"]["primary"]["diet"]["avoid"] = ["pork", "lamb"]
    household["members"]["primary"]["diet"]["ok"] = ["chicken"]
    con = _con(db)
    pid = _add_place(con, "Waffle Roasters", cuisine="cafe")
    con.close()
    pick = ans.Pick(place_id=pid, name="Waffle Roasters", diner_lines=[
        {"member": "primary", "dish": "Specialty house-roasted coffee and GF waffle",
         "note": "Sweet waffle option avoiding pork and lamb"},
        {"member": "spouse", "dish": "Vegetarian quiche"}])
    report = _run(_ctx(db, _frame(), ans.Answer(kind="picks", picks=[pick]), profile=household))
    assert [f.message for f in report.hard() if f.outcome == "P1"] == []
