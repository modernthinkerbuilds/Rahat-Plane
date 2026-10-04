"""Regression pin (2026-10-04): a repair can rescue a place.

LIVE (2026-10-04, a whole-area board-game café ask, replayed on the
fixed code): the first answer's JSON was incoherent and its repair spent
the one repair that always runs; the next answer named a good café with
one diner line the diet check refused, the latency budget was gone, and
the café was dropped unrepaired — the family got "nothing passed".

And a repair could never see what it learned: the evidence view cached
each place from the first answer's checks, so a status, rating or hours
the repair round recorded for a place already named was invisible to the
checks that judged the repaired answer.

THE PINS.
  * After a JSON repair, the first repair of the checks still runs, with
    the latency budget already spent.
  * A fact a repair round records for a place the first answer named is
    what the next check reads.
"""
from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import execute as cexecute  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.kb import schema as kb  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)
from tests.test_verify_render import _add_place  # noqa: E402


def test_the_check_repair_runs_after_a_json_repair(world, monkeypatch):  # noqa: F811
    incoherent = {"kind": "picks", "picks": [], "recommendation": world["casa"]}
    casa = dict(world["good"]["picks"][0], diner_lines=world["bad_pick"]["diner_lines"])   # lines the diet checks refuse
    # incoherent twice: the execute step asks once more itself, then the controller's JSON repair runs
    w = Wire(frame=FRAME, rounds=world["rounds"],
             answers=[incoherent, incoherent, {"kind": "picks", "picks": [casa]}, world["good"]])
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False,
                   time_budget_s=0.0)
    assert w.calls.count("answer") == 4 and res.control.repairs == 2
    assert res.control.verify.ok and res.control.dropped == [] and "*Casa Pasta*" in res.text


def test_what_a_repair_records_is_what_the_checks_read(world, monkeypatch):  # noqa: F811
    con = kb.connect(world["db"])
    nova = _add_place(con, "Cafe Nova", cuisine="italian", status="temporarily closed")   # an aggregator's stale word
    con.close()
    pick = dict(world["good"]["picks"][0], place_id=nova, name="Cafe Nova", evidence=[])
    w = Wire(frame=FRAME, rounds=world["rounds"], answers=[{"kind": "picks", "picks": [pick]}])
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    real = cexecute.execute

    def execute(*a, **kw):
        if kw.get("prior") is not None:          # the repair reads the place's own page: it is open
            c = kb.connect(world["db"])
            kb.add_fact(c, subject_type="place", subject_id=nova, claim_type="status", value="open",
                        source_url="https://cafenova.test/", source_name="own site", source_rank="own_site",
                        method="fetch", observed_at=(NOW + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), now=NOW)
            c.commit()
            c.close()
        return real(*a, **kw)

    monkeypatch.setattr(cexecute, "execute", execute)
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.repairs >= 1 and res.control.dropped == []
    assert "*Cafe Nova*" in res.text
