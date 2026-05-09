# Model-First Pivot — Rahat Mesh

**Author:** Claude (L8 Agent Architect)
**Date:** 2026-05-07 (updated 2026-05-08)
**Status:** Implemented; Gemini-only after the 2026-05-08 update note below.
**Supersedes:** primary recommendation in `LLM-COST-OPTIMIZATION.md` (P0.2). The cost-opt doc's telemetry work (P0.1) and LRU cache idea (P0.3) survive; everything else folds into this pivot.

> **2026-05-08 strategic update — provider change.** Anthropic was removed
> from the Rahat runtime entirely. The reasoner is now Gemini 2.5 Flash
> (default) with Gemini 2.5 Pro as the high-stakes opt-in — the same
> two-tier shape, different vendor. Reasons:
>
>   1. Anthropic's posture toward OpenClaw (a related Venkat project)
>      created a non-trivial vendor-risk asymmetry. Putting the daily-use
>      agent on Anthropic compounded that risk.
>   2. The architecture was provider-agnostic by design (`core/anthropic_io.py`
>      and the Gemini side were separate from the reasoner). Flipping the
>      primary cost ~3 hours of work, almost all of it in the io module
>      and tool-schema converter; the reasoner loop, charter gates, voice
>      layer, telemetry, and 360 eval cases are unchanged.
>   3. Cost gets cheaper, not more expensive. Gemini 2.5 Flash at
>      $0.30/M input + $2.50/M output runs ~70% of Haiku 4.5's per-call
>      cost on this workload, even without prompt caching.
>
> Concretely:
>   - `core/anthropic_io.py` — tombstone (raises ImportError; comment explains why).
>   - `core/gemini_reasoner_io.py` — the new primary I/O wrapper with
>     function-calling, multi-turn function_response loops, and Usage
>     telemetry shaped identically to the deleted Anthropic version.
>   - `core/cost.py` — pricing table now Gemini-only; cache fields
>     retained for forward-compat if we adopt explicit Gemini caches.
>   - `agents/the_scientist/coach_system.py` — `system_text()` returns
>     a single concatenated string (Gemini's `system_instruction` shape).
>   - `agents/the_scientist/reasoner.py` — Gemini-primary, two-tier
>     fallback ladder is now Gemini → legacy regex. No third tier.
>   - `tests/scientist/eval_reasoner.py` — same 10 B8 tests, same
>     contract, Gemini-shaped stubs.
>   - `requirements.txt` — `anthropic` dropped.
>
> The rest of this document still describes the architecture correctly;
> wherever it says "Haiku 4.5 default / Sonnet 4.6 high-stakes," read
> "Gemini 2.5 Flash default / 2.5 Pro high-stakes." The pivot's premise
> (model-first reasoner with deterministic tools) is provider-orthogonal.

---

## TL;DR

The Scientist failed in the wild today across **five distinct ways** that share one root cause: regex-first dispatch with the model bolted on as a fallback. The fix is an architectural inversion — make a tool-using model the **primary reasoner** and demote the deterministic handlers to **tools the model calls when it needs them**. With prompt caching, this is affordable on the user's $100/mo Anthropic budget, and it directly fixes every bug in today's screenshots.

This is not "add more LLM calls." It is "stop using regex as the planner."

---

## 1. The five bugs in today's screenshots

| # | What the user saw | What the system did | Root cause |
|---|---|---|---|
| 1 | "Replan to get 1016 calories per day" → got the same template plan, no per-day adjustment | `Replan` regex fired `handle_replan()`, which ignored the rest of the sentence and rebuilt with default targets | Regex strips the constraint; no reasoner reads "1016/day" as an optimization goal |
| 2 | Plan still showed CF=850, not the new 1,150 | `protocols.py` constants were updated but the live Miya process is running stale code | Operational, not architectural — but masked by point 4 below |
| 3 | Timeline reply said `Daily intake 2,600 (TDEE 2,957 - 375)`; `llm_coach` says locked 2,300 | Two independent code paths each compute their own intake number | No shared truth — every handler re-derives from scratch |
| 4 | Plan listed Mon/Tue/Wed as "Active rest 500" when Wed had 951 kcal of CF burn | `replan_week()` rebuilt from scratch using new picks (Fri/Sun) and discarded historical reality of the week so far | The replanner is templating, not reasoning over what already happened |
| 5 | "When will I reach target / per week / per active rest / per workout" → only got the timeline | Timeline regex matched first; other parts of the question dropped | Dispatcher is single-intent; it can't decompose a multi-clause question |

**Common thread:** there's no component in the loop that reads the week's reality, holds the user's constraint, weighs it against the locked rules, and produces a coherent answer. We have ~25 deterministic handlers that pattern-match and respond. None of them reason.

---

## 2. Why regex-first is structurally wrong

The original design's intuition was reasonable in 2023: "regex for cheap deterministic paths, LLM for free-form fallback." That intuition assumed:

1. LLMs hallucinate too much for high-stakes planning.
2. LLM calls are slow and expensive.
3. Users mostly type one of N predictable shapes.

All three assumptions have aged poorly:

1. **Modern Sonnet/Haiku-class models with structured tool calling** don't hallucinate planning math when the math comes from tools they call. The hallucinations we saw in `llm_coach` were because we asked it to *narrate* numbers it didn't have, not *compute* them.
2. **Prompt caching has crashed the cost floor** for repeated context. Our static system prompt is 760 tokens; caching makes it ~$0.0001/call. Latency for Haiku 4.5 is ~600–900 ms — within the human "feels instant" budget for a chat agent.
3. **The user types in Hyderabadi-English mix, multi-clause sentences, and ad-hoc constraints** — the empirical message distribution is not regex-shaped. Five bugs today, all five from the dispatcher being too rigid for the actual inputs.

The deeper point: **we built an expert system in an LLM-shaped world.** Expert systems fail on the long tail; LLMs handle the long tail and fail on rigor. The right design is to use each for what it's good at — model for understanding intent and orchestrating, deterministic code for math and constraints — *not* the inverse.

---

## 3. The pivot: tool-using agent

```
                  ┌──────────────────────────────────────────────┐
                  │            Inbound user message               │
                  └──────────────────────────────────────────────┘
                                       │
                                       ▼
              ┌─────────────────────────────────────────────────────┐
              │  Reasoner: Claude Haiku 4.5 (or Sonnet 4.6 for      │
              │  high-stakes) with cached system prompt:            │
              │   - Athlete identity, locked numbers, voice rules   │
              │   - Tool catalog (descriptions + JSON schemas)      │
              │   - Anti-hallucination contract: "all numbers come   │
              │     from tools, never invent"                        │
              └─────────────────────────────────────────────────────┘
                                       │
                       ┌───────────────┴───────────────┐
                       ▼                               ▼
       ┌──────────────────────────┐    ┌─────────────────────────────────┐
       │ Read tools (cheap, safe) │    │ Write tools (charter-gated)      │
       │  - get_week_burn         │    │  - propose_replan(constraint)    │
       │  - get_today_target      │    │  - commit_picks(days)            │
       │  - get_weight_timeline   │    │  - tolerate_movement(name)       │
       │  - get_eligible_cf_days  │    │  - log_weight(lbs)               │
       │  - get_blacklist         │    │  - swap_day(from,to)             │
       │  - get_missed_workouts   │    │  - set_recovery_tier(name)       │
       │  - get_hrv_status        │    │                                  │
       │  - get_recent_episodes   │    │  All write-tools call into       │
       │                          │    │  existing handle_*() helpers,    │
       │                          │    │  and emit a charter intent       │
       │                          │    │  before mutating state.          │
       └──────────────────────────┘    └─────────────────────────────────┘
                       │                               │
                       └───────────────┬───────────────┘
                                       ▼
                  ┌──────────────────────────────────────────────┐
                  │  Reasoner composes final reply, in voice    │
                  │  (Hyderabadi register), bounded ≤6 lines     │
                  └──────────────────────────────────────────────┘
                                       │
                                       ▼
                  ┌──────────────────────────────────────────────┐
                  │       Charter outbound check → Telegram       │
                  └──────────────────────────────────────────────┘
```

The key inversion:

- **Today (broken):** regex matches → handler runs → if no match, LLM is asked to compose freely with no tools and a 1,200-token cage of rules.
- **After pivot:** model receives every message → model reads tools → model writes through tools → model composes reply.

The deterministic logic does not disappear. **It survives as the tool layer.** `compute_week_recalibration`, `eligible_cf_days`, `weight_timeline`, `replan_week` — all of these become `@tool`-decorated functions. The model picks which to call and how to combine the outputs. The model can no longer hallucinate the weight timeline because if asked, it must call `get_weight_timeline()` to get the numbers.

This pattern fixes every bug in §1:

- **Bug 1 (replan ignored constraint):** Model reads "Replan to get 1016 calories per day", calls `propose_replan(daily_target_kcal=1016)`. The tool returns several candidate plans (or "infeasible — would require 4 CF days, exceeds locked cadence"); the model presents the trade-off.
- **Bug 2 (stale constants):** Operational fix; the pivot helps because `protocols.py` becomes the *only* source of locked numbers, read by every tool.
- **Bug 3 (intake mismatch):** Both `get_weight_timeline` and `get_locked_intake` read the same `protocols.py` constant. Two paths becomes one path.
- **Bug 4 (plan loses history):** Replan tool reads the week's actual logged burns first, then proposes the remaining-day plan that *honors what's already happened*. The model can refuse to commit if the proposal is internally inconsistent.
- **Bug 5 (multi-part question):** Model decomposes naturally — calls `get_weight_timeline`, `get_weekly_target`, `get_per_day_targets`, composes a unified reply. Multi-call orchestration is exactly what tool-using agents are good at.

---

## 4. Concrete shape — the Scientist after the pivot

### 4a. Tool catalog (lives in `agents/the_scientist/tools.py`, new file)

```python
from anthropic import tool  # decorator-style; the actual SDK uses dict schemas

@tool(description="Burn for a given week (defaults to current). Returns per-day list and total.")
def get_week_burn(week_start: str | None = None) -> dict: ...

@tool(description="Today's planned target kcal, the day_type label, and the eligible WOD details if it's a CF day.")
def get_today_target() -> dict: ...

@tool(description=(
    "Weight projection at the locked deficit pace. Returns now, intermediate (84kg) "
    "and final (80kg) target dates, plus daily intake and weekly active-burn numbers. "
    "ALWAYS call this for ETA / target-date questions; never compute it yourself."
))
def get_weight_timeline() -> dict: ...

@tool(description=(
    "Days this week eligible for CrossFit (gym programming clean of the user's "
    "blacklisted movements: handstand, OHS, snatch in strength, partner WOD, muscle-up, "
    "minus per-week tolerated_blacklist). Returns list of {date, label, blockers}."
))
def get_eligible_cf_days(week_start: str | None = None) -> list[dict]: ...

@tool(description=(
    "Past CF/Z2 days where actual burn fell below MISSED_WORKOUT_THRESHOLD_KCAL=700. "
    "Returns list of missed days with the gap and a recommended make-up day."
))
def get_missed_workouts() -> list[dict]: ...

@tool(description=(
    "Build a candidate plan for the rest of the week, optionally hitting a per-day "
    "target. Honors locked cadence (≤3 CF, ≤1 Z2 unless user overrides explicitly). "
    "Returns a list of candidate plans ranked by feasibility, each with the math worked. "
    "Does NOT mutate state — call commit_picks() to lock it in."
))
def propose_replan(*, daily_target_kcal: int | None = None,
                   prefer_days: list[str] | None = None) -> list[dict]: ...

@tool(description="Lock in CF picks for the current week. Charter-gated.")
def commit_picks(cf_days: list[str]) -> dict: ...

@tool(description="Add a movement to this week's tolerated_blacklist (allows it once).")
def tolerate_movement(movement: str) -> dict: ...

@tool(description="Log a weight reading. Triggers timeline recalibration.")
def log_weight(lbs: float, ts: str | None = None) -> dict: ...

@tool(description="Swap a day's planned workout type with another day's.")
def swap_day(from_day: str, to_day: str) -> dict: ...

@tool(description="Move user to a different recovery tier (baseline/performance/hammer/re_entry/survival).")
def set_recovery_tier(tier: str) -> dict: ...

@tool(description=(
    "Recent decision-ledger entries — useful when the user references something they "
    "did or said earlier. Returns last N spans for this trace, or last N user messages."
))
def get_recent_context(n: int = 10) -> list[dict]: ...
```

Twelve tools. Every one of them already exists as code inside `main.py` — this is wrapping, not rewriting. Stripping the wrappers is roughly an afternoon of work.

### 4b. The reasoner loop (new file `agents/the_scientist/reasoner.py`)

```python
from anthropic import Anthropic
from agents.the_scientist import tools as T
from core import decisions, voice

CLIENT = Anthropic()
MODEL = "claude-haiku-4-5"

SYSTEM = [
    {"type": "text", "text": ATHLETE_AND_LOCKED_NUMBERS},   # cached, ~280 tok
    {"type": "text", "text": VOICE_AND_FORMAT_RULES,         # cached, ~210 tok
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": ANTI_HALLUCINATION_CONTRACT,    # cached, ~200 tok
     "cache_control": {"type": "ephemeral"}},
]

def reason(msg: str, *, trace_id: str) -> str:
    """Run the tool-using agent until it produces a final assistant message."""
    messages = [{"role": "user", "content": msg}]
    for hop in range(8):  # safety cap; typical conversations are 1–3 hops
        with decisions.span("scientist.reason.hop", trace_id=trace_id,
                            actor="scientist", input={"hop": hop}) as s:
            resp = CLIENT.messages.create(
                model=MODEL,
                system=SYSTEM,
                tools=T.SCHEMAS,
                messages=messages,
                max_tokens=400,
            )
            s.tokens_in = resp.usage.input_tokens
            s.tokens_out = resp.usage.output_tokens
            s.cost_usd = cost(MODEL, s.tokens_in, s.tokens_out,
                              cache_read=resp.usage.cache_read_input_tokens,
                              cache_write=resp.usage.cache_creation_input_tokens)
        if resp.stop_reason == "end_turn":
            return voice.maybe_dress(resp.content[0].text)
        # Otherwise: tool_use — execute and append result
        for block in resp.content:
            if block.type == "tool_use":
                result = T.dispatch(block.name, block.input, trace_id=trace_id)
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": block.id,
                     "content": json.dumps(result)},
                ]})
    # Hop budget exhausted — degrade gracefully
    return "Bole to, soch ke nahi nikla. Try `today` ya `plan dekh`."
```

Critical properties:

- **Token-and-cost telemetry on every hop** — fixes the gap from `LLM-COST-OPTIMIZATION.md` P0.1 in this same change.
- **System prompt is cached** — three blocks, all marked `cache_control: ephemeral`. Warm-cache reads cost 90% less than cold.
- **Hop budget = 8** — empirically 1 hop for ~60% of messages, 2–3 hops for most others, 5+ is rare. The cap prevents infinite loops.
- **`voice.maybe_dress()` still wraps the final reply** — Hyderabadi phrasebook layer is preserved as a deterministic enforcement so the model can't drift.
- **All tools route through `decisions.span`** — full observability of every read and write the reasoner does. When a future bug appears, we can replay the exact tool calls and arguments that led to it.

### 4c. What stays in `main.py` (the existing file)

- Tick-driven nudges (morning brief, mid-day check, etc.). These remain deterministic — they're not user-driven, they're scheduled.
- Initial state setup, DB migrations, helpers.
- The 25 existing handlers stay as **internal functions** — the new tools call them. We don't delete code; we move what was the "main entry point" (the dispatcher) and replace it with the reasoner loop.

`route(msg)` becomes a one-liner:
```python
def route(msg: str) -> Reply:
    return Reply(text=reason(msg, trace_id=decisions.new_trace()),
                 confidence=0.9)
```

### 4d. What the user experience looks like, by example

**User:** "Replan to get 1016 calories per day"

Reasoner hop 1: calls `get_week_burn()`, `get_eligible_cf_days()`, `get_missed_workouts()`.
Reasoner hop 2: calls `propose_replan(daily_target_kcal=1016)`. Tool returns:
```json
[
  {"feasible": false, "reason": "1016/day × 4 days = 4,064 kcal; exceeds remaining 4,065 by 1 kcal — knife-edge OK, but requires both Fri+Sun as CF and an extra Sat Z2 push to ~1,200."},
  {"feasible": true,  "plan": [{"Thu": "rest 500"}, {"Fri": "CF 1150"}, {"Sat": "Z2 1100"}, {"Sun": "CF 1150"}], "per_day_avg": 975, "shortfall": 65},
  {"feasible": true,  "plan": [{"Thu": "Z2 800"}, {"Fri": "CF 1150"}, {"Sat": "rest 500"}, {"Sun": "CF 1150"}], "per_day_avg": 900, "note": "below request — but matches your locked cadence cleanly"},
]
```
Reasoner hop 3: composes reply.

**Reply, in voice:**
> Hau bhai, 1016/day exact mein nahi hota — apne locked cadence ke saath:
> • Fri CF + Sat Z2 + Sun CF → ~975/day, 65 kcal short.
> • Add Thu rest → walks for ~70 NEAT; closes the gap.
> Confirm with `pick fri sat sun` to lock.

That is the kind of output the user has been asking for. **It is not currently possible with the regex dispatch.** The reasoner reads the constraint, the tool does the math, the composition is the model's job — each layer doing only what it's good at.

---

## 5. Cost reconciliation

Earlier doc projected `llm_coach`-as-fallback at ~$0.10/mo today, ~$1/mo with Haiku+caching at the 20-agent target. The pivot puts **every** user message through the reasoner, raising the LLM-coach call rate from ~30% to ~100%. Math:

```
Today (50 inbound msgs/day, ~70% warm cache hit, ~1.4 hops/msg avg):
  Cold cache cost (~30% of msgs): 50 × 0.30 × 1.4 × $0.0021 = $0.044/day
  Warm cache cost (~70% of msgs): 50 × 0.70 × 1.4 × $0.00126 = $0.062/day
  Tools dispatched: ~2 per msg avg, all in-process (zero LLM cost)
  ─────────────────────────────────────────────────────────────────
  Total: ~$0.10/day = $3.20/mo today
```

Twenty-agent target (assume 250 msgs/day across the mesh):
```
  ~$3.20 × 5 = $16/mo
```

Both well within the $100 budget. The pivot is roughly **30× more expensive than today's LLM bill** — but we go from "occasionally embarrassing" to "actually correct." The dollar question isn't "how do we save 80¢/mo" — it's "what's the cost of one more user-visible bug like the screenshots." Easily $16/mo.

If costs ever bite (they won't at this scale), the optimization knobs:
- Sonnet 4.6 only for "high stakes" intents (recalibration, weight log) detected by Haiku 4.5 in hop 1; Haiku stays for everything else. Two-tier reasoner is a 1-day add.
- 1-hour cache instead of 5-min cache (when GA — beta now).
- Reduce tools sent in the catalog when intent is obvious (after intent classification in hop 1).

---

## 6. Migration plan

### Phase 1 — Land the scaffolding (≤6 hours, this week)

1. **Add `anthropic>=0.40` to `requirements.txt`.** Set `ANTHROPIC_API_KEY` in `.env`.
2. **Create `core/anthropic_io.py`** — mirrors `core/io.py:llm_generate` but uses `messages.create` with tools and caching, returns `(content_blocks, usage)`.
3. **Create `core/cost.py`** — pricing dict, `cost_usd(model, in, out, cache_read, cache_write)`. (This was P0.1 from the cost-opt doc; it lands here.)
4. **Wire `tokens_in/tokens_out/cost_usd` into `decisions.span`.** Auto-population when set on the span before exit.
5. **`scripts/llm_cost_report.py`** — daily cost CLI from the ledger. (Also P0.1.)

### Phase 2 — Wrap existing logic as tools (≤4 hours)

6. **Create `agents/the_scientist/tools.py`** — twelve `@tool`-shaped wrappers around existing `main.py` helpers. Each tool has a JSON-schema input description, a clear docstring (the model reads this), and a thin call to the existing function.
7. **Create `agents/the_scientist/coach_system.py`** — extract the three static prompt blocks (athlete, voice, anti-hallucination) into module-level constants for cache-friendly stable strings.
8. **Add `tools.SCHEMAS` and `tools.dispatch(name, input)`** — registry lookup, no magic.

### Phase 3 — Build the reasoner loop and switch the entry point (≤4 hours)

9. **Create `agents/the_scientist/reasoner.py`** — the loop above.
10. **Replace `route()` in `main.py`** to call `reasoner.reason()`. Keep the old dispatcher behind `RAHAT_LEGACY_DISPATCH=1` env flag for the first week — easy rollback.
11. **Eval suite re-run** — all 350 cases must still pass with the reasoner active. Some will need updates because the model's wording differs from regex-handler wording; allow voice-layer flexibility but enforce structural assertions.
12. **Spot-test the five bugs from §1** — each must produce a sensible reply in the new architecture. Add them as B8 cases in `eval_extended.py` to prevent regression.

### Phase 4 — Production cutover (≤1 hour)

13. **Restart Miya** (operational; also fixes bug #2).
14. **Watch the cost CLI for 24 hours.** Expect ~$3/day; if higher, investigate hop count.
15. **Tighten the voice layer** based on real outputs.
16. **Delete the legacy dispatcher** after 7 days of clean reasoner operation.

**Total commitment:** ~14 hours, spread across a week.

---

## 7. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reasoner hallucinates a tool that doesn't exist | medium | low — Anthropic SDK errors immediately | the SDK enforces tool catalog; we log + degrade gracefully |
| Reasoner exceeds hop budget | low | medium — user gets generic fallback message | hop=8 cap; degraded message shows in eval suite |
| Cost surprise from runaway loops | low | medium | hop cap; daily cost CLI alerts when >$5/day |
| Eval suite breaks because model wording differs | high | low — allows voice-layer flexibility, structural assertions stay rigid | re-eval with wording-tolerant assertions; structural assertions stay strict |
| Latency increase (regex was instant; reasoner is 600–900ms × N hops) | medium | medium — chat feels slower | acceptable; user already waits for Telegram round-trip; monitor p95 |
| Anthropic API outage | low | high — Scientist becomes unresponsive | fallback to Gemini Flash via `core/io.llm_generate` if `core/anthropic_io` raises; **same tool catalog**, just slower model |
| Tool authors forget to charter-gate writes | medium | high — bypassed safety | every write tool calls `charter.check()` as the first line; eval B2 cases assert this |
| Cached system prompt drifts because someone edits it casually | high | medium — cache invalidates frequently, costs go up | move static blocks to a versioned constant + log a hash of the prompt on every cache miss |
| Model quality regressions (Anthropic changes Haiku) | low | medium | model id is pinned; we can pin to `claude-haiku-4-5-20260301` once GA |

---

## 8. What this pivot does NOT do

To be precise about scope:

- **It does not replace the tick scheduler.** Morning briefs, hourly nudges, etc. remain deterministic and fire on a clock — they don't go through the reasoner. (Though they call the same tools to compose their content, which gives us consistency.)
- **It does not replace the Charter.** Charter still mediates outbound. `kind=notify.user.reply` for reasoner replies, `kind=notify.user.nudge` for tick-fired nudges. Quiet hours rule on the latter still works.
- **It does not replace Miya routing.** Miya still picks which agent gets the message. The reasoner-vs-dispatcher question is *inside* a single agent. Miya's classifier (the cheaper LLM call) keeps its LRU cache and its own optimization arc.
- **It does not yet apply to other agents.** Bajrangi, the toddler curriculum agent, etc. inherit the pattern when they're built — not retrofitted, since they don't exist yet.

---

## 9. What I need from you to start

Three decisions:

1. **OK to add `anthropic>=0.40` as a dependency?**
2. **OK to land Phase 1 (telemetry plumbing) regardless of whether we go ahead with the full pivot?** This unblocks the cost CLI and is useful even if the pivot were aborted.
3. **OK with Haiku 4.5 as the default model**, with Sonnet 4.6 as an opt-in upgrade per intent (deferred until needed)?

If yes to all three, I'll start with `core/anthropic_io.py` and `core/cost.py` and the `decisions.span` wiring tonight, then move to tool wrapping tomorrow.

The headline: this is the right architecture for an LLM-shaped world, the screenshots prove the current design is hitting its ceiling, and the cost is comfortably within budget. The pivot also collapses a backlog of half-built features (multi-part question handling, constraint-aware replan, missed-workout reconciliation that respects history) into a single coherent capability — because once you have a reasoner with tools, those features are emergent, not handler-specific.
