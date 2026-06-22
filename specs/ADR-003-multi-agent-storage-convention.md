# ADR-003 — Multi-agent storage convention (scale-to-20)

**Date:** 2026-05-13  
**Status:** Accepted  
**Context:** The substrate (`core/memory/*`) was built agent-agnostic
from day one (every table carries an `agent` column). Kobe, written
before the substrate landed, still owns four legacy Scientist-private
tables (`intents`, `user_state`, `week_preferences`, plus pure
telemetry like `hrv_log` / `weighin_log` / `weekly_plan`). With ~20
agents planned (Coach, Curriculum, Foodie, Travel, Finance, Music…),
we need a single rule for where future agents store their data so we
don't accidentally rebuild Kobe's pattern N more times.

## Decision

Every new agent stores its state in `core/memory/*`. No new
agent-private tables, no new rows in Kobe's legacy tables.

| Need | Goes in | Schema-level namespace |
|---|---|---|
| Active goals (e.g. weight target, savings target, book count) | `memory_entities` with `type` of the agent's choosing | `agent` column |
| Sticky user preferences (e.g. "dark roast only", "Tue/Thu lifts") | `memory_preferences` | `agent` column + `key` |
| Time-stamped firehose (every meaningful event) | `memory_events` | `agent` column |
| Conversation threads (per topic) | `memory_threads` | `agent` column |
| Entity-to-entity links (cross-agent OK) | `memory_relationships` | follows `entity_a` / `entity_b` |
| Pure domain telemetry (raw readings, logs) | New agent-private table named `<agent>_<kind>` (e.g. `foodie_meals`) | Implicit (table name) |

**Pure telemetry is the only legitimate reason to create a new private
table.** If you find yourself reaching for a KV / preference / goal
shape, use the substrate.

## Why this is safe (vs. Kobe's legacy pattern)

* **No namespace collisions.** Two agents can both have a key `mode`
  in `memory_preferences` because `(agent, key)` is the primary key.
* **Cross-agent reads go through Miya's broker** (`cross_agent_query`
  in `core/miya.py`). Observable, auditable, permission-checkable.
* **Discovery for free.** "What does Foodie remember about me?"
  becomes `SELECT … FROM memory_entities WHERE agent='foodie'`. No
  table inventory needed.
* **Lifecycle is built in.** `memory_entities.status` (active /
  superseded / expired / archived) means every agent gets goal
  retirement semantics without rebuilding them.

## Kobe / Huberman grandfathering

Kobe's legacy tables stay where they are:

* `intents` — duplicates `memory_entities[type='goal']` for Kobe's
  weight targets. New agents do NOT use `intents`. A future ADR will
  retire Kobe's `intents` writes (the `memory_entities` path is
  already the source of truth; the `intents` row is a sidecar from
  pre-substrate code).
* `user_state` — Kobe's `recovery_tier` and `default_cf_pattern` keep
  living here. New agents do NOT write here. A future ADR will
  migrate these two keys into `memory_preferences` with
  `agent='kobe'`.
* `week_preferences` — Kobe-shaped (gym-specific columns). New agents
  do NOT use this table. A future ADR will rename it to
  `kobe_week_preferences` for clarity.
* `weekly_plan` / `weekly_campaigns` / `hrv_log` / `weighin_log` /
  `workout_log` / `nudge_log` / `raw_vitals` — Kobe domain telemetry;
  stays as agent-private per the rule above. **Rename in a future
  pass:** add `kobe_` prefix so the boundary is obvious.

## Helper API for new agents (added 2026-05-13)

`core/memory/api.py` provides agent-scoped one-liners so new-agent
authors never have to write SQL for the common cases:

    from core.memory.api import pref_get, pref_set, goal_active

    pref_set("foodie", "preferred_cuisine", "indian", confidence=0.9)
    cuisine = pref_get("foodie", "preferred_cuisine", default="any")
    goals   = goal_active("foodie", type="weekly_macro")

These wrap `core.memory.*` and accept the same parameters; they exist
to make "the right way" the path of least resistance.

## Contract test

`tests/test_storage_convention.py` source-greps every file under
`agents/<agent>/` and fails if a NEW agent (anything other than
`the_scientist` / `bajrangi` / `kobe` / `huberman`) imports or writes
to the legacy Scientist-private tables. Adding a new agent that
accidentally repeats Kobe's pattern fails this test loudly.

## Retirement plan for Kobe's legacy tables

Deferred work, sequenced behind one full week of green nightlies:

1. **`user_state` → `memory_preferences`** (highest leverage; smallest
   blast radius — 3 keys to migrate). Add an `agent='kobe'` row for
   each existing key. Update `state.state_get` / `state.state_set` to
   read/write `memory_preferences` first, fall back to `user_state`.
   Cut over fully in a follow-up.
2. **`intents` → drop after confirming `memory_entities[type='goal']`
   carries everything `get_active_goal` reads.** Source-grep test that
   no new write to `intents` lands.
3. **`week_preferences` → rename to `kobe_week_preferences`.** Pure
   table rename, no schema change. Belt-and-suspenders by leaving a
   SQL view named `week_preferences` for one release.
4. **`weekly_plan` / `nudge_log` / etc. → rename with `kobe_` prefix.**
   Mechanical, no schema change.

Each of these is its own PR. Doing them all in one PR creates a
~500-line diff with a real chance of subtle bugs in the most
load-bearing handlers (`recalibrate`, `replan_week`, morning brief).

## Rollback / risk

ADR-003 by itself ships zero code mutations to load-bearing paths. The
helper module is additive (new file). The contract test guards
forward — current state is already compliant (only Kobe touches
legacy tables and Kobe is in the grandfather list). If the contract
test fires on a future PR, that PR has gone the wrong direction;
the fix is to use `core/memory/*` instead of legacy tables.
