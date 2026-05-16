# Fraser Build — Day 3 Report (2026-05-14)

## Landed

- [P0] HRV-red bypass rewired to `WorkOrder.priority<=2` — ✅ per your directive.
  - Dropped `_override_hrv_red` payload flag from `state.commit_workout` and `_charter_gate`.
  - Added `priority` kwarg to both signatures (default 5 = normal).
  - Two new Charter policies in `core/charter.py`:
    - `fraser_hrv_red_blocks_workout` — vetoes `fraser.workout.commit` when `ctx['huberman_state']['recovery_color']=='red'`. `priority<=2` is the single-axis urgent lane.
    - `fraser_1rm_increase_needs_green` — vetoes `fraser.1rm.update` increases unless Huberman=green. Decreases always approve. First-time 1RM treated as increase (so % math isn't anchored on a stressed PR). `priority<=2` bypasses.
  - 12 new tests in `tests/test_charter_policies.py` (`TestFraserHrvRedBlocksWorkout`, `TestFraser1RMIncreaseNeedsGreen`). The existing `test_priority_1_bypasses` / `test_priority_2_bypasses` for `quiet_hours` still pass — same axis, no regression.
- [P0] SUBSTITUTION_CONDITIONS vocabulary — ✅ tuple + validator, not Enum.
  - 7-string vocabulary in `protocols.py` (alphabetical, documented in ADR-004): `equipment_missing`, `format_incompatible`, `mobility_limit`, `recovery_gate`, `rx_unavailable`, `time_constrained`, `user_dislike`.
  - `_validate_condition` called from `SubstitutionRuleBody.to_payload()` — typos fail at write-time. Read path is permissive (old rows survive vocab changes).
  - `DEFAULT_SUBSTITUTION_SEED` migrated: `no_rope`/`no_wall_ball`/`no_pull_up_bar`/`no_box` → `equipment_missing`; `user_dislikes_devil_press` → `user_dislike`; `back_loaded_injury`/`overhead_blocked` → `mobility_limit`.
  - 3 new tests: `test_substitution_rule_rejects_unknown_condition`, `test_substitution_conditions_vocab_is_alphabetical`, `test_substitution_conditions_includes_canonical_seven`. Existing tests updated to the new vocab.
- [P0] `core/budget.py` — ✅ two functions, global cap, per-agent observability.
  - `record_spend(actor, tokens, cost_usd, *, trace_id=None)` writes to the existing `decisions` ledger with `op='budget.spend'`. `trace_id` minted from `decisions.new_trace()` when omitted.
  - `check_budget(*, actor=None)` aggregates `cost_usd` over the rolling 24h window. `actor=...` scopes spend (observability); `limit_usd` is the global cap regardless. Returns `{limit_usd, spent_usd, remaining_usd, exceeded, actor}`.
  - Env var `RAHAT_TOKEN_BUDGET_DAILY_USD` controls the cap. Default $5/day. Explicit `0` disables enforcement (rollback story per ADR-005). Unparseable falls back to default (surfaces misconfiguration via tighter-than-intended cap rather than silent disable).
  - 11 tests in `tests/test_budget.py`.
- [P0] `specs/ADR-005-budget-enforcement.md` — ✅ two paragraphs per your spec: the decision (global enforcement, per-agent observability via the `actor`-keyed `decisions` ledger), the promotion trigger (first incident where one agent's runaway loop blows the global cap), the rollback story (env var = 0). The `actor` parameter being present in both signatures from day one is called out as the deliberate seam.
- [P0] ADR-004 amended with `§"Substitution conditions"` — ✅ documents the strings+tuple-validation choice, the (movement, condition) pair-uniqueness contract, and the **real** promotion trigger (count of exhaustive `match condition:` blocks > 5). Includes the mechanical migration recipe.
- [P0] Gate cleared: contract layer 154 → **180** (+26 today). Above your ~165 target. 5/5 layers green, zero regressions.
- [P0] `FraserAgent` flipped on in `core/miya_main.py` — ✅ uncommented per your directive. Description-based routing puts Fraser in the pool; the stub `route()` returns `Reply(confidence=0.1)` so Miya's tie-breaker still prefers Kobe for ambiguous fitness queries. Live deployment safe until the real reasoner lands.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **180 passed** (was 154 end-of-Day-2; +26 today: 12 charter + 11 budget + 3 protocol)
  - eval: 43 passed, 1 skipped (Scientist baseline preserved)
  - adversarial: 14 passed
  - regression: 17 passed
- Pre-flip test count: 180. Post-flip test count: 180. The FraserAgent registration doesn't add tests; it just exposes the route() surface to Miya.

## Charter policy registration verified

Two ways to verify the new policies fire:

1. **Direct unit tests** (12 cases in `TestFraserHrvRedBlocksWorkout` + `TestFraser1RMIncreaseNeedsGreen`) — exercise `charter.review()` with hand-built `WorkOrder` + `ctx`.
2. **Through state.py** — `tests/test_fraser_state.py::test_every_write_appends_to_governance_log` confirms each Fraser write writes one `governance_log` row, regardless of approve/veto.

Both are in the contract layer.

## Doctrine corollaries pinned

- **Single-axis urgent lane.** `priority<=2` bypasses every Fraser gate the same way it already bypasses `quiet_hours`. No parallel `_override_*` payload flags. ADR-004 §"Cross-agent reads" gets a sibling principle: single-axis instrumentation across the Charter.
- **Vocabulary, not Enum.** Strings + tuple validation matches the `dislikes.SCOPES` / `BLACKLIST` pattern. Promotion to Enum is a 5-exhaustive-match trigger, not a vibe. The migration recipe is in ADR-004.
- **Global enforcement, per-agent observability.** Budget caps are mesh-wide; per-agent slicing is a SQL filter. ADR-005 documents the doctrine and the promotion trigger.

## Surprises

- Zero. Every directive landed without surprise.
- The genai-import path in `agents/the_scientist/agent.py` errors when running `python -c` outside pytest (because `conftest.py` stubs `google.genai`). The `run_all` path uses pytest so it's fine; just worth noting if you ever script a non-pytest smoke import.

## Files touched

```
core/charter.py                       (+~60 LOC: 2 Fraser policies)
core/budget.py                        (NEW — 165 LOC)
agents/fraser/protocols.py            (+~40 LOC: SUBSTITUTION_CONDITIONS,
                                       _validate_condition, validation in
                                       SubstitutionRuleBody.to_payload)
agents/fraser/state.py                (rewired commit_workout + update_1rm
                                       to priority; migrated seed vocab)
core/miya_main.py                     (FraserAgent registered)
tests/test_charter_policies.py        (+12 tests)
tests/test_budget.py                  (NEW — 11 tests)
tests/test_fraser_protocols.py        (+3 tests; updated wall_ball assertion)
tests/test_fraser_state.py            (updated to new vocab)
tests/run_all.py                      (+1 entry: test_budget.py)
specs/ADR-005-budget-enforcement.md   (NEW)
specs/ADR-004-five-file-agent-pattern.md  (+§"Substitution conditions")
```

## Reasoner work — explicit scope deferral

The actual Day-3 reasoner work (replace `_reasoner_stub` with a real Gemini 2.5 Flash call, build the system prompt from the now-populated `FRASER_BEHAVIORAL_TRANSCRIPT.md` + tool catalog, drop the xfail marks as eval cases invert) is intentionally NOT in this commit. The contract here was:

> "Then on to Day 3's reasoner work. Flip `FraserAgent` on in `core/miya_main.py` after the three above land + tests green. Run the full 5-layer nightly before any reasoner integration — I want to see contract layer at 154 → ~165 before any LLM-touching code goes in."

Three items landed, gate cleared at 180, FraserAgent flipped. The next commit on this branch is the reasoner integration. Flagging that explicitly so you can review the prep work in isolation before LLM code touches the tree.

## Decision needed from you before reasoner integration

1. **Reasoner-call budget gate.** Where should `check_budget(actor='fraser')` get called? Three options:
   - (a) Inside `_reasoner_stub`'s replacement — before every LLM call.
   - (b) In `FraserAgent.route()` — gates all reasoner traffic.
   - (c) As a Charter policy on a new `fraser.reasoner.call` kind that wraps the LLM call as a WorkOrder.
   - My take: (c) — it's substrate-symmetric and the `governance_log` row falls out for free, matching the ADR-005 doctrine. But it adds a Charter kind we don't strictly need.
2. **Tool catalog manifest format.** The reasoner's tool-call manifest needs a JSON-schema-ish description of each tool (name, args, return shape). Two options: (a) hand-roll dataclasses in `protocols.py` and serialize; (b) generate from inspect signatures. I'd default to (a) for explicit control over what the LLM sees; (b) is brittle for the reasoner's strict-JSON contract.
3. **XFAIL→XPASS removal cadence.** When the reasoner produces real movements, the 7 vacuous XPASS cases in `tests/evals/test_fraser_conversation.py` will assert real constraints. Remove the `@pytest.mark.xfail` marks one-by-one as each case validates, or batch-drop after all 10 produce consistent output? My take: one-by-one so the diff is auditable; batch-drop hides which case stabilized when.
