"""core.budget — token-spend ledger + global cap.

What this file pins
-------------------
1. `record_spend` writes one row to the `decisions` ledger with
   `op='budget.spend'`, the supplied actor, tokens, and cost_usd.
2. `check_budget` aggregates `cost_usd` over the rolling 24-hour
   window. With no spend it returns spent=0 cleanly (no crash on
   a fresh DB).
3. The env var `RAHAT_TOKEN_BUDGET_DAILY_USD` controls the cap;
   default is $5/day; explicit `0` disables enforcement.
4. `actor` scoping returns per-agent spend without changing the
   global cap — that's the "global enforcement, per-agent
   observability" doctrine from ADR-005.
5. `trace_id` carries forward when supplied; minted when not.

Every test is offline. No LLM, no Telegram.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Per-test sandbox DB — same fixture pattern as test_fraser_state."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture
def clean_budget_env(monkeypatch):
    """Make sure RAHAT_TOKEN_BUDGET_DAILY_USD is in a known state.
    Tests that need a non-default cap set the env var explicitly."""
    monkeypatch.delenv("RAHAT_TOKEN_BUDGET_DAILY_USD", raising=False)


# ─── 1. record_spend writes to the decisions ledger ─────────────────
def test_record_spend_writes_row(fresh_db, clean_budget_env):
    from core import budget
    import sqlite3

    budget.record_spend("fraser", tokens=500, cost_usd=0.002,
                        trace_id="trace-1")
    con = sqlite3.connect(str(fresh_db))
    try:
        row = con.execute(
            "SELECT actor, op, tokens_in, cost_usd, trace_id "
            "FROM decisions WHERE op=? ORDER BY decision_id DESC LIMIT 1",
            (budget.OP_NAME,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    actor, op, tokens_in, cost_usd, trace_id = row
    assert actor == "fraser"
    assert op == "budget.spend"
    assert tokens_in == 500
    assert abs(cost_usd - 0.002) < 1e-9
    assert trace_id == "trace-1"


def test_record_spend_mints_trace_when_omitted(fresh_db, clean_budget_env):
    from core import budget
    import sqlite3

    budget.record_spend("fraser", tokens=100, cost_usd=0.001)
    con = sqlite3.connect(str(fresh_db))
    try:
        row = con.execute(
            "SELECT trace_id FROM decisions WHERE op=? "
            "ORDER BY decision_id DESC LIMIT 1",
            (budget.OP_NAME,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    trace_id = row[0]
    # uuid4 hex is 32 chars.
    assert trace_id and len(trace_id) >= 16


# ─── 2. check_budget aggregates over the rolling window ─────────────
def test_check_budget_empty_db_returns_zero_spent(fresh_db, clean_budget_env):
    from core import budget
    snap = budget.check_budget()
    assert snap["spent_usd"] == 0.0
    assert snap["limit_usd"] == budget.DEFAULT_DAILY_USD
    assert snap["remaining_usd"] == budget.DEFAULT_DAILY_USD
    assert snap["exceeded"] is False


def test_check_budget_sums_recent_spends(fresh_db, clean_budget_env):
    from core import budget
    budget.record_spend("fraser", tokens=100, cost_usd=0.50)
    budget.record_spend("kobe",   tokens=200, cost_usd=1.25)
    budget.record_spend("fraser", tokens=150, cost_usd=0.75)
    snap = budget.check_budget()
    assert abs(snap["spent_usd"] - 2.50) < 1e-9


def test_check_budget_actor_scope_filters_spend(fresh_db, clean_budget_env):
    """Per-agent observability — pass actor='fraser' to see only
    Fraser's slice. Global cap is unchanged per the doctrine."""
    from core import budget
    budget.record_spend("fraser", tokens=100, cost_usd=0.50)
    budget.record_spend("kobe",   tokens=200, cost_usd=1.25)
    budget.record_spend("fraser", tokens=150, cost_usd=0.75)
    fraser = budget.check_budget(actor="fraser")
    kobe   = budget.check_budget(actor="kobe")
    assert abs(fraser["spent_usd"] - 1.25) < 1e-9
    assert abs(kobe["spent_usd"]   - 1.25) < 1e-9
    # Limit is the same (global enforcement).
    assert fraser["limit_usd"] == kobe["limit_usd"] == budget.DEFAULT_DAILY_USD


# ─── 3. Env-var-driven cap ──────────────────────────────────────────
def test_check_budget_env_var_overrides_default(fresh_db, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "10.0")
    from core import budget
    snap = budget.check_budget()
    assert snap["limit_usd"] == 10.0


def test_check_budget_unparseable_env_falls_to_default(fresh_db, monkeypatch):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "definitely not a number")
    from core import budget
    snap = budget.check_budget()
    assert snap["limit_usd"] == budget.DEFAULT_DAILY_USD


def test_check_budget_zero_disables_enforcement(fresh_db, monkeypatch):
    """ADR-005 rollback story: env var = 0 means no enforcement.
    `exceeded` is always False, even with positive spend."""
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0")
    from core import budget
    budget.record_spend("fraser", tokens=1_000_000, cost_usd=100.0)
    snap = budget.check_budget()
    assert snap["limit_usd"] == 0.0
    assert snap["exceeded"] is False


# ─── 4. Exceeded flag fires at the boundary ─────────────────────────
def test_check_budget_exceeded_at_limit(fresh_db, clean_budget_env):
    from core import budget
    # Default cap is $5.00 — push spend to exactly that.
    budget.record_spend("fraser", tokens=5_000, cost_usd=5.00)
    snap = budget.check_budget()
    assert snap["spent_usd"] >= snap["limit_usd"]
    assert snap["exceeded"] is True
    assert snap["remaining_usd"] == 0.0


def test_check_budget_below_limit_not_exceeded(fresh_db, clean_budget_env):
    from core import budget
    budget.record_spend("fraser", tokens=1_000, cost_usd=1.50)
    snap = budget.check_budget()
    assert snap["exceeded"] is False
    assert abs(snap["remaining_usd"] - 3.50) < 1e-9


# ─── 5. Shape contract ──────────────────────────────────────────────
def test_check_budget_returns_documented_shape(fresh_db, clean_budget_env):
    """ADR-005 documents the return shape. Future call sites depend on
    exactly these keys; rename = breaking change."""
    from core import budget
    snap = budget.check_budget(actor="fraser")
    assert set(snap.keys()) == {
        "limit_usd", "spent_usd", "remaining_usd", "exceeded", "actor"}
    assert snap["actor"] == "fraser"
