# Fraser Build — Day 4 Addendum 2 (2026-05-14)

Three more pre-reasoner items landed per the directive. Reasoner replacement is now the next commit; the gate underneath it is fully wired.

## Landed

- [P0] **FraserAgent registration RE-COMMENTED** in `core/miya_main.py`. Day-3 wiring had it active; the Day-4 directive tightened the gate to "10/10 eval cases passing AND ≥3 manual workout-card reviews". The comment block in `miya_main.py` now spells out both gates so the next person who touches that line knows what unblocks the flip. The class is still importable; nothing in production calls `route()` until the registration goes back on.
- [P0] **VCR-style fixture record mode in `core.llm`.** New `RAHAT_FIXTURE_RECORD=1` env var bypasses cassette playback, hits the wire, and saves the response to `$LLM_FIXTURE_DIR/<key>.json` (overwriting if present — re-records always overwrite, by design). Failed wire calls (`GeminiUsage.error` set) do NOT save fixtures — would create misleading cassettes that play back "success" with an error response. 4 new tests cover record/playback cycle, overwrite, error-no-save.
- [P0] **`--record` flag in `tests/run_all.py`.** Sets `RAHAT_FIXTURE_RECORD=1` in the pytest subprocess env. Loud banner at the top of the run output names the active mode + warns if `LLM_FIXTURE_DIR` or `GEMINI_API_KEY` is unset (preventing the "I recorded a useless cassette" pitfall). Help text documents the cost implications.
- [P0] **Per-actor budget override** via `RAHAT_TOKEN_BUDGET_DAILY_USD_<ACTOR>`. `core.budget._daily_cap_usd` now takes an optional `actor` parameter; lookup order is per-actor env → global env → `DEFAULT_DAILY_USD`. `check_budget(actor='fraser')` returns the Fraser-specific cap when set, else the global. Default position is unchanged — no env override needed for dev. Production deploy sets `RAHAT_TOKEN_BUDGET_DAILY_USD_FRASER=0.50` per the directive's $5-dev/$0.50-prod knob. 6 new tests pin the override / no-actor-isolation / global-fallback / unparseable-fallback / exceeded-trigger / case-insensitive-actor behaviors.
- [P0] **Per-case domain-assertion comments** above every xfail mark in `tests/evals/test_fraser_conversation.py`. Each block enumerates ALL the conditions that must hold to drop the mark — e.g., `fraser_001`: precondition + intensity ≤70% + no overhead + NOTES references HRV. Strict-mode cadence won't let an xfail drop on a weakly-tested case anymore; the comment is the gate, not the test docstring.
- [P0] **ADR-005 amended with the per-actor override paragraph.** Documents the env-var convention (`RAHAT_TOKEN_BUDGET_DAILY_USD_<ACTOR>`), the lookup order, the dev-vs-prod intent, and the distinction from the (future) Charter-policy promotion. Makes clear that env-var tightening is a deployment knob; per-agent Charter policies are a separate, deferred policy decision.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **216 passed** (was 206 end-of-addendum-1; +10 today — 4 fixture-record + 6 per-actor budget)
  - eval: 43 passed, 1 skipped
  - adversarial: 14 passed
  - regression: 17 passed
- Above the ≥210 implied target. Sub-1.2-second contract layer.

## Files touched

```
core/miya_main.py                     (FraserAgent re-commented with the
                                       updated gate spec inline)
core/llm.py                           (+ _save_fixture + _is_recording +
                                       record-mode branch in generate)
core/budget.py                        (_daily_cap_usd now takes optional
                                       actor for per-actor env override)
tests/run_all.py                      (+ --record CLI flag + loud banner)
tests/test_llm.py                     (+4 tests for record/playback cycle)
tests/test_budget.py                  (+6 tests for per-actor override)
tests/evals/test_fraser_conversation.py
                                      (per-case domain-assertion comments
                                       on all 9 remaining xfail blocks)
specs/ADR-005-budget-enforcement.md   (+§"Per-actor override")
```

## Doctrine pins (this round)

- **Re-commenting is reversible; merging FraserAgent on is not.** The "safer to defer than to undo" instinct shows up here too. The class stays importable; the registration line is the binary switch.
- **Fixture record mode is two env vars, not one.** `RAHAT_FIXTURE_RECORD=1` + `LLM_FIXTURE_DIR` together → record. Either alone → no-op (and the banner warns). Defense against the "I thought I was recording" failure mode.
- **Per-actor budget tightening lives in env vars, not Charter policies.** Charter is for cross-cutting policy; deployment-time tightening is for env. ADR-005 calls out the distinction explicitly so the next person doesn't conflate them.
- **Per-case domain assertions document the unblock condition.** The xfail mark is the gate; the comment block is the spec for what flips it. Without that, the strict-mode cadence becomes "drop any mark that passes once" — which is exactly what we're trying to avoid.

## Reasoner replacement readiness

Everything the reasoner replacement needs is now in the tree and tested:

1. `core.llm.generate(actor, kind, *, prompt, trace_id)` — budget-gated, fixture-routable, record-aware.
2. `core.llm.record_tool_call(actor, tool_name, *, args, result, error, trace_id)` — 90-day audit trail.
3. `protocols.TOOL_CATALOG` — 4 manifests ready to round-trip into Gemini's `FunctionDeclaration`.
4. `protocols.FRASER_SYSTEM_PROMPT_VERSION` — stamped by `commit_workout` for bisectability.
5. `RAHAT_FIXTURE_RECORD=1` + `LLM_FIXTURE_DIR` → record once, replay forever.
6. `RAHAT_TOKEN_BUDGET_DAILY_USD_FRASER=0.50` → prod cap.
7. Per-case domain assertions enumerated — each xfail drop is documented.

## Next session — the reasoner commit

Order (no surprises from prior sessions):

1. Replace `handler._reasoner_stub` with `core.llm.generate(...)`.
2. System prompt: structural preamble (`FRASER_CHARTER_RULE_SPECS` + `TOOL_CATALOG` + InputMode classifier rules + `FRASER_SYSTEM_PROMPT_VERSION` header) + transcript body.
3. Tool-call loop with native Gemini function-calling, 8-hop cap, budget brake.
4. Each `tool_call` invocation: `dispatch_tool(name, args)` → `record_tool_call(...)` under the parent trace_id.
5. Record cassettes once via `python -m tests.run_all --record --layer eval` (after enabling Fraser eval cases in the eval-layer paths — Day-6 work).
6. Drop xfail marks one-by-one as eval cases stabilize. Each drop is one commit + the prompt change that made it pass. Strict mode forces the cadence.
7. After 10/10 green AND ≥3 manual reviews: uncomment FraserAgent registration in `core/miya_main.py`.

## Open items (none blocking the reasoner commit)

- Fraser eval file not yet in `tests/run_all.py`'s eval-layer paths. Day-6 hookup.
- `core.llm.generate`'s wire-call uses `cio.llm_generate_with_usage` which doesn't pass `trace_id` to the underlying `decisions.log` span. Minor — the budget spend row carries trace_id; the wire call's own log span doesn't. Worth threading through if Day-5 debugging needs it.
- The `_reasoner_produced_content` precondition checks `card.strength.lifts OR card.wod.movements`. A reasoner that produces ONLY warm-up + cool-down (e.g., a Smash Format pure-recovery day) would fail this. Acceptable for now — every real Fraser workout has either strength OR a WOD; pure-recovery is a Day-5+ format consideration.
