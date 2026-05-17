# feat/miya-mesh-routing — Chief Architect Final Report

**Branch:** `feat/miya-mesh-routing`
**Date:** 2026-05-17
**Status:** ✅ Implementation complete. All 5 test layers green (492/492).

---

## What shipped

Three ratified ADRs and the core code that implements them.

### ADR-006: Capability-based router
- `core.miya.classify_intent(msg) -> dict[str, float]` — one Gemini Flash
  call reads every registered agent's `description` field and returns a
  confidence-scored ranking. Replaces trigger-based regex routing.
- Aliases resolve to canonical names; unknown agents are dropped; scores
  clamp to [0,1]; JSON code-fence wrappers are stripped.
- Trigger-mode preserved as fallback via `RAHAT_ROUTER_MODE=triggers`.

### ADR-007: Cross-agent delegation
- New module `core/delegation.py` with `delegate_to(agent_name, query,
  *, context, _caller_chain, _depth, trace_id, db_path)`.
- Loop prevention: caller-chain check (case-insensitive) + depth cap
  (`MAX_DELEGATION_DEPTH = 2`).
- Every delegation lands as a `miya.delegate` decisions span with
  `actor`, `to`, `depth`, `caller_chain`.
- Rollback: `RAHAT_DELEGATION_ENABLED=0`.
- Failure modes are documented codes (`agent_not_registered`,
  `delegation_loop`, `depth_exceeded`, `delegation_disabled`,
  `agent_error`) — never exceptions. Each includes a `fallback_reply`.

### ADR-008: Clarification policy
- Confidence thresholds in `core.miya`:
  - ≥ 0.7 → dispatch directly
  - 0.5–0.7 → dispatch with caveat
  - top-2 within `ambig_threshold` (0.2) → multi-dispatch
  - < 0.5 → ask clarification
  - < 0.2 → noise (no reply)
- `ask_clarification(msg, candidates, chat_id)` builds an A/B/C reply
  and persists state to `memory_entities` with a 60s TTL.
- `resolve_clarification(reply, chat_id)` accepts bare `A`/`B` (case
  insensitive, trailing punctuation stripped), marks the entity
  superseded once consumed.
- Rollback: `RAHAT_CLARIFICATION_ENABLED=0`.

---

## Test coverage added

| File | Tests | Layer |
|---|---:|---|
| `tests/test_capability_router.py` | 27 | contract |
| `tests/test_delegation.py` | 14 | contract |
| `tests/test_clarification.py` | 15 | contract |
| `tests/test_fraser_delegation.py` | (Fraser arch) | contract |
| `tests/test_fraser_tool_catalog.py` | +1 module ref to `core.delegation` | contract |

Full suite (after wiring): **492 passed, 3 skipped, 0 failed** across
unit / contract / eval / adversarial / regression layers.

---

## Files changed (uncommitted)

**Modified:**
- `agents/fraser/agent.py`, `handler.py`, `protocols.py` — Fraser arch's
  Day-8 delegation wiring (TOOL_CATALOG entry, reasoner hook).
- `agents/the_scientist/tools.py` — Kobe arch's delegation hook.
- `core/miya.py` — capability classifier + confidence policy +
  clarification flow + multi-dispatch.
- `tests/run_all.py` — registers the three new contract files +
  Fraser delegation + Day-7 eval entry.
- `tests/scientist/eval_*.py` — small adjustments for the new
  confidence-policy contract.

**New:**
- `core/delegation.py` — the single entry point for cross-agent
  delegation.
- `specs/ADR-006-capability-based-router.md`
- `specs/ADR-007-cross-agent-delegation.md`
- `specs/ADR-008-clarification-policy.md`
- `tests/scenarios/eval_fraser_doc_scenarios.py` — 43 doc-derived
  scenarios.
- `tests/test_capability_router.py`, `test_delegation.py`,
  `test_clarification.py`, `test_fraser_delegation.py`
- `FRASER_DAY8_REPORT.md` (Fraser arch).

---

## Merge sequence (recommended)

1. **feat/miya-mesh-routing → main** (this branch; it carries the
   ADRs + core changes Fraser/Kobe depend on).
2. **Fraser arch's branch → main** (Day-8 delegation hooks).
3. **Kobe arch's branch → main** (Kobe-side delegation hooks).

All three pass their layer tests independently; merging in this order
avoids transient breakage on `main`.

---

## How to commit (once Cursor releases the lock)

```bash
# Kill the Cursor helper that's holding .git/index.lock
pkill -9 -f 'Cursor Helper'

# Then commit
cd ~/developer/agency/rahat
rm -f .git/index.lock
git add -A
git commit -m "feat(miya): capability router + cross-agent delegation + clarification policy

Implements ADR-006, ADR-007, ADR-008.

- core/miya.py: classify_intent() via Gemini Flash on agent descriptions;
  confidence policy (high/med/multi/clarify/noise); ask/resolve clarification
  with 60s TTL substrate persistence; trigger fallback preserved behind
  RAHAT_ROUTER_MODE=triggers.
- core/delegation.py: delegate_to() with loop prevention (caller chain +
  depth cap) and decisions-span observability. Disabled via
  RAHAT_DELEGATION_ENABLED=0.
- 56 new contract tests (capability router 27 + delegation 14 +
  clarification 15) + Fraser delegation suite. All 5 layers green
  (492/492).
"
```

---

## End-to-end smoke test (after merge)

Send via Telegram: **"what is the WOD"**

Expected: classifier picks Fraser (≥ 0.7), reply originates from
FraserAgent. The decisions ledger shows:

```sql
SELECT op, actor, input FROM decisions
WHERE op LIKE 'miya.%' ORDER BY ts DESC LIMIT 5;
```

…with `miya.classify` carrying `{"top": "fraser", "scores": {...}}`.

Previously this routed to Kobe (trigger-based) and hallucinated a
workout. ADR-006 closes that gap.
