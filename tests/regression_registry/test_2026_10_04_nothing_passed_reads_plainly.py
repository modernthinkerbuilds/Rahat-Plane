"""Regression pin (2026-10-04): when every pick is dropped, the reply
doesn't contradict itself and says why in the household's words.

LIVE (2026-10-04, a whole-area board-game café ask, replayed): the reply
read "<café> offers a full tabletop board game space paired with cafe
coffee…" and, two lines down, "Nothing I found passed the checks:
<café>: '<dish>' for primary is in the gluten scope with no GF version
named" — the model's line about a place the household was not being
sent to, then the checks' own words, member ids and all.

THE PINS.
  * The model's lines about a dropped pick go; its other lines stay.
  * The gap line names each place found with a plain reason, never the
    checks' message or a member id.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core import verify as cverify  # noqa: E402
from core.harness import control as ccontrol  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)


def test_a_line_about_a_place_is_found_by_its_telling_words():
    assert ccontrol._mentions("Lantern Games Alpha offers a full tabletop space", "Lantern Games Alpha - Cafe")
    assert ccontrol._mentions("Try the Pub Grub burger", "Pub Grub")
    assert not ccontrol._mentions("I searched three towns for a cozy café", "The Cozy Café")
    assert not ccontrol._mentions("The coffee here is fine", "Blue Lantern Coffee")


def test_every_pick_dropped_reads_plainly(world, monkeypatch):  # noqa: F811
    bad = {"kind": "picks", "picks": [world["bad_pick"]],
           "lines": ["Pub Grub is a hearty pub with a lively room.", "I looked around the stay first."]}
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=FRAME, rounds=world["rounds"], answers=[bad]))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False,
                   time_budget_s=0.0)
    assert res.control.dropped == [world["bad"]] and res.control.gap_stated
    assert "hearty pub" not in res.text and "I looked around the stay first." in res.text
    gap = next(ln for ln in res.text.splitlines() if ln.startswith("Nothing I found passed the checks"))
    assert "Pub Grub (" in gap and "primary" not in gap and "spouse" not in gap
    reasons = set(gap.split("Pub Grub (", 1)[1].split(")", 1)[0].split("; "))
    tenant = world["tenant"]
    words = {tenant.gap_reason(cverify.Failure(o, "", "")) for o in ("P1", "P2", "P5", "P7", "P8", "P9", "P10", "E2", "E5")}
    assert reasons <= words | {"its rating is under your bar"}
