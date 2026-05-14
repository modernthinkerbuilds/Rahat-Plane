"""core.budget — token-spend ledger + global budget cap.

Two-function module, per the Day-3 directive (2026-05-14):

    record_spend(actor, tokens, cost_usd, *, trace_id=None) -> None
    check_budget(*, actor=None) -> dict

The doctrine:

    • **Global enforcement, per-agent observability.** A single env-var
      cap (`RAHAT_TOKEN_BUDGET_DAILY_USD`, default $5/day) applies to
      the whole mesh. Per-agent observability comes from filtering the
      `decisions` ledger by `actor` after the fact — no per-agent
      enforcement is wired today.

    • **`actor` is in every signature from day one.** It's the seam
      for promoting to per-agent caps later without touching call
      sites. YAGNI on the enforcement side; AYAGNI ("agency you
      already know") on the API side.

    • **Storage is the existing `decisions` ledger.** Every spend is
      one row with `op='budget.spend'`. SQL aggregation over the
      24-hour rolling window gives the running total.

ADR-005 (`specs/ADR-005-budget-enforcement.md`) is the canonical
write-up — read that first if you're touching this file.

Promotion trigger: if one agent's runaway loop blows the global cap
and a more discriminating limit would have helped, promote to
per-agent enforcement. Until then, the global cap + the actor-keyed
ledger is enough.

Rollback story: set `RAHAT_TOKEN_BUDGET_DAILY_USD=0` to disable
enforcement entirely. `check_budget` returns `{exceeded: False,
remaining_usd: 0.0}` and callers that read it MUST treat the value
as advisory until they re-check.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from core import decisions as _decisions
from core import io as cio


# ─────────────────────────── Configuration ──────────────────────────
DEFAULT_DAILY_USD: float = 5.0
ENV_VAR_DAILY_USD: str = "RAHAT_TOKEN_BUDGET_DAILY_USD"
OP_NAME: str = "budget.spend"


def _daily_cap_usd() -> float:
    """Resolve the global daily cap from the environment.

    Empty / unset / unparseable → DEFAULT_DAILY_USD. The intent is
    that a deployment misconfiguration falls back to a safe number,
    not zero (which would disable enforcement and surprise an oncall).
    Explicit zero means 'disabled'.
    """
    raw = os.environ.get(ENV_VAR_DAILY_USD)
    if raw is None or not raw.strip():
        return DEFAULT_DAILY_USD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_DAILY_USD


# ─────────────────────────── Public API ─────────────────────────────
def record_spend(actor: str, tokens: int, cost_usd: float,
                 *,
                 trace_id: str | None = None,
                 db_path: str | None = None) -> None:
    """Log one token-spend event to the decisions ledger.

    Args:
        actor:      The agent name responsible for the spend
                    ('fraser', 'kobe', 'miya', …). Matches the
                    `actor` column on the `decisions` table — every
                    SQL query downstream keys off it.
        tokens:     Total tokens for this call (prompt + completion).
                    Written to `tokens_in` for now; if call sites
                    need a prompt/completion split, we extend the
                    signature, not this stub.
        cost_usd:   Dollar cost of this call. Authoritative number;
                    the budget aggregation sums this column.
        trace_id:   Optional trace identifier. If None, a fresh one
                    is minted via `decisions.new_trace()`. Pass the
                    upstream trace_id when this spend is a child of
                    a larger decision tree (e.g., a reasoner call
                    inside a user message handler).

    No return value — the decisions ledger swallows write failures
    (observability must not crash the runtime, per
    `decisions.log` contract).
    """
    tid = trace_id if trace_id is not None else _decisions.new_trace()
    _decisions.log(
        actor=actor, op=OP_NAME,
        trace_id=tid,
        tokens_in=int(tokens), tokens_out=0,
        cost_usd=float(cost_usd),
        outcome="ok",
        db_path=db_path,
    )


def check_budget(*, actor: str | None = None,
                 db_path: str | None = None) -> dict[str, Any]:
    """Return the current budget snapshot for the rolling 24-hour window.

    Args:
        actor:  If provided, scope `spent_usd` to this actor's spend.
                The `limit_usd` is STILL the global cap regardless —
                that's the doctrine (global enforcement, per-agent
                observability). When per-agent enforcement lands,
                this returns a different limit per actor; today it
                doesn't.

    Returns:
        {
            "limit_usd":      <float>   # global daily cap from env
            "spent_usd":      <float>   # sum(cost_usd) over last 24h
            "remaining_usd":  <float>   # max(0, limit - spent)
            "exceeded":       <bool>    # spent >= limit
            "actor":          <str|None># echoes the input scope
        }

    Failure mode: if the `decisions` table doesn't exist yet (fresh
    DB, no spends ever recorded), `spent_usd` is 0.0 — not an error.
    The caller can still gate on `exceeded`.
    """
    limit = _daily_cap_usd()
    con = cio.db(db_path) if db_path else cio.db()
    try:
        if actor:
            cur = con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions "
                "WHERE ts >= datetime('now', '-1 day') AND actor = ?",
                (actor,))
        else:
            cur = con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions "
                "WHERE ts >= datetime('now', '-1 day')")
        spent = float(cur.fetchone()[0])
    except sqlite3.OperationalError:
        # Decisions table not yet auto-migrated (fresh DB). Zero spend
        # is the safe answer — the caller's first record_spend() call
        # creates the table.
        spent = 0.0
    finally:
        con.close()
    # `exceeded` is False when the cap is 0 (disabled) — explicit
    # opt-out per the rollback story in ADR-005.
    exceeded = (spent >= limit) if limit > 0 else False
    return {
        "limit_usd": limit,
        "spent_usd": round(spent, 6),
        "remaining_usd": round(max(0.0, limit - spent), 6),
        "exceeded": exceeded,
        "actor": actor,
    }


__all__ = [
    "DEFAULT_DAILY_USD", "ENV_VAR_DAILY_USD", "OP_NAME",
    "record_spend", "check_budget",
]
