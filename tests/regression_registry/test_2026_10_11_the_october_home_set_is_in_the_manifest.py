"""Regression (2026-10-11): the October home set is in Bourdain's manifest.

QA-2026-10-04-12: golden_home_2026-10.jsonl (the asks of Oct 3–4 at
home) ran only through `--set` or tests/test_golden_private.py's
"after the manifest" rule, so `scripts/eval_outcomes.py` with no `--set`
left it out. And core/eval_outcomes said "one chat per set", while a
run is one chat across its sets (the home asks follow the trip, as they
did for the household).

Fix: the manifest lists the three sets in recording order; the docstring
says what the runner does.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from core import eval_outcomes as ev
from core import manifest as cmanifest


def test_the_three_sets_in_recording_order():
    names = [Path(p).name for p in cmanifest.load("bourdain").golden_sets]
    assert names == ["golden_trip_2026-09.jsonl", "golden_home.jsonl", "golden_home_2026-10.jsonl"]


def test_a_run_is_one_chat():
    assert inspect.signature(ev.run_set).parameters["chat_id"].default == "golden"
    doc = " ".join((ev.__doc__ or "").split())
    assert "Cases run in order with one chat per set" not in doc and "Cases run in order in one chat" in doc
