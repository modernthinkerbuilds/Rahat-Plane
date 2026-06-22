"""Pin: 2026-06-14 — decisions ledger sub-second ordering tiebreaker
([[rahat_decisions_tail_ordering]]).

Both `core.decisions.tail()` and `by_trace()` order by a timestamp string.
Under a sub-second burst (multiple rows with an identical `ts`), a sort by
`ts` alone is non-total and SQLite may return rows in any order. The fix
adds `decision_id` as the secondary key:
  * tail()     → ORDER BY ts DESC, decision_id DESC
  * by_trace() → ORDER BY ts ASC,  decision_id ASC

This pin writes 3 rows with the SAME ts and asserts a deterministic,
monotonic decision_id ordering — newest-first for tail, oldest-first for
by_trace. If a refactor drops the secondary key, this fires.
"""
from __future__ import annotations

import pytest

from core import decisions as dec


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch):
    # RAHAT_TEST_MODE redirects core.io DB to a per-process sandbox.
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")


def _log_burst(n: int, trace_id: str, ts: str) -> list[int]:
    """Insert n decision rows that all share the SAME ts, directly into the
    sandbox DB that tail()/by_trace() read (cio.db() under RAHAT_TEST_MODE).
    log() doesn't accept ts (schema default CURRENT_TIMESTAMP, 1s
    precision), so we set it explicitly to force a deterministic collision."""
    from core import io as cio
    con = cio.db()
    ids: list[int] = []
    try:
        dec._ensure_schema(con)
        for i in range(n):
            cur = con.execute(
                "INSERT INTO decisions (ts, trace_id, actor, op, outcome) "
                "VALUES (?, ?, 'miya.v2', 'turn', 'ok')",
                (ts, trace_id),
            )
            ids.append(int(cur.lastrowid))
        con.commit()
    finally:
        con.close()
    return ids


def test_tail_breaks_subsecond_ties_by_decision_id_desc():
    ts = "2026-06-14 12:00:00"
    ids = _log_burst(3, "trace-tail", ts)
    rows = dec.tail(50, actor="miya.v2")
    # Filter to our trace, preserve tail()'s order.
    ours = [r["decision_id"] for r in rows if r["trace_id"] == "trace-tail"]
    assert ours == sorted(ids, reverse=True), (
        f"tail() returned undefined sub-second order: {ours}"
    )


def test_by_trace_breaks_subsecond_ties_by_decision_id_asc():
    ts = "2026-06-14 13:00:00"
    ids = _log_burst(3, "trace-bytrace", ts)
    rows = dec.by_trace("trace-bytrace")
    got = [r["decision_id"] for r in rows]
    assert got == sorted(ids), (
        f"by_trace() returned undefined sub-second order: {got}"
    )
