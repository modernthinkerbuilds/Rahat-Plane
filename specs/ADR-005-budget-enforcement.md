# ADR-005 — Token-budget enforcement

**Status:** Accepted
**Date:** 2026-05-14
**Owner:** Modern Builder
**Related:** ADR-001 (control plane), ADR-003 (storage convention), ADR-004 (five-file pattern), `core/decisions.py`, `core/budget.py`

---

## Decision

Token-spend enforcement runs against a **single global daily cap** for the entire agent mesh — `RAHAT_TOKEN_BUDGET_DAILY_USD`, default $5/day — not against per-agent caps. The `core/budget.py` module exposes two functions: `record_spend(actor, tokens, cost_usd, *, trace_id)` writes one row to the existing `decisions` ledger with `op='budget.spend'` and the supplied `actor` string, and `check_budget(*, actor=None)` aggregates `cost_usd` over the rolling 24-hour window. The `actor` parameter is present on both signatures from day one but is observability-only today — the limit returned by `check_budget` is the global cap regardless of which `actor` was queried. Per-agent observability comes from filtering the ledger by `actor`, which falls out for free because every Rahat agent already writes to `core/decisions.py` with its name as the actor (see ADR-001 §3). This keeps the call-site surface stable when per-agent enforcement lands later, without paying the cost of designing that surface today.

## Per-actor override (Day-4, 2026-05-14)

The Day-4 directive added a per-actor tightening knob without breaking the global-enforcement doctrine: `RAHAT_TOKEN_BUDGET_DAILY_USD_<ACTOR>` (uppercased actor name) sets a per-actor cap that overrides the global when `check_budget(actor=…)` is called. In `core/budget.py::_daily_cap_usd`, the lookup order is per-actor env → global env → `DEFAULT_DAILY_USD` ($5/day). Unset / empty / unparseable at any tier falls through to the next. The default position is unchanged — no per-actor env, `check_budget(actor='fraser')` returns the global cap. The intent is a single deployment-time tightening for actors with known-tight budgets (production Fraser at $0.50/day vs. dev Fraser sharing the global $5/day) without fragmenting the policy plane. This is NOT the per-agent enforcement promotion referred to in the Promotion-trigger section below — that promotion would involve per-agent CHARTER POLICIES, not just env-var tightening. The per-actor env var is a deployment knob; the Charter promotion is a policy decision.

## Promotion trigger and rollback story

The trigger to graduate from global to per-agent enforcement is concrete: the first production incident where one agent's runaway loop blows the global cap and a more discriminating limit would have meaningfully softened the impact — i.e., a real case where global-cap throttling kills a healthy agent's session because a sick agent ate the budget. Until that incident, the global cap plus the actor-keyed ledger is enough; the SQL `WHERE actor=?` filter that backs `check_budget(actor=…)` is exactly the query an incident-response writeup would run, so the data is already available. The rollback story is symmetric: `RAHAT_TOKEN_BUDGET_DAILY_USD=0` disables enforcement entirely — `check_budget` returns `{exceeded: False, limit_usd: 0.0}` and callers MUST treat their gate as advisory until they re-check. The default fallback when the env var is unset or unparseable is `$5/day` (`core/budget.DEFAULT_DAILY_USD`), chosen to surface a misconfigured deployment via a tighter-than-intended cap rather than via the silent disable that `0` produces.
