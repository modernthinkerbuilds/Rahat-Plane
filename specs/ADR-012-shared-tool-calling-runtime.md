# ADR-012 — Shared Tool-Calling Runtime (the 20-agent substrate)

**Status:** Proposed (2026-05-24) — awaiting go-ahead. Does not change live code.
**Context owner:** Modern Builder
**Builds on ADR-011 (deterministic shell / LLM core). Makes the four-part agent contract real in code.**

## Context

ADR-011 set the principle and the target contract:

```
Agent = { name, description, system_prompt, tools[] }
```

P0 (Fraser composer) and P1 (Kobe plan-tools) shipped the first two working
instances and proved the pattern end-to-end with real Gemini. But the contract
is **not yet real at the base-class level**, and the two references are two
*different halves* of the loop ADR-011 describes — not one repeatable template.
Onboarding agent #4 today still means hand-writing a `route()` and, if it
mutates state, a bespoke tool/parse layer. That is exactly the linear growth
ADR-011 exists to stop.

This ADR extracts the loop **once**, into a shared runtime, so that agents
#4–#20 are declaration (a `system_prompt` + a registered tool set) rather than
pipelines.

### Findings that shaped this ADR (verified against `main @ eabae6a`)

- `core/agent.py:54–65` — the base `Agent` contract is
  `{name, description, version, aliases, triggers}` with an abstract `route()`
  and a no-op `tick()`. **There is no `system_prompt` and no `tools[]`.** The
  ADR-011 contract is still aspirational in code.
- `agents/the_scientist/plan_tools.py:38–47` — the transitional smell is real:
  `_set_crossfit` builds the string `f"pick {days} for crossfit"` and
  `_set_zone2` builds `f"{day} for run"`, both re-parsed by
  `handle_pick_days`. Tools serialize back to natural language.
- `agents/the_scientist/plan_tools.py:217` — `plan_via_tools(msg, db_path=None)`
  accepts `db_path` but never threads it into the handlers (the `RAHAT_TEST_MODE`
  global guard masks it). Fraser's composer threads `db_path` everywhere; the
  two references are inconsistent.
- `agents/fraser/composer.py:133–139` — Fraser reads state via deterministic
  bridges (`athlete_profile`, `kobe_bridge`, `huberman_bridge`, `pain_state`,
  `chat_memory`) baked directly into the prompt. It has **no tools** — every
  read is hand-assembled.
- `agents/the_scientist/handler.py:2578` + `:1190` — Kobe's `route()` runs
  `dispatcher.dispatch(msg)` first (the ADR-009 fast path); the flag-gated
  planner lives inside `_try_plan_mutation`, reached only via the
  `plan_mutation` route (last in the table).
- `core/miya.py:732`/`:742` — agents are invoked as `agent.route(msg, **kwargs)`
  with `chat_id` and `db_path` as the optional ABI. This is the seam the runtime
  plugs into.

### The two halves, today

| | Kobe `plan_tools.py` | Fraser `composer.py` |
|---|---|---|
| Role | **write** half | **read + voice** half |
| Contract to model | `TOOL_SCHEMAS` (declarative) | one large `system_prompt` |
| Reads | — | hand-assembled via bridges |
| Writes | `execute_actions` (never raises) | — |
| Compose | deterministic (`handle_show_plan`) | LLM composes the reply |
| Smell | NL round-trip; `db_path` dropped | no tools; all reads baked in |

Neither does the full ADR-011 runtime — *read tools + write tools + compose in
one turn*. The compound-edit case ("replan, I'm running tomorrow, I rested
today") works in P1 only because it is pure-write; a turn that must **read then
write** (e.g. "make today easier than yesterday") has no home.

## Decision

Introduce three pieces and migrate onto them behind flags, one agent at a time.

### 1. `core/tools.py` — `Tool` + `ToolRegistry`

A `Tool` is `{name, description, args (typed schema), fn, kind: read|write}`.
Tools take and return **structured** values; the runtime serializes them for the
model and for the user-facing render. `ToolRegistry.schemas()` generates the
planner contract — superseding the hand-written `TOOL_SCHEMAS`. A contract test
asserts the registry stays in sync with the registered callables (the assertion
`plan_tools` already implies).

This is where the NL round-trip dies: `set_days(indices=[0,2,4], kind="cf")`
calls a **structured handler core** directly; nothing re-serializes to a
sentence.

### 2. `core/runtime.py` — the loop, lifted from `plan_via_tools` + `design_session`

```
run(msg, system_prompt, registry, *, chat_id, db_path, compose: bool):
  prompt   = build(system_prompt, registry.schemas(), history, context)
  raw      = io.llm_generate(prompt)                 # hermetic text→text
  actions  = parse(raw)                              # tolerant; [] on failure
  results  = registry.execute(actions)               # never raises
  results  = charter.dispose(results)                # safety shell (see below)
  return   compose_llm(results, system_prompt) if compose
           else deterministic_render(results)
```

Same hermetic `core.io.llm_generate` plumbing (LLM-as-planner, not native
function-calling), consistent with the classifier; honors `RAHAT_TEST_MODE`;
**threads `db_path` and `chat_id` throughout** (closing the `plan_via_tools`
gap). A turn may call read tools to assemble context, then write tools to
mutate, then compose — the full loop, once.

**Safety shell — "LLM proposes, deterministic guards dispose" (ADR-011 §Safety).**
Write-tool results pass through a deterministic charter gate (blacklist
substitution, no breath-holding cues with a standing cardio-caution flag) before they are
committed or rendered. The gate is **runtime-level**, so every agent #4–#20
inherits it for free rather than re-implementing it.

### 3. Extend `core/agent.py`

Add `system_prompt: str = ""` and `tools: list[Tool] = []` to the base class,
and provide a **default `route()`** that calls `runtime.run(...)`. Agents that
want the deterministic fast path keep overriding `route()` (Kobe keeps the
ADR-009 dispatcher in front); config-only agents inherit the default and are
pure declaration.

### Coexistence with the dispatcher (ADR-009) — unchanged

The single ordered dispatcher stays **first** and stays the fast path +
fallback for the common deterministic cases (slash, reads, numeric logs). The
runtime only handles what falls through — the open-ended and compound intents.
This honors the ADR-011 non-goal ("do not remove the deterministic
dispatcher").

### The contract becomes real

```
Agent = { name, description, system_prompt, tools[] }     # route() = runtime default
```

Onboarding #4–#20 = write a description + a system prompt + register tools.
Zero new regex pipelines. Miya stays a thin router + context assembler + safety
gate, exactly as ADR-011 specifies.

## Consequences

- **+** One loop, one mental model for 20 agents. Huberman (#4) becomes the
  first **config-only** agent — the concrete proof the plumbing stopped growing
  linearly.
- **+** The NL round-trip and the dropped-`db_path` inconsistency are fixed
  *structurally* (one tool contract, one threaded loop), not patched per-site.
- **+** Read and write unify: the model can read-then-write in a single turn —
  the case P1 only half-solved.
- **−** Real new infrastructure (`tools.py` + `runtime.py`). Must land behind
  the green 5-layer stack, one agent at a time (ADR-011 §Consequences).
- **−** An LLM compose step adds a round-trip to mutation turns that today
  render deterministically. Gate it per-agent so pure-mutation paths skip
  compose and stay cheap/exact.
- **−** LLM-as-planner-over-text is weaker than native function-calling for
  multi-hop reads. Acceptable now (hermetic + consistent with the classifier);
  revisit if multi-hop read chains appear.

## Migration path

Each step is flag-gated, green across unit/contract/eval/adversarial/regression,
and ships with one `tests/regression_registry/test_YYYY-MM-DD_*.py` per `fix:`
(house rule). Rollback for every step is the same lever as P1 today: remove the
flag line from `.env` and `launchctl kickstart -k gui/$(id -u)/com.rahat.miya`.

- **M0 — On-ramp (prerequisite).** Add a structured core to the Kobe handlers
  (e.g. `set_days(indices, kind, next_week)`) that `handle_pick_days` /
  `handle_rest_day` / `handle_unavailable` wrap; point `plan_tools` at the
  structured core; delete the NL round-trip. Thread `db_path` through
  `plan_via_tools`. Bounded, reversible — the natural first change.
- **M1 — Runtime + Kobe port.** Build `core/tools.py` + `core/runtime.py`; port
  Kobe's plan-tools onto them behind the existing `RAHAT_PLAN_TOOLS` flag.
  Behavior identical; tests pin parity against the current planner.
- **M2 — Fraser onto the runtime.** Bridges → read tools; `_SYSTEM_DIRECTIVE` →
  `system_prompt`; `design_session` → runtime default. New flag
  `RAHAT_FRASER_RUNTIME` (default OFF); prove parity against
  `FRASER_GEMINI_CHAT_REFERENCE` evals; flip when green.
- **M3 — Contract real.** Extend `core/agent.py` with `system_prompt` + `tools`
  + default `route()`; Kobe and Fraser declare via the contract.
- **M4 — Huberman (#4).** Onboard the recovery stub as the first config-only
  agent. If it works as pure declaration, the milestone is proven.

## Non-goals

- Removing the deterministic dispatcher (fast path + fallback).
- Native function-calling — stay on LLM-as-planner-over-text for hermeticity and
  consistency with the existing classifier.
- LLM-computing any number that must be exact (kcal, target weights) — those
  stay deterministic substrate (ADR-011).
- A big-bang rewrite. One agent at a time, each behind the green 5-layer stack.

## Open decisions (need the owner's call before M1)

- **A. Compose for mutation turns.** Keep Kobe's plan edits on the deterministic
  single-render (cheap, exact, what's live), or let them gain an LLM compose
  (warmer, variable copy)? *Recommendation: deterministic for Kobe,
  LLM-compose for Fraser; the runtime supports both via a per-agent flag.*
- **B. Flag strategy for M1.** Reuse `RAHAT_PLAN_TOOLS`, or introduce
  `RAHAT_RUNTIME`? *Recommendation: a new flag isolates the runtime port from
  planner semantics, so a regression in either is independently reversible.*
- **C. Safety-shell placement.** A runtime-level charter gate all writes pass
  through, or per-tool validators? *Recommendation: runtime-level, so #4–#20
  inherit it for free.*
- **D. Sequencing.** M0 on-ramp first (recommended), or build the runtime
  scaffolding first and clean the round-trip during M1?
