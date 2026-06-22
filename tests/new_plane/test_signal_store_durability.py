"""§1.2 — `new_plane/signals/store.py` durability + the RAHAT_TEST_MODE
sandbox gap (PRE_SCALE D-P1 / G).

Happy-path filtering is covered in test_cross_agent_signal_isolation.py.
This file pins the DURABILITY contract the cross-agent bus must hold before
a 2nd surface/agent opens the same SQLite file:
  * replay on boot (rows survive a fresh connection / process bounce),
  * late subscriber sees earlier signals,
  * dedup of consumers under duplicate mark_consumed,
  * deterministic newest-first ordering by id even on identical ts,
  * the missing RAHAT_TEST_MODE sandbox guard (SAFETY TARGET, xfail).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from new_plane.signals import store


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    yield db


# ─── replay on boot / restart ─────────────────────────────────────────
def test_signals_survive_process_bounce(tmp_path, monkeypatch):
    db = tmp_path / "durable.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    sid = store.publish(agent="kobe", type_="pace_update",
                        payload={"v": 1}, trace_id="t-boot")

    # Fresh process: drop cached path, reopen same file.
    store._DB_PATH = None
    store.set_db_path(db)
    store.init_db()

    rows = store.recent(limit=10)
    assert any(r["id"] == sid for r in rows), "signal lost across restart"


def test_late_subscriber_sees_earlier_signals():
    s1 = store.publish(agent="kobe", type_="x", payload={}, trace_id="t1")
    s2 = store.publish(agent="kobe", type_="x", payload={}, trace_id="t2")
    # A subscriber that connects "later" still reads both via recent().
    ids = {r["id"] for r in store.recent(agent="kobe", limit=50)}
    assert {s1, s2} <= ids


# ─── consumer dedup ───────────────────────────────────────────────────
def test_duplicate_mark_consumed_is_idempotent():
    sid = store.publish(agent="fraser", type_="design_done",
                        payload={}, trace_id="t")
    assert store.mark_consumed(sid, "miya") is True
    assert store.mark_consumed(sid, "miya") is False  # no duplicate
    row = next(r for r in store.recent(limit=10) if r["id"] == sid)
    assert row["consumed_by"].count("miya") == 1


def test_distinct_consumers_accumulate():
    sid = store.publish(agent="kobe", type_="x", payload={}, trace_id="t")
    store.mark_consumed(sid, "miya")
    store.mark_consumed(sid, "genie")
    row = next(r for r in store.recent(limit=10) if r["id"] == sid)
    assert set(row["consumed_by"]) == {"miya", "genie"}


# ─── deterministic ordering even on identical timestamps ──────────────
def test_recent_orders_by_id_under_subsecond_collision():
    """recent() orders by `id DESC`, so even when three publishes share an
    identical ts (sub-second burst), order is total and stable — newest id
    first. This is the signal-store analogue of the decisions tiebreaker."""
    fixed_ts = "2026-06-14T12:00:00.000000Z"
    ids = [store.publish(agent="kobe", type_="burst", payload={"n": n},
                         trace_id="t", ts=fixed_ts) for n in range(3)]
    got = [r["id"] for r in store.recent(type_="burst", limit=10)]
    assert got == sorted(ids, reverse=True), (
        f"sub-second burst returned undefined order: {got}"
    )


# ─── RAHAT_TEST_MODE sandbox guard (PRE_SCALE D-P1 — landed) ───────────
def test_store_sandboxes_under_test_mode(monkeypatch):
    """The signal store now mirrors core.io's corruption guard: with
    RAHAT_TEST_MODE=1 and no explicit OPENCLAW_SIGNALS_DB, `_default_path()`
    resolves to a pid-scoped temp sandbox, NEVER the live ~/.rahat DB. This
    closes the D-P1 gap (same class as the 2026-05-08 corruption). Pinned
    GREEN so a revert of the guard fires this test."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("OPENCLAW_SIGNALS_DB", raising=False)
    store._DB_PATH = None
    p = store._default_path()
    home_db = Path.home() / ".rahat" / "new_plane_signals.db"
    assert p != home_db, (
        "REGRESSION: test mode resolved the signal store to the LIVE user "
        "DB — the D-P1 sandbox guard was removed; a buggy test could corrupt "
        "the real signals DB"
    )
    assert "rahat_signals_test_" in p.name, (
        f"expected a pid-scoped sandbox, got {p}"
    )
