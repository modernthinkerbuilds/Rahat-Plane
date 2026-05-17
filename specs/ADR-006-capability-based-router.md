# ADR-006 — Capability-based router (no hardcoded triggers)

**Date:** 2026-05-16  
**Status:** Accepted (Chief Architect, owner-approved 2026-05-16 evening)  
**Context:** The 2026-05-16 production screenshots showed every workout
question routing to Kobe (which hallucinated answers) while Fraser sat
registered but invisible. Diagnosis confirmed `FraserAgent.triggers=[]`
and `route()` returning `Reply(confidence=0.1)`. Kobe's 15-pattern
trigger list captured every fitness keyword. The owner's verdict:
"I don't want hardcoding — there are architectural gaps with how AI
agents should work. I want probabilistic scenarios to be addressed."
Reviewed in tandem with ADR-007 (cross-agent delegation) and ADR-008
(clarification policy). Together they replace regex-trigger routing
with LLM-classifier + capability-description routing.

## Decision

Miya routes via an LLM intent classifier that reads each agent's
**`description`** field (already declared on every Agent subclass)
and returns a confidence-ranked dispatch decision. Regex `triggers`
become a **fallback only** — used when the classifier is unavailable
(no API key, network error, test sandbox).

## Architecture — three layers

```
                         ┌───────────────────────┐
   user message  ───►    │   Miya.route(msg)    │
                         └──────────┬────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
       1. SLASH SHORTCUT     2. SEMANTIC CLASSIFIER    3. TRIGGER FALLBACK
         /pace /today …      LLM-driven, reads          Regex (legacy,
         (deterministic)     agent.description for       used only when
                             every registered agent      LLM unavailable)
                                    │
                                    ▼
                          dict[agent_name, confidence]
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              4. POLICY:      4. POLICY:       4. POLICY:
              top ≥0.7        top-2 within     all <0.4
              → dispatch       0.2, both ≥0.5  → clarify
              one              → dispatch       (ADR-008)
                              both, merge
```

## Classifier design

**Single prompt, one Gemini Flash call per message.** Prompt template:

```
You are the router for a personal AI mesh. Each agent below is a
specialist with a description of what they own. Given the user
message, return a JSON object mapping each agent name to a
confidence score [0.0, 1.0] indicating how well that agent's
domain matches the user's intent.

Agents:
- kobe: <description from KobeAgent.description>
- fraser: <description from FraserAgent.description>
- huberman: <description from HubermanAgent.description>
...

User message: "<msg>"

Reply with ONLY a JSON object. Example:
{"kobe": 0.15, "fraser": 0.85, "huberman": 0.05}
```

* Cost: ~$0.0001 per message on Flash. Caching the prompt prefix
  (system + agent catalog) drops it further; only the user message
  varies per call.
* Latency: ~200–400 ms. Acceptable for the routing layer.
* Determinism: temperature=0.0, structured output schema enforced.

**Loaded on Miya init, refreshed when `register()` adds a new agent.**

## Confidence policy (ADR-008 owns the thresholds)

| Top score | Behavior |
|---|---|
| ≥ 0.7 (clear winner) | Dispatch to that agent. |
| 0.5–0.7 AND second-place ≥0.5 AND within 0.2 | Dispatch both. Miya merges replies, attributes voices. |
| 0.4–0.5 single | Dispatch with a confidence caveat ("I'm not sure but…"). |
| All < 0.4 | Don't guess — ask the user a clarifying question per ADR-008. |

These are policy defaults. Each can be overridden via env vars
(`RAHAT_ROUTER_HIGH_CONF`, `_AMBIG_THRESHOLD`, `_LOW_CONF_FLOOR`).

## Why descriptions, not triggers

* **Triggers don't compose.** Every new phrasing needs a regex update.
  We watched this fail twice on Kobe alone (the "which days am I
  working out" bug, the "what is the WOD" bug).
* **Descriptions scale.** A new agent ships with one well-written
  paragraph — no enumeration of phrasings.
* **Descriptions are already maintained.** Every `Agent` subclass
  declares `description` because Miya already used it for
  `list_capabilities()`. We're load-bearing an existing field.
* **Classifier sees semantics, not surface forms.** "What's my WOD"
  and "give me today's prescription" and "what should I do at the gym"
  all route to Fraser because the classifier reads its description
  ("CrossFit/Z2 workout designer. Adapts gym programming…") and
  matches intent, not keywords.

## Required updates to agent descriptions

For the classifier to route correctly, every agent's description must
be SHARP about its territory. The Chief Architect briefs each
specialist thread with their description-rewrite directive.

### Kobe (current vs target)

| Field | Current | Target |
|---|---|---|
| `description` | "Vitality lead. Owns weight, HRV, weekly caloric targets, the 3 CF + 1 Z2 + active-rest cadence, weight-loss timeline math, and the Hyderabadi-direct coaching voice. Use for any question about calories, HRV, weight, weighing in, workout plan, schedule, training tier, breathing protocols, pre/post-workout fuel, or weekday-specific workout lookups." | "Vitality coach. Owns weight tracking, HRV interpretation, weekly caloric burn targets, weight-loss timeline math, recovery tier, breathing/cooldown/pre-fuel protocols. For SPECIFIC workout prescription (today's WOD, movement substitutions, weight calculations from 1RMs, predicted burn for a specific session), defer to Fraser." |

Note the explicit "defer to Fraser" boundary. Without it the classifier
keeps picking Kobe for everything fitness-shaped because Kobe's domain
overlaps Fraser's.

### Fraser (current vs target)

| Field | Current | Target |
|---|---|---|
| `description` | (Day-1 stub, likely thin) | "CrossFit + Zone-2 workout designer. Given today's SugarWOD programming and your 1RMs / HRV / dislikes / equipment / injuries, produces a fully adapted Workout Card with: warm-up, scaled WOD movements with calculated loads, predicted burn against Kobe's target, cool-down. Use for: 'what's my WOD', 'give me the workout', 'I want to do PRVN', 'make-up session', 'can I substitute X for Y', 'show me Friday's workout', 'scale this WOD', any workout-prescription question." |

## Implementation

* `core/miya.py` gets a new function `classify_intent(msg) -> dict[str, float]`
* Existing `route()` consumes the classifier output, applies the
  confidence policy, dispatches.
* Trigger-based `_matching_agents()` stays as **fallback only** (used
  when `cio.llm_client()` returns None or the call errors).
* New test file: `tests/test_capability_router.py` with these
  invariants pinned:
  * Classifier picks Fraser for "what's my WOD" and 5 paraphrases
  * Classifier picks Kobe for "what's my weight goal" and 5 paraphrases
  * Classifier picks both for "did I hit my weekly burn target?" (ambiguous)
  * Classifier abstains (all <0.4) for off-domain noise ("what's the weather")
  * Fallback to triggers when `llm_client()` returns None
  * `test_storage_convention` still green (no new tables)

## What stays the same

* `decisions` ledger actor strings — no change. Trace continuity preserved.
* Charter — name-agnostic, no policy edits.
* Substrate (`core/memory/*`) — no schema changes.
* Existing agent `triggers` declarations — kept as fallback, not deleted.
* Slash command dispatcher — runs BEFORE classifier (cheaper, deterministic).
* The semantic intent classifier already inside Kobe's handler (Phase 16
  work, "Go 2") — stays in place, operates at the *intent within Kobe*
  layer. ADR-006's classifier is at the *which agent* layer. Two
  classifiers, two scopes, not redundant.

## Retirement

After 2 weeks of green nightlies with classifier-routing as primary,
ADR-009 will retire each agent's `triggers` list (delete the field
on `Agent`, remove `_matching_agents()`, remove the fallback path).
That's a separate ADR + PR. Not in scope here.

## Rollback

Set `RAHAT_ROUTER_MODE=triggers` to disable the classifier and revert
to pre-ADR-006 behavior. The fallback path is the rollback path —
no separate revert PR needed.
