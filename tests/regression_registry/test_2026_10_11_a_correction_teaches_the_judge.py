"""Regression (2026-10-11): a correction teaches the judge.

LIVE shape (QA-2026-10-04-13): the judge earns authority from owner
labels, and stays in shadow until 50 of them line up with its verdicts
(ADR-018 §2.8, which names corrections as a label source). On Oct 4 the
household's nine Bourdain asks were a first ask and then corrections
("Other options please?", "Check across the whole area", "I can travel
up to 60 minutes, expand search"), with no thanks and no verdict words.
record_reaction stored each correction and wrote no label, so the judge's
agreement with the household was never measured.

Fix (core/harness/record.py): a correction labels the previous trace's
U1 (the answer did not settle the ask) and U7 (a follow-up did not carry
the ask forward) as fail, with source "correction" so these labels can be
weighed apart. Shadow reactions still label nothing; verdicts and
acceptances label as before.
"""
from __future__ import annotations

import os
from datetime import datetime

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
        return sorted(con.execute("SELECT outcome, label, source FROM judge_labels WHERE trace_id = ?",
                                  (trace,)).fetchall())
    finally:
        con.close()


def _react(db: str, text: str, frame=CORRECTION, label: bool = True) -> dict:
    return crecord.record_reaction(trace_id="t2", previous_trace="t1", agent="bourdain", chat_id="c",
                                   frame=frame, text=text, judged_outcomes=["U1", "D3"], now=NOW,
                                   db_path=db, label=label)


def test_a_correction_labels_u1_and_u7_fail(tmp_path):
    db = str(tmp_path / "j.db")
    assert _react(db, "Other options please?") == {"kind": "correction", "outcomes": ["U1", "U7"]}
    assert _labels(db, "t1") == [("U1", "fail", "correction"), ("U7", "fail", "correction")]


def test_the_labels_meet_the_judges_verdicts(tmp_path):
    """The point: a correction now counts toward the labels the judge needs."""
    db = str(tmp_path / "j.db")
    con = cjudge._connect(db)
    con.execute("INSERT INTO judge_verdicts (trace_id, agent, outcome, verdict, at) VALUES "
                "('t1', 'bourdain', 'U1', 'pass', '2026-10-04 11:10:00')")
    con.commit()
    con.close()
    assert cjudge.agreement("bourdain", db_path=db)["overall"]["n"] == 0
    _react(db, "Check across the whole area")
    overall = cjudge.agreement("bourdain", db_path=db)["overall"]
    assert overall["n"] == 1 and overall["accuracy"] == 0.0      # the judge passed what the household corrected


def test_a_shadow_correction_still_labels_nothing(tmp_path):
    db = str(tmp_path / "j.db")
    assert _react(db, "Other options please?", label=False)["kind"] == "correction"
    assert _labels(db, "t1") == []


def test_acceptances_and_verdicts_label_as_before(tmp_path):
    db = str(tmp_path / "j.db")
    _react(db, "thanks", frame=None)
    assert _labels(db, "t1") == [("D3", "pass", "reaction"), ("U1", "pass", "reaction")]
