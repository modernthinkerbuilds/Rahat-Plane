# Rahat conventions

The non-negotiable rules every refactor must respect. Each entry has a
production incident attached so future-you can see why it's load-bearing.

---

## UTC at the SQL boundary

**Rule:** every `datetime` value persisted to SQLite must be UTC.
Python-side code that compares against SQL `CURRENT_TIMESTAMP` (or
similar SQL-time defaults) must also use UTC.

**Canonical pattern:**

```python
from datetime import datetime, timezone

# WRITE: UTC at the boundary
valid_until = datetime.now(timezone.utc) + timedelta(seconds=60)
# (also acceptable for legacy code that already uses naive datetimes
# at the SQL boundary: datetime.utcnow() — but prefer the tz-aware form
# for new code)

# READ + COMPARE: SQL's CURRENT_TIMESTAMP is UTC; never compare
# against `datetime.now()` (local time)
```

**Why:** the 2026-05-17 production incident. `_clarification_remember`
stored `valid_until` via `datetime.now()` (local time, Pacific = UTC-7
on the host). The `list_entities` filter used SQL `CURRENT_TIMESTAMP`
which is always UTC. On Pacific, the stored ISO string compared as
seven hours in the past, so every clarification entity was treated as
expired immediately — clarifications never resolved in production
even though the same tests passed in the (UTC) sandbox.

**Pinned by:** `tests/regression_registry/test_2026_05_17_clarification_tz_mismatch.py`
and the production-parity TZ matrix in `tests/production_parity/`.

---

## Slash commands bypass the classifier

**Rule:** `core.miya.route()` must check `msg.strip().startswith("/")`
first and dispatch directly to the agent that owns the slash table
(currently Kobe). Slash commands never go through `classify_intent`
or `_route_via_triggers`.

**Why:** the 2026-05-17 mesh-routing merge broke `/next` and `/plan`.
The classifier scored slashes by semantic similarity and routed them
to Fraser, who returned `[Fraser] mode=default …` stubs.

**Pinned by:** `tests/regression_registry/test_2026_05_17_slash_bypass_dispatched_to_fraser.py`.

---

## Agents never hallucinate factual state

**Rule:** any agent reasoner that receives a factual question about
the user's plan, dislikes, weight history, HRV, tier, or specific-day
workout MUST call the corresponding tool (`get_plan`,
`get_workout_on`, `get_dislikes`, `get_tier`, `get_weight_history`,
`get_pace`). Reasoners may NOT synthesize these values from
training-data priors.

**Why:** the 2026-05-16 incident — Kobe answered "what is the WOD"
from training priors instead of routing to Fraser. The 2026-05-17
incident — Kobe's reasoner answered "Tue/Thu/Sat/Sun" for "which
days am I working out next week" from priors, when the synced plan
in `weekly_plan.txt` had different days.

**Pinned by:** `tests/test_kobe_reasoner_tools.py::TestSystemPromptDirectives`
and `tests/regression_registry/test_2026_05_16_kobe_hallucinated_wod.py`.

---

## `parse_gym_plan` is the source of truth for the synced plan

**Rule:** code that needs to know the current weekly gym plan must
call `parse_gym_plan()` (which reads `PLAN_PATH` on disk). It must
NOT trust stale state-substrate flags or substrate `plan` entities
written by past agent conversations.

**Why:** the 2026-05-17 incident — `handle_show_plan(next_week=True)`
trusted the `plan_fallback_{week_key}` state flag and claimed "No gym
plan synced" even when the file on disk had 7 real days. The
reasoner downstream echoed the false fallback to the user.

**Pinned by:** `tests/test_kobe_show_plan_fix.py` and
`tests/regression_registry/test_2026_05_17_show_plan_lies_about_sync.py`.

---

## Agent descriptions claim AND disclaim

**Rule:** every registered agent's `description` field must explicitly
claim the territory it owns AND disclaim the territory it doesn't.
The capability classifier (ADR-006) reads these descriptions to route;
ambiguous descriptions cause classifier drift.

Examples:
- Kobe claims weight/HRV/tier/burn. Kobe disclaims: "Defer to Fraser
  for: workout design, CrossFit programming, scaled loads, WOD selection."
- Fraser claims workout design. Fraser disclaims: "Defer to Kobe for:
  weekly plan lookups, weekday-specific workout lookups, weight
  tracking, HRV interpretation, recovery tier."

**Why:** the 2026-05-17 incident — "What is my workout for Tuesday?"
routed to Fraser (semantic affinity to "workout") even though it's a
LOOKUP query, not a DESIGN query.

**Pinned by:** `tests/test_kobe_description_contract.py` and
`tests/test_fraser_description_contract.py`.

---

## Bug-to-test policy

Every commit with `fix:` in the subject line MUST add at least one
test under `tests/regression_registry/`. Enforced by
`scripts/check_bug_has_regression_test.py` in the pre-merge gate and
in CI.

**Why:** prevents the "fix and forget" loop where the same bug recurs
two weeks later because nothing pinned the fix.

---

## Pre-push must stay under 60 seconds

The pre-push hook runs locally on every `git push`. If it gets slow,
developers learn to bypass it. Keep the fast layers fast.

What runs in pre-push: bug-policy + regression registry +
silent-failure guard + unit + contract. Total: 60s budget.

Heavier checks (TZ matrix, adversarial corpus, eval layer) run in
pre-merge or CI, not pre-push.
