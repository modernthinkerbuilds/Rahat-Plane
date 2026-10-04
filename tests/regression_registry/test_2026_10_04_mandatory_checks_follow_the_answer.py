"""Regression pin (2026-10-04): an answer that names places is held to the
names_places mandatory set, whatever the frame read the message as.

Before the fix the contract was assembled from the frame alone. "All of
these are not well rated on yelp" read as a verdict, so the contract had
no P1–P10, E1–E5, A1 or A3, and the answer that followed (a new pick) went
out unchecked: golden case T13 sent an ice-cream parlor that was shut at
that hour. ADR-018 §2.2: mandatory sets can't be deselected.
"""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import answer as ans  # noqa: E402
from core.harness import contract as hc  # noqa: E402
from core.harness import frame as hf  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.kb import query, schema as kb  # noqa: E402
from tests.test_harness_loop import Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)

NOW = datetime(2026, 9, 22, 18, 40)        # Tuesday evening; the bagel shop closes at 3
LIB = hc.load_library("agents/bourdain/outcomes.json")
VERDICT_FRAME = {"speaker": "primary", "intents": ["verdict"], "diners": ["primary", "spouse", "infant"],
                 "refers_to_last_answer": True,
                 "verdicts": [{"kind": "reported_score", "text": "not well rated on yelp"}]}


def test_contract_gains_the_mandatory_set_when_the_answer_names_places():
    frame = hf.Frame.model_validate(VERDICT_FRAME)
    contract = hc.assemble(frame, LIB)
    assert "E2" not in contract.ids() and "P1" not in contract.ids()
    no_places = ans.Answer(kind="ack", lines=["noted"])
    assert hc.complete_for_answer(contract, LIB, frame, no_places) is contract
    picks = ans.Answer(kind="picks", picks=[ans.Pick(place_id="p1", name="X")])
    full = hc.complete_for_answer(contract, LIB, frame, picks)
    for oid in LIB.mandatory["names_places"]:
        assert oid in full.ids(), oid
    e2 = next(o for o in full.outcomes if o.id == "E2")
    assert e2.priority == "must" and "the answer names places" in e2.reason and not e2.conditional
    plan = ans.Answer(kind="plan", plan=[ans.Slot(time="12:30", label="lunch", place_id="p1")])
    assert set(LIB.mandatory["day_plan"]) <= set(hc.complete_for_answer(contract, LIB, frame, plan).ids())


def test_a_closed_pick_after_a_verdict_is_caught_and_dropped(world, monkeypatch):  # noqa: F811
    con = kb.connect(world["db"])
    bagel = query.find_place(con, "Bagel Corner")[0]["place_id"]      # "Daily 8–15": shut at 6:40 PM
    con.close()
    answer = {"kind": "picks", "picks": [{
        "place_id": bagel, "name": "Bagel Corner", "why": "better rated",
        "diner_lines": [{"member": "primary", "dish": "GF bagel", "note": "gluten-free"},
                        {"member": "spouse", "dish": "veggie bagel"}]}]}
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=VERDICT_FRAME, rounds=[[]], answers=[answer]))
    res = hrun.run(world["tenant"], "All of these are not well rated on yelp", chat_id="c1", now=NOW,
                   db_path=world["db"], judge_enabled=False)
    assert "E2" in res.contract.ids()                    # the set stays after the pick is dropped
    e2 = next(o for o in res.contract.outcomes if o.id == "E2")
    assert "the answer names places" in e2.reason
    assert res.control.dropped == [bagel]
    assert "*Bagel Corner*" not in res.text and "not open at that time" in res.text
