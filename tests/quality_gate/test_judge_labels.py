"""Quality gate (2026-10-04): the owner's corrections teach the judge.

ADR-018 §2.8: the judge earns authority from owner labels — "Labels come
from owner reactions (corrections, verdicts, acceptances)" — and stays in
shadow until 50 of them. core/harness/record.record_reaction turns a
verdict into fail labels and an acceptance ("thanks", "great"…) into pass
labels, but a correction records the reaction and no label at all.

On 2026-10-04 the household's nine Bourdain asks were a first ask and
then corrections ("Other options please?", "Check across the whole area",
"I can travel up to 60 minutes, expand search", "Can you search in the …
city") — no thanks, no verdict words. At that rate the judge's agreement
with the owner is never measured and it never leaves shadow.

QA-2026-10-04-13 (platform lane): a correction labels the previous
trace's judged outcomes it bears on — at least U1 (every part answered)
and U7 (continuity) as fail — so corrections count toward the 50.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import judge as cjudge  # noqa: E402
from core.harness import record as crecord  # noqa: E402
from core.harness.frame import Frame  # noqa: E402

NOW = datetime(2026, 10, 4, 11, 14)
CORRECTION = Frame.model_validate({"speaker": "primary", "intents": ["eat"], "diners": ["primary", "spouse"],
                                   "meal": "coffee", "refers_to_last_answer": True})


def _labels(db: str, trace: str) -> list[tuple]:
    con = cjudge._connect(db)
    try:
        return con.execute("SELECT outcome, label, source FROM judge_labels WHERE trace_id = ?", (trace,)).fetchall()
    finally:
        con.close()


def test_a_correction_is_classified_as_one():
    assert crecord.classify_reaction(CORRECTION, "Other options please?")[0] == "correction"


def test_verdicts_and_acceptances_label_today(tmp_path):
    db = str(tmp_path / "labels.db")
    crecord.record_reaction(trace_id="t2", previous_trace="t1", agent="bourdain", chat_id="c", frame=None,
                            text="thanks", judged_outcomes=["U1", "D3"], now=NOW, db_path=db)
    assert sorted(_labels(db, "t1")) == [("D3", "pass", "reaction"), ("U1", "pass", "reaction")]


@pytest.mark.xfail(strict=True, reason=(
    "QA-2026-10-04-13: record_reaction records a correction ('Other options please?') and writes no judge "
    "label, though ADR-018 §2.8 names corrections as a label source; at the household's rate of thanks the "
    "judge never reaches its 50 labels. Fix: a correction labels U1 and U7 (and the judged outcomes it bears "
    "on) fail for the previous trace. Delete this mark when it passes."))
def test_a_correction_becomes_labels(tmp_path):
    db = str(tmp_path / "labels.db")
    crecord.record_reaction(trace_id="t2", previous_trace="t1", agent="bourdain", chat_id="c", frame=CORRECTION,
                            text="Other options please?", judged_outcomes=["U1", "D3"], now=NOW, db_path=db)
    got = _labels(db, "t1")
    assert got and all(label == "fail" for _, label, _ in got), got
