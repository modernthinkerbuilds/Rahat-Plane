# Fraser Build — Day 4 Addendum (2026-05-14)

Four pre-reasoner items landed per the directive. All four are now in the tree and tested; reasoner replacement is the next commit.

## Landed

- [P0] **PRVN bug fix in isolated commit.** `state.advance_prvn_cycle` now respects `next_week`/`next_day`/`next_phase` kwargs in BOTH the first-call and subsequent-call branches. Switched from the `kwarg or X` idiom to `kwarg if kwarg is not None else X` so a deliberate `next_week=0` or `next_day=0` passes through (rare but valid for protocol-test fixtures). The fix made `fraser_007` XPASS under strict=True, so its xfail mark dropped in the same commit per the cadence — first xfail mark gone. 9 xfails remain in `tests/evals/test_fraser_conversation.py`.
- [P0] **`LLM_FIXTURE_DIR` test mode.** Under `RAHAT_TEST_MODE=1`, `core.llm.generate` looks up a fixture file at `$LLM_FIXTURE_DIR/<sha256(model:prompt)[:16]>.json` BEFORE the wire call. JSON shape mirrors `GeminiUsage` (`text` / `tokens_in` / `tokens_out` / `cost_usd` / `error`). Production safety: the fixture path only fires when `RAHAT_TEST_MODE=1` AND `LLM_FIXTURE_DIR` is set — the env-var is the explicit opt-in, and a separate test pins this to prevent accidental leakage of test data into a production deploy. 5 new tests cover load/missing/env-unset/test-mode-off/key-determinism.
- [P0] **Tool-call tracing to governance_log.** New `core.llm.record_tool_call(actor, tool_name, *, args, result, error, trace_id, db_path)` helper. Adds the `trace_id` column to `governance_log` via idempotent `ALTER TABLE` on first connect (so existing deployments migrate transparently). Subject convention: `f"{actor}.tool.{tool_name}"` — same dotted namespace as Charter kinds, so the audit log filters cleanly. The 90-day debuggability story: `SELECT * FROM governance_log WHERE trace_id=? ORDER BY id ASC` returns the full reasoning chain (generate() → tool calls → workout commit). 4 new tests cover write/error-path/chain-grouping/optional-trace_id.
- [P0] **System-prompt versioning.** New `FRASER_SYSTEM_PROMPT_VERSION = "v1"` constant in `protocols.py`. New `system_prompt_version` field on `WorkoutBody` (Optional, defaults to None for forward-compat with pre-Day-4 rows). `state.commit_workout` stamps the current version on every workout body before writing. The bisectability story is encoded in the constant's docstring: when quality regresses, `SELECT payload FROM memory_entities WHERE type='fraser_workout'` → group by `system_prompt_version`, find the inflection point. Bump trigger documented inline (structural changes bump; transcript content edits don't). 3 new tests pin the constant + round-trip + forward-compat.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **206 passed** (was 194 end-of-Day-4-prep; +12 today — 5 fixture mode + 4 tool-call tracing + 3 system-prompt versioning)
  - eval: 43 passed, 1 skipped
  - adversarial: 14 passed
  - regression: 17 passed
- Standalone Fraser eval: 1 PASS (fraser_007, xfail dropped) + 9 xfailed.
- Above the ≥205 implied target.

## Files touched (this addendum, on top of the earlier Day-4 commit)

```
agents/fraser/state.py                (advance_prvn_cycle first-call branch fix;
                                       commit_workout stamps system_prompt_version)
agents/fraser/protocols.py            (+FRASER_SYSTEM_PROMPT_VERSION constant +
                                       version-history block + bump-trigger doc;
                                       WorkoutBody.system_prompt_version field)
core/llm.py                           (+_fixture_key + _load_fixture +
                                       record_tool_call + ALTER TABLE migration
                                       for governance_log.trace_id)
tests/test_llm.py                     (+9 tests: fixture mode + tool-call tracing)
tests/test_fraser_state.py            (+1 test: system-prompt version stamp)
tests/test_fraser_protocols.py        (+2 tests: WorkoutBody version + constant)
tests/evals/test_fraser_conversation.py  (xfail dropped on fraser_007;
                                          assertion tightened to check day + phase)
```

## Doctrine pins (added this round)

- **Fixture-mode is opt-in via TWO env vars.** Both `RAHAT_TEST_MODE=1` AND `LLM_FIXTURE_DIR` must be set — defense in depth against a production deploy accidentally reading test fixtures from disk.
- **`ALTER TABLE` for in-place schema evolution.** SQLite's `ADD COLUMN` is idempotent under try/except `OperationalError` — safe to run on every connect. Used for `governance_log.trace_id`. Documented inline so future schema additions follow the same pattern.
- **System-prompt versioning is mandatory metadata.** Every `fraser_workout` carries the version. The bump trigger lives next to the constant; the version-history block accumulates one line per bump. Future regressions stay bisectable without git archaeology.

## Reasoner replacement — what's now wired and ready

When the reasoner replacement commit lands, it will:

1. Call `core.llm.generate(actor='fraser', kind='fraser.reasoner', prompt=<built>, trace_id=<minted>)` instead of returning the stub card. Budget gate fires at the wire call; fixture mode satisfies the eval cases offline.
2. Parse the `GeminiUsage.text` for tool-call invocations (Gemini native function-calling per your decision #1). For each tool call, dispatch via the `TOOL_CATALOG` registry, then `record_tool_call(actor='fraser', tool_name=..., args=..., result=..., trace_id=<same>)`.
3. Compose the resulting WorkoutCard. `state.commit_workout` stamps `FRASER_SYSTEM_PROMPT_VERSION` before writing.
4. Eval cases write fixture files to a `tmp_path` LLM_FIXTURE_DIR; the reasoner reads them; the assertions become real coverage; the xfail marks drop one-by-one as cases stabilize.

Each xfail drop is one commit per the strict-mode cadence. The PRVN-fix-and-drop pattern from this addendum is the template.

## Remaining open items (none blocking reasoner)

- The eval layer's `tests/evals/test_fraser_conversation.py` is still NOT in `tests/run_all.py`'s eval-layer paths. Day-6 hookup per spec §8. Until then, Fraser eval cases run as a standalone pytest invocation, not part of the nightly gate.
- The substitution-condition vocabulary (`SUBSTITUTION_CONDITIONS`) is still 7 strings; Day-3 directive's promotion trigger (>5 exhaustive `match` blocks) hasn't fired. ADR-004 §"Substitution conditions" documents the threshold.
