"""Regression pin (2026-10-04): a citation slip costs the citation, never
the place; and the first repair always runs.

LIVE (2026-10-04, the morning's asks replayed): good cafés were dropped
for bookkeeping slips the household never sees — "evidence record 1946
belongs to another place", "acclaim stated without an acclaim record" —
and with research alone outlasting the 30-second budget, no repair ever
ran (repairs 0). Seven of eight asks came back with nothing.

THE PINS.
  * sanitize_citations drops a citation to a missing record or another
    place's record, unverifies a diner line with no menu record of its
    own, and removes an acclaim with no record; the renderer still
    prints only each place's own records.
  * End to end, a pick whose only slip is a wrong citation goes out.
  * With the latency budget already spent, the first repair still runs.
"""
from __future__ import annotations

import os

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat  # noqa: E402
from core.harness import answer as ans  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.kb import query, schema as kb  # noqa: E402
from tests.test_harness_loop import FRAME, NOW, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)


def _facts(world):  # noqa: F811
    con = kb.connect(world["db"])
    try:
        return query.fact(con, world["casa"], "hours")["fact_id"], query.fact(con, world["bad"], "hours")["fact_id"]
    finally:
        con.close()


def test_sanitize_fixes_the_slips_and_keeps_the_place(world):  # noqa: F811
    own, other = _facts(world)
    pick = ans.Pick(place_id=world["casa"], name="Casa Pasta", acclaim="Local Mag",       # Casa has an acclaim record
                    evidence=[{"claim": "hours", "fact_id": own}, {"claim": "hours", "fact_id": other},
                              {"claim": "status", "fact_id": 987654}],
                    diner_lines=[{"member": "primary", "dish": "GF penne", "verified": True},
                                 {"member": "spouse", "dish": "penne", "verified": True, "fact_id": other}])
    pub = ans.Pick(place_id=world["bad"], name="Pub Grub", acclaim="a critic's list, 2025")  # no acclaim record
    ev = world["tenant"].evidence({"db_path": world["db"], "now": NOW, "mode": "trip"})
    fixed, notes = ans.sanitize_citations(ans.Answer(kind="picks", picks=[pick, pub]), ev)
    p, q = fixed.picks
    assert [r.fact_id for r in p.evidence] == [own]
    assert not any(d.verified for d in p.diner_lines) and p.acclaim == "Local Mag" and q.acclaim is None
    assert len(notes) == 5 and fixed.pick_ids() == [world["casa"], world["bad"]]
    clean = ans.Answer(kind="picks", picks=[ans.Pick(place_id=world["casa"], name="Casa Pasta",
                                                     evidence=[{"claim": "hours", "fact_id": own}])])
    assert ans.sanitize_citations(clean, ev) == (clean, [])


def test_a_wrong_citation_no_longer_drops_the_pick(world, monkeypatch):  # noqa: F811
    own, other = _facts(world)
    slipped = {"kind": "picks", "picks": [dict(world["good"]["picks"][0], evidence=[{"claim": "hours", "fact_id": other}])]}
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=FRAME, rounds=world["rounds"], answers=[slipped]))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False)
    assert res.control.dropped == [] and "*Casa Pasta*" in res.text
    assert any(s.get("step") == "sanitize" for s in res.steps)


def test_the_first_repair_runs_even_past_the_latency_budget(world, monkeypatch):  # noqa: F811
    first = {"kind": "picks", "picks": [world["good"]["picks"][0], world["bad_pick"]], "recommendation": world["casa"]}
    w = Wire(frame=FRAME, rounds=world["rounds"], answers=[first, world["good"]])
    monkeypatch.setattr(llm_chat, "_call_wire", w)
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"], judge_enabled=False,
                   time_budget_s=0.0)
    assert res.control.repairs == 1 and res.control.verify.ok and w.calls.count("answer") == 2
    assert "*Casa Pasta*" in res.text and "Pub Grub" not in res.text
