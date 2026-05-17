# Fraser Build — Day 8 Report (2026-05-16)

**Branch:** `feat/fraser-mesh-routing` (off main, after Day-7's FraserAgent flip landed).
**Motivating bug:** 2026-05-16 production — Fraser registered but architecturally invisible; every workout question routed to Kobe, Kobe hallucinated answers. Root cause: empty triggers + `Reply(confidence=0.1)`.
**Status:** All 6 deliverables landed. 5/5 layers green at contract=380 / eval=53.

## What landed

### 1. `FraserAgent.description` rewritten per ADR-006
Verbatim adoption of the target form from the brief, plus the "DOES NOT own" clause that disambiguates Fraser's territory from Kobe's. The classifier reads this string in `core/miya.py::classify_intent` — without the disclaimer, Kobe wins workout questions because its description overlaps Fraser's. The new description leads with "CrossFit + Zone-2 workout designer" and includes 8 concrete user-phrasings the classifier will see in actual queries.

### 2. `Route()` confidence bumped 0.1 → 1.0 / 0.3
- **1.0** for real cards (workout-design path).
- **0.3** for delegation failure (so Miya's tie-breaker treats it as a soft handoff).
Day-1's 0.1 stub was the bug — Miya's classifier would only land on Fraser for the narrowest queries, and even then the low confidence got out-voted.

### 3. `delegate_to` wired into Fraser's tool catalog
The Chief Architect's `core/delegation.py` was already in tree when I branched. I added:
- `TOOL_CATALOG` entry for `delegate_to` with `agent_name` + `query` + optional `context` schema. The description explicitly names the 2026-05-16 bug as the failure mode the tool exists to prevent.
- `handler._should_delegate(msg) -> str | None` — keyword detector for Kobe (weight / weekly burn / HRV bands / tier) and Huberman (sleep / RHR) territory.
- `handler.route()` calls `delegate_to` when `_should_delegate` fires; surfaces the response with attribution ("kobe says: ...") at the delegated agent's confidence.

The widened tool-catalog coverage test (`tests/test_fraser_tool_catalog.py`) now sources callables from `tools.py + state.py + source.py + core.delegation` so `delegate_to`'s home outside the agent module doesn't trip the guardrail.

### 4. System prompt updated with DELEGATION POLICY block + v4 bump
`_build_system_prompt` now leads with an explicit "DELEGATION POLICY (read first)" section listing what to delegate to Kobe vs Huberman, with the motivating-bug callout inline. `FRASER_SYSTEM_PROMPT_VERSION` bumped to `v4` with history entry. Every Workout Card committed from now on stamps `system_prompt_version="v4"` per the bisectability story established Day-4.

### 5. `tests/test_fraser_delegation.py` — 39 tests across 7 sections
- Section 1: description carries territory + DOES NOT own boundary + delegation targets named + triggers stay empty (per ADR-006).
- Section 2: route() confidence semantics (1.0 handled, 0.3 declined).
- Section 3: `_should_delegate` correctly routes 7 workout queries to Fraser (None), 7 Kobe-territory queries, 5 Huberman-territory queries, handles empty/None.
- Section 4: TOOL_CATALOG has delegate_to with required schema fields.
- Section 5: System prompt carries DELEGATION POLICY block + names both targets + warns against hallucinating + version is v4.
- Section 6: End-to-end **negative-space contract** — Fraser invokes `delegate_to('kobe', ...)` for weight-target questions, `delegate_to('huberman', ...)` for sleep questions, and DOES NOT invoke delegate_to for workout queries. The acceptance test for the 2026-05-16 bug.
- Section 7: End-to-end classifier integration — given a Fraser-favoring score dict, `miya.route` dispatches to Fraser; given a Kobe-favoring score dict, dispatches to Kobe. Pins the description-correctness contract independent of live LLM availability.

### 6. S1.workout un-mark in `tests/scenarios/eval_fraser_doc_scenarios.py`
The original SKIP condition was "classifier picked non-fraser" — which can mean two different things:
- **Production scenario**: real LLM ran, picked Kobe → Fraser-side regression, should fail.
- **Sandbox scenario**: stub LLM returned garbage, classify_intent returned {}, Miya fell back to triggers, Kobe won by default (Fraser has empty triggers per ADR-006) → not a Fraser bug.

I rewrote the test to distinguish: if any `triggers_fallback` decision row exists for this trace, it's the sandbox path → still SKIP with a clear message pointing to the unit-level proof in `test_fraser_delegation.py`. Otherwise (real classifier ran), it's a hard assert. In production with `GEMINI_API_KEY`, the SKIP becomes a real PASS automatically. No code change needed at the cutover.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **380 passed**, 2 skipped (handler regression intra-platform skips; not Fraser)
  - eval: 53 passed, 1 skipped
  - adversarial: 14 passed
  - regression: 17 passed
- `tests/scenarios/eval_fraser_doc_scenarios.py`: 41/41 real passes + 2 skipped (S1.workout per above + one unrelated).
- Total new Fraser tests Day-8: **39** in `test_fraser_delegation.py`.

## Coordination with Kobe Architect

Both threads landed in parallel against `core/delegation.py` (Chief Architect's), which exposes the canonical `delegate_to(agent_name, query, *, context=None, _caller_chain=(), _depth=0, trace_id=None, db_path=None)` signature. No integration friction:
- Fraser's `handler.route()` imports `core.delegation` and calls the function with the public kwargs only (`_caller_chain` and `_depth` stay at defaults — the brief's caller is the user, not another agent).
- Loop prevention is structural (depth cap + caller-chain check) and lives in `core.delegation`, so adding more agents doesn't change Fraser-side code.
- The `decisions` ledger gets `miya.delegate` spans from every cross-call — observability is automatic.

## Honest gap (flagged for review)

**Description-routing depends on a working classifier.** The unit test `TestClassifierPicksFraserForWorkoutQueries` mocks `core.io.llm_generate` to return a known-good classifier response, then asserts Miya routes to Fraser. That proves the description + classifier-policy + dispatch chain works WHEN the LLM behaves. It does NOT prove the LLM behaves — that requires a real GEMINI_API_KEY in production. The S1.workout scenario test handles this honestly by skipping on the triggers-fallback path.

If production turns up cases where the real classifier picks Kobe for workout queries despite the new description, the next iteration is tightening the description further (more concrete user-phrasings, sharper boundary language) — not adding triggers back.

## Files touched (Day 8)

```
agents/fraser/agent.py                  (description rewritten; version bumped to 0.8.0)
agents/fraser/handler.py                (+_should_delegate, +_KOBE/_HUBERMAN patterns,
                                         route() now delegates + bumps confidence to 1.0,
                                         _build_system_prompt adds DELEGATION POLICY block)
agents/fraser/protocols.py              (FRASER_SYSTEM_PROMPT_VERSION → v4 with history,
                                         delegate_to manifest in TOOL_CATALOG)
tests/test_fraser_delegation.py         (NEW — 39 tests / 7 sections)
tests/test_fraser_tool_catalog.py       (coverage helper widened to include core.delegation)
tests/run_all.py                        (+test_fraser_delegation.py in contract layer)
tests/scenarios/eval_fraser_doc_scenarios.py
                                        (S1.workout un-marked: assert on real-classifier
                                         path; skip with clear reason on triggers-fallback
                                         path)
FRASER_DAY8_REPORT.md                   (NEW — this file)
```

`core/delegation.py` was already in tree from Chief Architect's parallel work — not modified.

## Cutover note

This branch is `feat/fraser-mesh-routing` off main. Day-7's FraserAgent flip is already on main, so this branch's diff is purely the routing/delegation layer. Merge order doesn't matter relative to the other Day-8 specialist threads (Kobe, Huberman) because they touch different agent files; ADR-006/-007/-008 changes are additive at the protocol level.

When you merge: restart Miya → ask a workout question → confirm Fraser routes (classifier picks Fraser, route() returns confidence=1.0, real card delivered). Ask a weight question → confirm the delegate_to path fires (Kobe's response surfaces with attribution).
