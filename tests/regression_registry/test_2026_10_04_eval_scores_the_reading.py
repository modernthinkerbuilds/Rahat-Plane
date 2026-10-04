"""Regression pin (2026-10-04) — the eval scored answers against the model's own reading.

SYMPTOM. On the morning of 2026-10-04 Bourdain took the primary member for
the spouse (the speaker came from another agent's join order) and applied
the spouse's rules to every reply; "coffee near me" can be read as "just
me". Either reading makes every diet, room and seafood check (P1–P10)
pass for the wrong people, and the outcome eval would have scored those
answers green: U3 only checks that the frame is internally consistent,
and `frame_matches_oracle` (meal and diners, the baby included) was
reported, never scored.

FIX. core/eval_outcomes.py scores the reading in replay and live runs:
who is speaking and who is eating (the baby and toddler aside, guests as
a flag) must match the case, or the case fails with the outcome id FRAME;
meal, where, radius, exclusions, follow-up and the children are reported
per field ("reading: speaker 14/14 · who is eating 12/14 · …").

PIN. The two live misreadings fail the case; the right reading passes; a
stub run (which uses the case's own reading) reports nothing.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import eval_outcomes as ev  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.harness.answer import Answer, NoneClaim  # noqa: E402
from core.harness.contract import load_library  # noqa: E402
from core.harness.frame import Frame  # noqa: E402
from tests.test_eval_outcomes import world  # noqa: E402,F401  (the synthetic twins' fixture)

LIB = load_library("agents/bourdain/outcomes.json")
COFFEE = {"id": "C1", "when": "2026-10-04T10:48-07:00", "leg": "alpha_city", "speaker": "him",
          "message": "coffee and pastries near me",
          "context": {"where": "near the stay", "party": "him, her, baby"},
          "contract": [{"id": "U2", "params": {"meal": "coffee"}}, {"id": "U3", "params": {"diners": ["him", "her"]}}],
          "journeys": ["UC1"], "traps": ["'near me' read as 'just me'"], "reference": None, "owner_verdict": None, "fixes": []}


def _frame(**kw) -> Frame:
    base = {"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse", "infant"], "meal": "coffee"}
    base.update(kw)
    return Frame.model_validate(base)


def test_the_right_reading_is_faithful():
    oracle = ev.frame_from_golden(COFFEE, library=LIB)
    ff = ev.frame_faithfulness(_frame(), oracle)
    assert ff["speaker"]["ok"] and ff["eaters"]["ok"] and ff["meal"]["ok"]


@pytest.mark.parametrize("misread,field", [
    ({"diners": ["primary"]}, "eaters"),                                     # "near me" → just me
    ({"speaker": "spouse", "diners": ["spouse", "primary", "infant"]}, "speaker"),   # the primary taken for the spouse
])
def test_the_live_misreadings_fail_the_case(misread, field):
    oracle = ev.frame_from_golden(COFFEE, library=LIB)
    cr = ev.CaseResult(id="C1", passed=True)
    ev.score_frame(cr, _frame(**misread), oracle)
    assert not cr.passed and "FRAME" in cr.failed and cr.frame_fields[field] is False
    assert any(n.startswith(field) for n in cr.frame_notes)


def test_the_baby_alone_does_not_fail_the_case_but_is_reported():
    oracle = ev.frame_from_golden(COFFEE, library=LIB)
    cr = ev.CaseResult(id="C1", passed=True)
    ev.score_frame(cr, _frame(diners=["primary", "spouse"]), oracle)
    assert cr.passed and "FRAME" not in cr.failed and cr.frame_fields["children"] in (True, False)


def test_a_replayed_case_with_a_solo_reading_fails_end_to_end(world, monkeypatch):
    """Through run_case: the model's frame is the one scored, not the case's."""
    solo = _frame(diners=["primary"])
    answer = Answer(kind="none", none_claim=NoneClaim(area="the area", hours="10:48", searched=["the knowledge base"]))

    def fake_run(tenant, text, **kw):
        ctx = tenant.build_ctx(kw.get("chat_id", "golden"), kw["now"], text, db_path=kw.get("db_path"),
                               cassette_dir=None, trace_id=kw.get("trace_id"))
        return hrun.RunResult(text="Nothing fits.", trace_id=kw.get("trace_id") or "t", frame=solo, contract=None,
                              answer=answer, cost_usd=0.0, search_queries=0, tool_calls=0, ctx=ctx)

    monkeypatch.setattr(hrun, "run", fake_run)
    case = dict(COFFEE, contract=[{"id": "U3", "params": {"diners": ["him", "her"]}}])
    rep = ev.run_set(world["tenant"], [case], mode="replay", db_path=world["db"], cassette_dir=None)
    c = rep.cases[0]
    assert not c.passed and "FRAME" in c.failed, (c.failed, c.messages)
    assert "who is eating 0/1" in rep.render()


def test_stub_runs_report_no_reading(world):
    rep = ev.run_set(world["tenant"], [COFFEE], mode="stub", db_path=world["db"], cassette_dir=None)
    assert rep.cases[0].frame_fields == {} and rep.frame_summary() == ""
