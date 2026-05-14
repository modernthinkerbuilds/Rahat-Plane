# ADR-004 — Five-file agent pattern

**Status:** Accepted
**Date:** 2026-05-14
**Owner:** Modern Builder
**Related:** ADR-001 (Rahat control plane), ADR-002 (rebrand risk), ADR-003 (multi-agent storage convention)

---

## Context

The Scientist→Kobe split (Phase 4D, `specs/PHASE_4D_R1_PLAN.md`) established
a four-file shape for an agent: `protocols.py` / `state.py` / `handler.py` /
`main.py`. That shape was sufficient when Scientist's "tools" were just
state reads — `latest_weight`, `current_plan`, `recalibrate_intents`.

Fraser (the CrossFit programming agent, `specs/FRASER_REQUIREMENTS.md`)
breaks that assumption. Fraser's spec §4 catalogs computational tools
that don't read state:

- `compute_target_weight(lift, %, one_rm_kg)` — 1RM × % math.
- `compute_predicted_burn(card)` — per-movement kcal coefficients.
- `lookup_movement_cues(movement)` — static cue table (Hunch, Neck Guard, HBP).
- `parse_user_workout(raw_text)` — freeform → Workout Card schema.

These are pure transforms. Putting them in `state.py` hides DB-orthogonal
logic behind a name that promises substrate I/O. Putting them in
`handler.py` couples reasoner orchestration to coefficient tables.
Putting them in `protocols.py` (Day-1 placement instinct) mixes
types-as-contracts with stateful coefficient lookups.

## Decision

Adopt a **five-file pattern** for every post-ADR-003 agent:

```
agents/<name>/
├── protocols.py    # types, dataclasses, enums, normalizers,
│                   # charter-rule schemas, constants. No I/O.
├── state.py        # substrate wrappers (core.memory.api). DB I/O lives
│                   # here. Charter-gated writes. Cross-agent reads.
├── tools.py        # pure computational transforms. Static lookup tables.
│                   # Math. No DB. No LLM. No charter.
├── handler.py      # orchestration. Reasoner loop. Input-mode routing.
│                   # Imports state + tools. Calls the LLM.
├── main.py         # thin entrypoint. Star re-exports the cascade
│                   # (protocols → state → tools → handler) for the
│                   # importlib short-name contract.
└── agent.py        # Miya Agent ABI wrapper. Loads main.py via importlib.
```

The boundaries are enforced by import direction:

```
protocols ← state ← tools ← handler ← main
                                ↑
                              agent
```

`tools.py` MUST NOT import `state.py`. Otherwise DB I/O hides behind
a name that promises a pure transform — the same drift this ADR exists
to prevent.

## Star-import cascade order in `main.py`

```python
from agents.<name>.protocols import *
from agents.<name>.state import *
from agents.<name>.tools import *
from agents.<name>.handler import *
```

Order matters: `handler.py` star-imports from `state.py`, so by the
time `main.py` runs, `state`'s symbols are already cached. Adding
`tools.py` between `state` and `handler` means handler can opt-in to
star-import `tools` too once the Day-3 reasoner needs them — which
keeps `fraser.compute_target_weight` reachable from any caller.

## Cross-agent reads — substrate-symmetric

A corollary of ADR-003: when agent A needs agent B's state, A calls
`core.memory.cross_agent_list(type=<entity_type>)`. B writes a
versioned entity on every state change. Neither agent grows a
public read API method.

Example: **Fraser reading Kobe's tier (locked in 2026-05-14).**

```python
# Kobe's write side (lands Day 4 of the Fraser build):
core.memory.put_entity(
    agent="kobe", type="kobe_tier",
    payload={"tier": "hammer", "set_at_iso": "..."},
    supersede_existing=True)

# Fraser's read side (already wired in state.get_kobe_tier):
rows = core.memory.cross_agent_list(type="kobe_tier", status="active", limit=1)
tier = (rows[0]["payload"].get("tier") if rows else "zone2")
```

Why substrate-symmetric:

- **ADR-003 uniformity.** Adding the 11th, 12th, 21st agent doesn't
  multiply public read surfaces.
- **Zero new public methods.** `KobeAgent` stays focused on its own
  routing surface; the read contract lives in the substrate.
- **`governance_log` captures the cross-agent read for free.** Every
  substrate write lands in `memory_events` — the audit trail is
  complete without bespoke instrumentation.
- **Latency is fine.** Cross-agent reads happen at decision
  boundaries (workout design start, tier-change observation),
  not per-token.

## Consequences

**Positive:**

- New agents inherit a uniform shape — onboarding the 12th agent costs
  the same as the 2nd.
- Pure-transform tools are unit-testable in isolation. `tools.py` has
  no fixtures, no DB, no LLM stubs.
- The substrate-symmetric cross-read pattern composes cleanly:
  multi-hop reads (Fraser → Kobe → Huberman) work without growing
  any agent's public surface.

**Negative:**

- The four-file shape pinned in `specs/PHASE_4D_R1_PLAN.md` is now
  out of date for new agents. The Scientist itself stays grandfathered
  (consistent with ADR-003 grandfathering).
- The mapping "I want to add a new tool" → "which file?" is a
  judgment call when the tool reads state AND transforms it. The
  rule: if the function's body contains a `_mem*` call, it goes in
  `state.py`. If it doesn't, it goes in `tools.py`. The handler
  composes them.
- One more file to wire into the star-import cascade. Mitigated by
  the `main.py` template above.

## Test contract

`tests/test_storage_convention.py` already enforces ADR-003's storage
side. This ADR's structural side is enforced by:

- `tests/test_<agent>_protocols.py` — pure type round-trips.
- `tests/test_<agent>_state.py` — substrate compliance + Charter audit.
- `tests/test_<agent>_tools.py` — pure-transform unit tests.

The eval layer (`tests/evals/test_<agent>_conversation.py`) tests the
orchestration in `handler.py` end-to-end.

## Migration path for Scientist (NOT required for this ADR to land)

The Scientist's `state.py` (1,023 LOC) contains computational tools
that COULD move to a hypothetical `agents/the_scientist/tools.py`
(`compute_week_recalibration`, `latest_weight` math, the gym-day
filter). Doing so:

- Belongs in a separate Phase-4E plan, not this ADR.
- Is grandfathered per ADR-003 — no urgency.
- Would unlock 2–3 imports in `handler.py` to clean up.

Listed here so future-me doesn't re-discover the option.

## Status of agents

| Agent | Files | Shape |
|---|---|---|
| Scientist/Kobe | protocols, state (large), handler, main, agent | Grandfathered four-file |
| Fraser | protocols, state, **tools**, handler, main, agent | Five-file (this ADR) |
| Coach | TBD | Five-file (this ADR applies) |
| Curriculum | TBD | Five-file (this ADR applies) |
| Huberman (was Bajrangi) | TBD | Five-file (this ADR applies); rebrand per ADR-002 |
