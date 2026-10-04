"""A golden set only grows at its end (quality pass 2026-10-04).

Each case runs in the state the cases before it left (one chat per run),
and a recording is keyed on the whole request, message history included.
A case inserted in the middle of a set — or a reordering — changes every
later case's request, so the recordings of all of them go stale and the
~$5–10 recording run has to be paid again. Cases go at the END of a set
file or into a new one (tests/evals/incidents.py `promote` only appends).

private/prds/<agent>/evals/.order.json records each set's case ids when a
recording is made: {"golden_trip_2026-09.jsonl": ["T01", …], …}. This test
holds every set to it: the recorded ids still come first, in order; new
ids only after them. Skipped where the private sets are absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORDERS = sorted((ROOT / "private" / "prds").glob("*/evals/.order.json"))


@pytest.mark.skipif(not ORDERS, reason="no recorded case order (private/prds/<agent>/evals/.order.json)")
@pytest.mark.parametrize("order_file", ORDERS or [None], ids=lambda p: p.parent.parent.name if p else "none")
def test_recorded_cases_keep_their_place(order_file):
    recorded = json.loads(order_file.read_text(encoding="utf-8"))
    for name, ids in recorded.items():
        path = order_file.parent / name
        assert path.exists(), f"{name} was recorded and is gone"
        now = [json.loads(x)["id"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert now[:len(ids)] == ids, (
            f"{name}: the recorded cases moved or changed — new cases go at the END (recorded {ids[:5]}…, now {now[:5]}…)")
