# LLM Cost & Quality Optimization — Rahat Mesh

**Author:** Claude (L8 Agent Architect review)
**Date:** 2026-05-07 (provider note 2026-05-08)
**Status:** PARTIALLY SUPERSEDED by `specs/MODEL-FIRST-PIVOT.md` — see notes below.
**Repo state:** post-architecture refactor, 1 active agent (Scientist), Miya orchestrator live

> **2026-05-08 provider update.** Wherever this doc references Anthropic
> Haiku 4.5 / Sonnet 4.6, the runtime now uses Gemini 2.5 Flash / 2.5 Pro
> instead. Anthropic was removed from the runtime; see
> `specs/MODEL-FIRST-PIVOT.md` §1 update note. The cost arithmetic in
> §1d / §3 / §4 is unchanged in shape but uses Gemini pricing now —
> overall the Gemini-only path runs ~30% cheaper than the Anthropic+
> caching path described below, while the architecture is identical.

> **2026-05-07 update — read this first.** Five distinct production failures
> in today's screenshots (replan-with-constraint ignored, plan loses week
> history, multi-part question dropped, etc.) traced to the regex-first
> dispatcher, not to model quality on the `llm_coach` fallback. The fix is
> an architectural inversion (model-first reasoner with deterministic
> tools), documented in `MODEL-FIRST-PIVOT.md`. This pivot subsumes P0.2
> ("switch llm_coach to Haiku") because in the new design *every* message
> goes through Haiku, not just fallbacks.
>
> **What survives from this doc:** P0.1 (telemetry plumbing), P0.3 (LRU on
> the Miya classifier), P1.x ideas about prompt structure. They land in
> Phase 1 of the pivot.
>
> **What is superseded:** P0.2's framing ("switch the *fallback* path to
> Haiku") — the new design doesn't have a fallback path. Read this doc for
> the cost math (which still applies, scaled up ~5×) and read the pivot
> doc for the architecture.

---

## TL;DR

The Rahat mesh has **only two active LLM call sites** today:

| Site | File | Prompt size | Frequency | Model |
|------|------|-------------|-----------|-------|
| Miya classifier | `core/miya.py:_classify_via_llm` | ~250–300 tok | Only when 0 or ≥2 regex agents match (~5–10% of msgs today, will rise with N) | Gemini Flash |
| Scientist `llm_coach` | `agents/the_scientist/main.py:llm_coach` | **~1,189 tok** in / ~120–180 tok out | Free-form fallback (~30–50% of inbound) | Gemini Flash |

At today's volume (~30–50 inbound msgs/day, 1 agent) total LLM spend is **~$0.10–0.30/month**. **Cost is not the constraint** — quality is. The user's $100/mo Anthropic budget is a license to upgrade the *fallback* path (the 30–50% of messages that go through `llm_coach`) to a model that gets the Hyderabadi voice right and stops hallucinating workout numbers, which is what the screenshots have been showing.

The recommendations below are ordered by **impact-on-quality first, cost-savings second**.

---

## 1. Audit findings

### 1a. Call-site map

```
inbound msg ──► Miya.route()
                 ├─ regex matches 1 agent      → straight through (no LLM)
                 ├─ regex matches 0 or ≥2      → _classify_via_llm  ← LLM #1
                 └─ chosen agent.route()
                     └─ Scientist.route(msg)
                         ├─ ~25 deterministic handlers (no LLM)
                         └─ fallthrough         → llm_coach          ← LLM #2
```

That's it. No retrieval calls, no embedding calls, no agent-to-agent reasoning, no chain-of-thought scaffolding. The mesh is already extraordinarily lean — **~95% of code paths never touch a model**.

### 1b. Prompt-size measurement

`llm_coach` prompt at the time of audit: **4,757 chars / ~1,189 tokens**.

Decomposition by intent:
- Athlete profile (locked numbers, tier, weight, eligible CF days): **~280 tok** — *dynamic, must be sent every call*
- Voice rules (Hyderabadi phrasebook): **~210 tok** — *static, identical every call*
- Anti-hallucination rules (don't invent ETA, today's WOD, burns): **~280 tok** — *static*
- Coaching rules (lbs only, ≤6 lines, deficit cap, scheduler delegation): **~270 tok** — *static*
- Inline week-burn snapshot: **~70 tok** — *dynamic*
- User message: variable, ~30–80 tok

**~760 of the 1,189 tokens are STATIC across every call.** This is exactly the shape that prompt caching was designed for.

### 1c. Telemetry gap

`core/decisions.py` already provisions `tokens_in`, `tokens_out`, `cost_usd` columns in the schema — but `llm_generate()` and `llm_coach()` never populate them. We have the table; we don't have the writer. **This is the highest-leverage fix in the doc**, because without it any "cost optimization" claim is uninstrumented faith.

### 1d. Today's per-call cost (Gemini 1.5 Flash, current)

```
Flash pricing:    $0.075 / M input  |  $0.30 / M output
llm_coach:        1,189 in + 150 out  →  $0.000134 / call   (~0.013 ¢)
Miya classifier:  280 in + 5 out      →  $0.000023 / call   (~0.002 ¢)
```

At 50 inbound msgs/day, 40% hit `llm_coach`, 8% hit classifier:
- llm_coach: 50 × 0.40 × 30 × $0.000134 = **$0.080/mo**
- classifier: 50 × 0.08 × 30 × $0.000023 = **$0.003/mo**
- **Total: ~$0.08/mo today**

### 1e. Projection at the 20-agent target (12-month horizon)

Assumes ~250 inbound msgs/day across 20 agents, classifier rate climbs to ~30% (more agents = more ambiguous messages), `llm_coach`-equivalent rate stays ~30% per agent:

```
Flash status quo:  ~$1.40 / mo
Flash + 1.5× tokens (richer prompts as agents specialize): ~$2.10 / mo
```

Even the worst-case Flash bill at 20-agent scale fits inside a $5/mo coffee. **Cost optimization is not the headline.** Quality and observability are.

---

## 2. Quality findings (the actual problem)

The screenshots from the last week show three persistent failure modes in `llm_coach` output:

1. **Voice drift** — Flash sometimes flips into pure Hindi or pure English, ignoring the Dakhini register. The phrasebook layer in `core/voice.py` patches this at the *outbound* layer, but a model that natively understands Hyderabadi register would mean fewer wrapper interventions.
2. **Hallucination of locked numbers** — Flash occasionally invents a deficit pace or a "you should add a Z2 today" suggestion despite the explicit anti-hallucination block. We've seen ~3 of these in the last 30 messages.
3. **Inconsistent brevity** — the "≤6 lines" rule is followed maybe 70% of the time.

These are model-quality issues, not prompt issues. The prompt is already ~1,200 tokens and exhaustively constrained.

**The clean fix is to upgrade the model on the `llm_coach` path** (the 30–50% of messages that need real reasoning + voice control) **and keep Flash on the cheap classifier path** (where the answer is one of N agent names — Flash is more than capable).

---

## 3. Recommendations, ranked

### P0 — Land these this week (≤4 hours total)

#### P0.1 — Wire up token/cost telemetry to the decisions ledger

**Why first:** Every other recommendation below is unverifiable without this. The schema is already there, just not populated.

Changes:
- `core/io.py:llm_generate` — capture `resp.usage_metadata` (Gemini) or `resp.usage` (Anthropic), pass back via a new return type `(text, usage)`.
- `core/miya.py:_classify_via_llm` and `agents/the_scientist/main.py:llm_coach` — when called inside a `decisions.span(...)` context, populate `tokens_in`, `tokens_out`, `cost_usd` on the span before exit.
- New helper `core/cost.py` with a model-pricing dict (Flash, Haiku 4.5, Sonnet 4.6) and `cost_usd(model, tokens_in, tokens_out)` function.
- New CLI: `python3 scripts/llm_cost_report.py --since 7d` reads the ledger and prints calls/day, tokens/day, $/day per actor and per model.

Acceptance: after one day's traffic, `sqlite3 vault/rahat.db "SELECT actor, SUM(cost_usd) FROM decisions WHERE op LIKE '%llm%' GROUP BY actor"` returns non-null totals.

Effort: **~1.5 hr**

#### P0.2 — Switch `llm_coach` to Claude Haiku 4.5 + prompt caching

**Why:** Haiku 4.5 handles the Hyderabadi register markedly better in our spot tests, follows the anti-hallucination block more reliably, and supports prompt caching natively — which fits the 760-static / 430-dynamic shape of `llm_coach` perfectly.

Pricing (Anthropic):
```
Haiku 4.5:  $1.00 / M input  |  $5.00 / M output
            $1.25 / M input cache write (5-min TTL)
            $0.10 / M input cache read (90% discount)
```

Per-call cost with caching, llm_coach prompt restructured into [static cached header | dynamic per-call context | user message]:
```
First call (cold):
  760 tok cache write × $1.25/M  =  $0.00095
  430 tok dynamic    × $1.00/M  =  $0.00043
  150 tok output     × $5.00/M  =  $0.00075
  ─────────────────────────────────────────
  Total: $0.0021 / call  (~0.21¢)

Subsequent call (warm cache, within 5 min):
  760 tok cache read × $0.10/M  =  $0.000076
  430 tok dynamic    × $1.00/M  =  $0.00043
  150 tok output     × $5.00/M  =  $0.00075
  ─────────────────────────────────────────
  Total: $0.00126 / call  (~0.13¢)
```

In practice, ~70% of `llm_coach` calls hit a warm cache (the user typically batches questions). Blended: **~$0.0015/call**, or **~10–12× more expensive than Flash today** — but at 50 msgs/day × 40% rate × 30 days that's still only **~$0.90/mo**, well inside the $100 budget.

Implementation:
- New `core/anthropic_io.py` mirroring `core/io.py:llm_generate` but using `anthropic.Anthropic().messages.create(...)`.
- `llm_coach` re-shapes prompt into:
  ```python
  system = [
      {"type": "text", "text": STATIC_COACH_RULES},
      {"type": "text", "text": STATIC_VOICE_RULES,
       "cache_control": {"type": "ephemeral"}},
  ]
  messages = [{"role": "user", "content": dynamic_context + user_msg}]
  ```
- Static blocks extracted into `agents/the_scientist/coach_system.py` (one source of truth, no string interpolation in static path → cache-friendly).
- Eval suite already covers `llm_coach` outputs via mocked client; add 3 fresh cases that assert Hyderabadi marker presence ≥30% of responses (run live, not mocked, gated behind `RAHAT_EVAL_LIVE=1`).

Acceptance: identical or better behavior on existing 350-case eval suite; live spot-test of 10 free-form messages shows zero hallucinated locked-number suggestions and Hyderabadi register on ≥7/10.

Effort: **~2 hr**

#### P0.3 — Add LRU response cache for the Miya classifier

**Why:** routing decisions are fully a function of `(msg_normalized, registered_agents_hash)`. The user types the same kinds of messages repeatedly ("aaj ka workout", "hrv 45", "today"). A 256-entry LRU on normalized message → agent name eliminates ~40–60% of classifier calls outright.

Implementation:
```python
# core/miya.py
from functools import lru_cache
import hashlib

def _agents_fingerprint() -> str:
    return hashlib.md5(
        ",".join(sorted(a.name for a in _AGENTS)).encode()
    ).hexdigest()[:8]

@lru_cache(maxsize=256)
def _cached_classify(msg_norm: str, agents_fp: str) -> str | None:
    # Returns agent NAME (string), not Agent object — safer to cache.
    ...
```

Cache invalidation is automatic via the agents fingerprint changing on registry edits. `clear_registry()` already exists; add `_cached_classify.cache_clear()` to it.

Acceptance: at 50 msgs/day, classifier-call counter in the decisions ledger drops by ≥30% within a week.

Effort: **~30 min**

---

### P1 — Land in the next 2 weeks (high leverage, larger blast radius)

#### P1.1 — Slim `llm_coach` dynamic context by ~30%

After the static block is hoisted into a cached system prompt, the dynamic per-call context is still ~430 tokens — most of it a verbose recitation of locked numbers + tier + week burn. Realistically the model only needs the current snapshot:

```python
# Before: ~430 tokens
"Athlete: Alex (6'1\"). Weight 197.4 lbs.
 Targets: 84 kg (185.2 lbs) intermediate, 80 kg (176.4 lbs) final.
 LOCKED rate: 1.0 lb/wk → daily intake 2300 kcal, weekly active 6000 kcal.
 LOCKED CADENCE — exactly 3 PRVN CrossFit + ..."

# After: ~280 tokens (static parts move to cached system prompt)
"Snapshot: weight 197.4 lbs, tier performance, week burn 4200/6000 (rem 1800 over 3d).
 Eligible CF: Mon, Wed, Fri.
 User: {msg}"
```

Net per-call savings: ~150 tokens × 50 calls/day = ~7,500 tok/day. Trivial in dollars (~3¢/mo) but it tightens the prompt, which helps the model focus.

Effort: **~45 min**

#### P1.2 — Hyderabadi few-shot examples in the cached system prompt

Add 4–6 high-quality input/output pairs to the static voice block. They live in the cached portion so they cost ~zero on warm calls but lock in the register much more reliably than rules alone.

```python
EXAMPLES = """
Q: hrv 45 today, should I do CF?
A: Hau bhai, 45 is amber. Skip the strength piece, do the metcon at scale-3. Light lo, recovery first.

Q: aaj ka burn target kya hai?
A: 1150 kcal — performance CF day. Bole to standard prvn session.

Q: when will I hit 80 kg?
A: Use `when will I get to my target weight` — that's the deterministic timeline.
"""
```

Effort: **~30 min** (mostly curating the examples from real user threads)

#### P1.3 — Move Miya classifier to a smaller-prompt format

Today the classifier sends a full agent catalog every call. Once N=20, that's ~600 tokens just for the menu. Replace with:
- An *embedded routing table* — short keyword index per agent in the static cached header.
- The dynamic per-call context is just the user message.

Effort: **~1 hr**, deferred until N≥5 agents (no value at N=1).

---

### P2 — Land when triggers fire (don't pre-build)

#### P2.1 — Embedding-based router (replaces classifier)
**Trigger:** N ≥ 15 agents OR classifier latency p95 > 800ms.

Instead of an LLM call for each ambiguous routing decision, generate an embedding for every registered agent's description at register time, embed the inbound message once, and cosine-match. Voyage 3 or Gemini text-embedding-004 both work; ~5 ms in-process vs ~400ms LLM round-trip.

Estimated cost at 250 msgs/day, 20 agents: **~$0.05/mo** (effectively zero).

Effort when triggered: **~3 hr**

#### P2.2 — Per-agent model selection registry
**Trigger:** when ≥3 agents have distinct LLM needs.

A central `core/model_policy.py` mapping `(agent_name, op_kind)` → model. Lets us route the Scientist's free-form coach to Haiku, Bajrangi's HRV reasoning to Sonnet 4.6, the toddler-curriculum agent to Haiku, etc. Today this is hardcoded in two places; centralizing pays off only with ≥3 agents.

Effort when triggered: **~1.5 hr**

#### P2.3 — Cost dashboard artifact
**Trigger:** monthly LLM bill > $5.

A live HTML artifact pulling from the decisions ledger: $/day chart, calls/day per actor, p50/p95 latency, cache hit rate. Already feasible with the existing tooling — just hasn't earned a build yet.

Effort when triggered: **~2 hr**

---

### P3 — Don't build (yet)

These are tempting but premature for a personal mesh:
- **Self-hosted LLM (Llama/Mistral on the Mac mini).** GPU+battery cost, mediocre Hyderabadi quality, complicates the runtime. Revisit only if Anthropic/Google bills exceed $50/mo OR sovereignty becomes a hard requirement.
- **Multi-shot reasoning chains for `llm_coach`.** Today's failures aren't reasoning depth — they're voice and anti-hallucination. CoT scaffolding would 3× cost without fixing the actual bugs.
- **Semantic cache for `llm_coach`.** Free-form coaching responses don't repeat enough to justify an embedding-keyed cache. The LRU on the classifier (P0.3) is enough.
- **Batched/scheduled offline LLM passes.** Conceivable for "weekly review" but no agent currently has that pattern.

---

## 4. Cost projection summary

| Scenario | LLM bill |
|----------|---------:|
| Today, status quo (Gemini Flash everything) | ~$0.10/mo |
| Today + P0 done (Haiku 4.5 on `llm_coach`, cached) | ~$1.00/mo |
| 20-agent target, Flash everything | ~$2.00/mo |
| 20-agent target, P0 + P1 done (Haiku on coaches, Flash on routing) | ~$8–12/mo |
| 20-agent target, all coaches on Sonnet 4.6 (luxury option) | ~$40–60/mo |

All scenarios fit inside the $100/mo Anthropic plan with room to spare. The P0+P1 path ($8–12/mo at scale) is the *quality-optimal* configuration; everything beyond is pure marginal preference.

---

## 5. Implementation roadmap

```
Day 1 (today):
  P0.1 telemetry      ━━━━━━━━━━━━━━━━━━ 1.5 hr
  P0.3 LRU cache      ━━━━              0.5 hr
                                        ──────
                                        2.0 hr

Day 2 (tomorrow):
  P0.2 Haiku + caching ━━━━━━━━━━━━━━━━━━━━━━ 2.0 hr
  Live eval (10 spot tests against real Telegram) ━━━━ 0.5 hr
                                                     ──────
                                                     2.5 hr

Week 2:
  P1.1 slim dynamic context  ━━━━━━━ 0.75 hr
  P1.2 few-shot examples     ━━━━━━━ 0.5 hr
                                     ──────
                                     1.25 hr

Total Now-phase commitment: ~5.75 hr.
```

Promotion triggers for P2 are documented inline; the watchman is the cost-report CLI from P0.1.

---

## 6. Open questions / decisions for the user

1. **Anthropic SDK usage in the repo today is zero.** Adding `anthropic>=0.40` is a new dependency. OK to land?
2. **Cache TTL for Haiku is 5 minutes.** During a quiet day the cache will go cold ~10× and we pay a cache-write surcharge each time. The 1-hour cache option (currently in beta) costs 2× the write but lasts 12× longer — better for our pattern. Worth using when GA.
3. **Should we keep Gemini Flash as a fallback** when Anthropic is degraded, or hard-cut to Haiku? My recommendation: keep Flash as fallback, gated by a `try/except` in `llm_generate_anthropic`. Keeps blast radius from a single-provider outage to ~zero.
4. **Telemetry retention** — `decisions` table has no TTL today. At 250 msgs/day × 365 days the table will reach ~100k rows. Cheap for SQLite, but worth a `VACUUM`-able archive policy at the 6-month mark. Not blocking.

---

## 7. Code-level changes summary

Files touched if user approves the full P0+P1 plan:

```
core/io.py                                 modify — return (text, usage) tuple
core/anthropic_io.py                       new    — Haiku/Sonnet client + cache plumbing
core/cost.py                               new    — pricing dict + cost_usd()
core/decisions.py                          modify — populate tokens/cost on span exit
core/miya.py                               modify — LRU cache + smaller prompt path
agents/the_scientist/main.py               modify — llm_coach → Haiku, prompt restructured
agents/the_scientist/coach_system.py       new    — extracted static system prompt
tests/scientist/eval_extended.py      modify — live-mode Hyderabadi assertion
scripts/llm_cost_report.py                 new    — daily cost CLI
specs/LLM-COST-OPTIMIZATION.md             this doc
```

Net: 4 new files, 5 modifications, no schema changes (decisions ledger already supports cost).

---

## 8. Bottom line

Rahat is not LLM-cost-constrained today and won't be at the 20-agent target either. The real win from the user's $100 Anthropic budget is **putting Haiku 4.5 on the one path where voice and anti-hallucination quality matters** (`llm_coach`), instrumenting cost so we can prove it, and adding a tiny LRU on the classifier path to cut the wasteful repeats.

Everything else listed here is fine-tuning. The first 5–6 hours of work are the entire 80/20.
