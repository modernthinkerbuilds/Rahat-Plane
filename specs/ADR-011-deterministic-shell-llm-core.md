# ADR-011 — Deterministic Shell, LLM Core

**Status:** Accepted (2026-05-24)
**Context owner:** Modern Builder
**Supersedes nothing. Sets the agent-design principle for scaling Rahat to ~20 agents.**

## Context

Live testing on 2026-05-23 surfaced a cluster of bugs that look unrelated but
share one root cause:

- "Design a **clean**-based session" → returned **back squat + bench** (the
  explicit request lost to the profile's default emphasis).
- "Reduce the WOD to **under 30 minutes**" → returned a **75-minute** session
  (the rigid 4-section template overrode the stated duration).
- A workout designed at **2:30 PM** opened with "🌙 9pm check / raat hai" (a
  content-regex guessed time-of-day from the word "recovery" in the cool-down).
- `/profile set back squat 120.` was rejected (a regex parser couldn't handle a
  trailing period); the profile rendered `backsquat` (Markdown ate the `_`).

Each is a case of **deterministic code making a judgment call that belongs to
the model**: guessing intent, guessing the right shape/length of an answer,
guessing the time of day from text. Today every agent is, in effect, a bespoke
regex pipeline with an LLM bolted on as an overlay. Fraser alone stacks ~5
hand-tuned heuristic layers (`parse_request` keyword list → input-mode
classifier → `_is_followup_question` → a mandatory 4-section schema →
`voice.py` greeting wrapper). That does not survive being copied across 20
domains, and every layer is a bug source.

## Decision

Split the system into two layers and put each kind of work where it belongs.

### Substrate — stays deterministic
State reads/writes, kcal math, the weekly-plan grid, routing to the right
agent, number/date formatting, and the charter safety vetoes. These must be
exact, fast, and cheap. Moving them onto the model makes them slower, pricier,
and hallucination-prone (`/pace` showing "863 kcal" must be computed, never
generated). **Do not LLM-ify math, lookups, persistence, or routing.**

### Intelligence — is the LLM's job
Interpreting intent ("clean-based", "under 30 min"), deciding refine-vs-new,
choosing session structure/length, voice/greeting, and honoring free-form
constraints. Today this lives in regexes/keyword-lists/fixed templates; that is
the wrong layer.

### The smell test
> If a piece of code uses regexes, keyword lists, or fixed templates to **guess
> the user's meaning** or **the right shape of an answer**, it is in the wrong
> layer. If it reads state or does math, keep the LLM out of it.

### Safety nuance — LLM proposes, deterministic guards dispose
The model may not silently override hard safety limits (no blacklisted
movements; no breath-holding cues with borderline-high BP). Those stay as
deterministic **guards that validate the model's output** (the charter). This
is *not* "all LLM" — it is an LLM core inside a deterministic safety shell.

## The 20-agent contract

Every agent is described by four things — nothing more:

```
Agent = { name, description, system_prompt, tools[] }
```

- **description** → what Miya's classifier routes on (already true).
- **system_prompt** → the agent's domain expertise *and its voice* (Fraser's
  coaching rules; Kobe's planning rules). Voice is spoken by the model, not
  bolted on by a separate wrapper.
- **tools[]** → typed deterministic functions the model calls: `get_plan()`,
  `get_1rms()`, `get_todays_gym_wod()`, `set_rest(day)`, `set_workout(day,type)`,
  `replan()`, `report_pain()`. This is the substrate, exposed to the model.
- **runtime** → a tool-calling loop: (user msg + conversation history + system
  prompt + tool schemas) → the model reads what it needs, writes what it
  decides, composes the reply. No `parse_request`, no `_is_followup`, no
  mandatory schema.

Miya stays a thin **router + context assembler + safety gate**. Onboarding
agents #4–#20 = write a description + system prompt + register tools. Zero new
regex pipelines. The dispatcher's slash routes, the substrate API, the charter,
and the kcal math stay shared and deterministic — that is the reusable shell.

## Consequences

- **+** One mental model for 20 agents; new agents are config, not pipelines.
- **+** Bugs like clean→squat and reduce-to-30 stop being possible — the model
  honors intent because nothing rigid overrides it.
- **−** More LLM round-trips per message (cost + latency). Acceptable for a
  personal mesh; the deterministic fast-path (slash, plan reads) avoids the LLM
  entirely for the common deterministic cases.
- **−** A tool-calling loop is real new infrastructure; migrate **one agent
  end-to-end first** (Fraser), prove it, then fan out.

## Migration path

**P0 (this change — Fraser becomes the reference LLM-core agent):**
1. `voice.py` — time-of-day greetings come from the **clock**, not content.
2. composer — explicit request (movements/focus/duration/format) **overrides**
   the gym WOD and profile defaults; the 4-section schema is a **default, not a
   mandate** (honor the requested duration); pass **real local time**; **unify**
   the design/follow-up path so the model decides refine-vs-new from history
   (delete `_is_followup_question`).
3. `/profile` render — display canonical keys with spaces (no Markdown `_` eat).

**P1 (next — the tool-calling substrate):**
4. Expose plan mutations as **tools** (`set_rest`, `set_workout`, `replan`,
   `report_pain`) so a compound natural-language edit ("Replan, I'm running
   tomorrow, I rested today") is one model turn that calls the right tools —
   instead of regexes that intercept inconsistently. Land behind tests; keep the
   deterministic dispatcher as the fast path + fallback.
5. Generalize the `Agent = description + system_prompt + tools` contract; Fraser
   is the template the other 19 follow.

## Non-goals

- Removing the deterministic dispatcher (it's the fast path and the fallback).
- LLM-computing any number that must be exact.
- A big-bang rewrite. One agent at a time, each behind the green 5-layer stack.
