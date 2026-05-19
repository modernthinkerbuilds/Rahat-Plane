# ADR-009 — Single ordered dispatcher (Option C)

**Status:** Proposed (2026-05-19)
**Author:** Chief Architect (post-2026-05-18 production incident review)
**Supersedes:** Partial routing logic in ADR-006, ADR-007, ADR-008

---

## Context

Between 2026-05-16 and 2026-05-18, the production bot accumulated **10 distinct
routing layers**. Each layer could intercept a message before it reached its
correct handler:

1. Miya capability classifier (LLM call, ~700ms)
2. Miya Tier-1 slash bypass (string check)
3. Miya trigger fallback (regex)
4. Miya clarification policy (substrate state)
5. Kobe `_try_slash_command` (slash dispatcher)
6. Kobe `_should_delegate` (regex → Fraser/Huberman)
7. Kobe `reasoner.reason()` (LLM with tool catalog of 8+ tools)
8. Kobe `_legacy_route` (15+ regex matchers — only runs after reasoner fails)
9. Fraser `_should_delegate` (regex → delegate back to Kobe)
10. Fraser `design_workout` (snapshot stub for unrecognized intents)

In 48 hours, we shipped P0 fixes for **7 of these 10 layers** routing the same
class of query incorrectly. The pattern is clear: every layer is a decision
point, every layer can be wrong, and the wrong layer winning is the bug.

The most expensive symptom: the user spent 15+ hours trying to get Fraser
working, only to discover Fraser's `design_workout` is a stub. Every "Fraser"
response in production was either a context-snapshot echo or an LLM
hallucination wrapped in Kobe's voice. Fraser delivered zero unique value.

## Decision

Replace the 10-layer cake with **one ordered dispatch table** owned by a new
`core/dispatcher.py` module. First match wins. The LLM reasoner becomes a
last-resort fallback for open-ended chat only — it never sees a factual query
that has a deterministic handler.

### The new flow

```
Telegram inbound → Miya.route(msg)
  ↓
  core.dispatcher.dispatch(msg)        ← ONE ordered regex table
  ├── match? call handler directly, return result
  └── no match? → return None
  ↓ (only if no match)
  reasoner.reason(msg)                 ← open-ended fallback ONLY
```

### What gets dispatched deterministically

Every **factual** query gets a regex entry in the dispatch table. Examples:

| Pattern | Handler | Owner |
|---|---|---|
| `^\s*/` (slash) | `_try_slash_command` | kobe |
| `\bwhat is the WOD for <weekday>\b` | `handle_gym_wod_on(idx)` | kobe |
| `\bweight[:\s]+\d+` | `handle_weight(val)` | kobe |
| `\bhrv\s+\d+` | `handle_hrv(val)` | kobe |
| `\btier\s+<color>` | `handle_set_tier(t)` | kobe |
| `\b(pace|on track|status)\b` | `handle_pace()` | kobe |
| `\bwhat is my plan\b` | `handle_show_plan()` | kobe |
| `\bdesign\s+(?:a\s+)?workout\b` | `Fraser.design_workout(...)` | fraser |
| ... | ... | ... |

Roughly **20-30 routes** cover ~95% of real traffic, based on mining
`vault/rahat.db`'s decisions.input_json for the past 30 days.

### What's still allowed to use the reasoner

Open-ended queries with no deterministic match: coaching conversation,
motivational chat, free-form Q&A about training philosophy, etc. The LLM is
the right tool for these; the regex isn't.

### What gets retired

- `Miya.classify_intent()` — replaced by the dispatcher
- `_apply_confidence_policy` — no more multi-dispatch, no more clarification
  flows for factual queries
- `Kobe._should_delegate()` — replaced by Fraser routes registered directly
  in the dispatcher
- `Fraser._should_delegate()` — same; Fraser only owns its own design routes
- `_legacy_route()` — its regex matchers move INTO the dispatcher; the
  function itself goes away
- `agent.triggers` field — no longer consulted

### Feature flag for safe rollout

`RAHAT_USE_DISPATCHER=1` (default) enables the new path. Setting to `0` falls
back to the legacy 10-layer flow. This lets us ship and roll back without
re-merging.

## Consequences

### Wins

- **Every query has ONE place where its routing is decided.** Bugs become
  localized.
- **No LLM in the routing path for factual queries.** The 2026-05-18 incident
  (reasoner hallucinated "Fraser says strength_only" instead of calling
  `get_gym_wod_on`) becomes architecturally impossible.
- **Test coverage becomes finite.** Every entry is one regex test plus one
  end-to-end test. No more "which of 10 layers will fire for this phrasing?"
- **Latency drops.** No LLM classifier call per message (~700ms → ~5ms for
  factual queries).
- **Fraser's identity gets clarified.** Either Fraser owns generative design
  routes explicitly, or Fraser is deleted (separate ADR-010 to follow).

### Costs

- **One-shot refactor risk.** Mitigated by the feature flag.
- **Less LLM-driven flexibility.** New phrasings need a regex entry, not just
  a description tweak. Acceptable trade — regex is cheap to add, LLM routing
  is expensive to debug.
- **Loses the multi-agent classifier ceremony.** Some queries that *could*
  have gone to Fraser will now go to Kobe by default. If Fraser starts
  delivering real value (ADR-010), we add Fraser routes back to the
  dispatcher.

### Migration path

1. **Phase 1 (this PR):** Build `core/dispatcher.py` with the 15-20 most
   common routes. Wire into Kobe's `route()` BEFORE existing delegate/reasoner
   path. Behind feature flag. **Reasoner stays available as fallback.**
2. **Phase 2 (next week):** Mine `vault/rahat.db` for every user message in
   the last 60 days. Add routes for any phrasing that doesn't match. Aim for
   95% deterministic-match rate.
3. **Phase 3 (after 2 weeks of green nightlies):** Retire `_legacy_route`,
   `_should_delegate`, `Miya.classify_intent`, `agent.triggers`. Flag becomes
   permanent default.
4. **Phase 4 (ADR-010):** Decide Fraser's fate. If Fraser is retained, give
   it real generative routes. If deleted, merge `design_workout` into Kobe.

## What this ADR does NOT change

- Slash commands still work the same way (just registered via the dispatcher
  instead of a special bypass).
- Reasoner still answers open-ended questions.
- All existing handlers (`handle_weight`, `handle_show_plan`, etc.) are
  unchanged. We just call them more directly.
- Test gates, regression registry, silent-failure guard all remain in place.

## Open questions

1. **Multi-language / Hindi phrasings.** Today's `_legacy_route` has
   `HINDI_AAJ_WORKOUT_RE`. Need to ensure these phrasings get dispatcher
   entries.
2. **Voice/style.** The reasoner currently wraps responses in Hindi-flavored
   coaching voice. Direct-dispatched handlers return plain text. Need a
   post-handler voice-layer pass for stylistic consistency, OR accept that
   factual answers are terser than open-ended ones (probably fine).
3. **Fraser's generative routes.** Until ADR-010, the dispatcher has zero
   Fraser routes. All "design me a workout" queries fall through to the
   reasoner, which can call Fraser's tools if needed. Document this.

## Decision

Adopt Option C. Ship behind feature flag. Mine real phrasings in Phase 2.
Retire the cake in Phase 3.
