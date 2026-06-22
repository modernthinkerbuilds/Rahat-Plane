"""Pin: 2026-06-14 — budget snapshot works, but is NOT enforced on the live
synth path (PRE_SCALE E-P2).

`core.budget.check_budget()` correctly computes a rolling-24h spend snapshot
with an `exceeded` flag. But `orchestrator.handle()` never calls it before
the synthesizer LLM call, so a runaway loop (e.g. a future 2nd agent) blows
the daily cap silently.

This file:
  * pins `check_budget` in isolation (GREEN) — the primitive is sound, so
    the wiring has something correct to gate on; and
  * pins the GAP as the SAFETY TARGET (xfail): handle() should consult the
    budget before the synth call.
"""
from __future__ import annotations

import inspect

import pytest

from core import budget as cbudget


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")


@pytest.fixture
def db(tmp_path):
    """Per-test decisions DB so spends don't bleed across tests (the
    per-process RAHAT_TEST_MODE sandbox is shared otherwise)."""
    return str(tmp_path / "decisions.db")


# ─── the primitive is sound (green) ───────────────────────────────────
def test_check_budget_reports_under_cap(monkeypatch, db):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "10.0")
    snap = cbudget.check_budget(db_path=db)
    assert snap["limit_usd"] == 10.0
    assert snap["exceeded"] is False
    assert snap["remaining_usd"] >= 0.0


def test_check_budget_flags_exceeded_after_spend(monkeypatch, db):
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0.01")
    # Record a spend above the cap into an isolated decisions table.
    cbudget.record_spend(actor="miya.v2", tokens=1000, cost_usd=5.0, db_path=db)
    snap = cbudget.check_budget(db_path=db)
    assert snap["spent_usd"] >= 5.0
    assert snap["exceeded"] is True
    assert snap["remaining_usd"] == 0.0


def test_check_budget_cap_zero_disables_enforcement(monkeypatch, db):
    """Cap 0 is the explicit opt-out (ADR-005 rollback story): never
    'exceeded' regardless of spend."""
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD", "0")
    cbudget.record_spend(actor="miya.v2", tokens=1000, cost_usd=99.0, db_path=db)
    assert cbudget.check_budget(db_path=db)["exceeded"] is False


# ─── the gap: not gated on the live synth path (safety target) ────────
@pytest.mark.xfail(
    strict=False,
    reason="SAFETY TARGET (PRE_SCALE E-P2): orchestrator.handle() never calls "
           "core.budget.check_budget before the synthesizer LLM call, so a "
           "runaway loop blows the daily cap silently. Gate check_budget in "
           "handle() (and the nudge tick) and flip this to a hard pin.",
)
def test_handle_consults_budget_before_synth():
    """handle()/synthesizer should reference the budget gate. Today neither
    does — the budget is recorded but never enforced on the live path."""
    from new_plane.miya_runner import orchestrator, synthesizer
    src = inspect.getsource(orchestrator) + inspect.getsource(synthesizer)
    assert "check_budget" in src or "core.budget" in src or "import budget" in src, (
        "no budget gate on the live synth path — runaway spend is unbounded"
    )
