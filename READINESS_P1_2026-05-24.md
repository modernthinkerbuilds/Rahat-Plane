# ADR-011 P1 — Plan-edits via LLM tools (overnight, 2026-05-24)

Branch: `feat/p1-plan-tools` (off `main` @ 7b47b9a). **Not merged** — review when
awake. Full CI-env stack green (contract 758). **Default behavior unchanged**:
the new path is OFF unless you set `RAHAT_PLAN_TOOLS=1`.

## What landed (2 commits)

1. `b4d8042` — **the tool layer** (`agents/the_scientist/plan_tools.py`): typed,
   deterministic wrappers over the existing handlers — `set_rest`,
   `set_crossfit`, `set_zone2`, `mark_unavailable`, `replan`, `report_pain` —
   plus `TOOL_SCHEMAS` (the contract the model sees) and `execute_actions()`
   which runs a validated `[{tool, args}]` list and **never raises** (a bad
   tool/args becomes an error string). Reuses the handlers, so merge/backfill/
   replan logic is identical to the slash path.

2. `8aaf568` — **the LLM planner** (`plan_via_tools`): asks the model for a JSON
   action plan over the schemas, parses it (tolerates ```fences``` and prose),
   and runs it through `execute_actions`. So *"I rested today, running tomorrow,
   replan"* becomes one model turn → `[set_rest today, set_zone2 tomorrow,
   replan]` — no regex scramble. Uses the existing `cio.llm_generate`
   text→text plumbing (LLM-as-planner, not native function-calling) so it's
   hermetic to test and consistent with the classifier.

Wired into `handler._try_plan_mutation` behind **`RAHAT_PLAN_TOOLS`** (default
OFF): when on, the planner runs first and **falls through to the deterministic
regex path** if it yields nothing. So it's strictly additive.

Tests: `tests/regression_registry/test_2026_05_24_plan_tools.py` (17 cases) —
schema/registry sync, fail-safe execution, dispatch, persistence, parse
robustness, planner execute+persist, flag on/off routing.

## How to try it (after merge)

1. Smoke with real Gemini in a scratch shell:
   `RAHAT_PLAN_TOOLS=1 RAHAT_LEGACY_DISPATCH= python -c "from agents.the_scientist import plan_tools; print(plan_tools.plan_via_tools('I rested today, running tomorrow, replan'))"`
   — confirm it returns sensible set_rest/set_zone2/replan results.
2. If good, set `RAHAT_PLAN_TOOLS=1` in the launchd env (`core/com.rahat.miya.plist`
   → `EnvironmentVariables`) and restart. Compound edits now route through the
   planner; everything else is unchanged.
3. Roll back instantly by unsetting the flag — no code change.

## What's still staged (deliberately)

- **Making it the default** (removing the flag) — do it after the real-Gemini
  smoke confirms the planner is reliable on your phrasing. I won't flip a live
  LLM path to default unsupervised.
- **Generalizing `Agent = description + system_prompt + tools` across the 20
  agents** — Fraser's composer + Kobe's plan tools are now the two reference
  examples. The next slice is extracting a shared tool-calling runtime so
  agents #4–#20 are config (prompt + tools), not pipelines. Bigger change,
  best done with you reviewing — captured in ADR-011 §"Migration path" P1.

## Net

`main` is untouched and green. P1's substrate + planner are built, tested, and
reversible behind a flag on `feat/p1-plan-tools`, ready for your review + a
real-Gemini smoke before going live.
