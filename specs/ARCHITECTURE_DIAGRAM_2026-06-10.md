# Rahat Architecture — 2026-06-10 (post-cutover view)

This diagram reflects the system AFTER the Phase E cutover from old
Miya to new Miya v2. Old plane handlers (Kobe, Fraser) are still the
source of truth for domain logic; new plane owns routing + voice.

---

## Top-level data flow

```
┌──────────────────────────────────────────────────────────────────┐
│                       Telegram (user)                            │
└──────────────────────────────┬───────────────────────────────────┘
                               │ chat_id, text
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│              new_plane/miya_runner (the runner)                  │
│                                                                  │
│  __main__.py — poll loop, parses each Telegram update,           │
│  builds a Turn(user_message, chat_id, trace_id), calls           │
│  orchestrator.handle(turn).                                      │
└──────────────────────────────┬───────────────────────────────────┘
                               │ Turn
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│          new_plane/miya_runner/orchestrator.handle()             │
│                                                                  │
│   Step 1: chat_memory.append (user turn)                         │
│   Step 2: classify_delegation(msg) →                             │
│           ─ kobe_route     → native_client.kobe_route(...)       │
│           ─ fraser_route   → native_client.fraser_route(...)     │
│           ─ huberman_route → native_client.huberman_route(...)   │
│           ─ orchestrate    → continue below                      │
│                                                                  │
│   Step 3: classify_intent(msg) (orchestrate path only)           │
│   Step 4: pull facts (active_goal, recalibration, gym_wod,       │
│           fraser_design) per intent                              │
│   Step 5: arbitrate(facts) → verdict (behind_pace / goal_close)  │
│   Step 6: charter.check(kind, ctx)                               │
│           kind derived from intent (notify.user.reply,           │
│             coach.push_intensity, fraser.workout.commit, ...)    │
│           ctx includes hrv_state (P0-3 fix 2026-06-10)           │
│   Step 7: VERBATIM WOD BYPASS (P0-1 fix 2026-06-10)              │
│           If intent=workout_lookup AND gym_wod has text AND      │
│           charter allows → return Kobe text wrapped, SKIP synth  │
│   Step 8: cost_router.decide → flash vs pro                      │
│   Step 9: synthesizer.synthesize(intent=<scoped>) → Gemini       │
│   Step 10: publish signal (agent=miya), record chat_memory       │
│   Step 11: emit Response(text, sent=True, ...)                   │
└─────┬───────────────────────────────────────────────┬────────────┘
      │ kobe_route / huberman_route                   │ orchestrate path
      ▼                                               ▼
┌──────────────────────────────────────┐  ┌────────────────────────┐
│ agents/the_scientist/handler.route() │  │ new_plane synthesizer  │
│ (the old plane Kobe — still the      │  │ (Gemini Flash / Pro)   │
│ source of truth for domain logic)    │  └────────────────────────┘
│                                      │
│   - 30+ handle_* functions           │
│   - core/dispatcher.py (20 routes)   │
│   - charter integration              │
│   - decisions ledger writes          │
│   - mesh delegation to Huberman      │
└──────────────────────────────────────┘
      │ fraser_route
      ▼
┌──────────────────────────────────────┐
│ agents/fraser/handler.route() +      │
│ composer.design_workout              │
│                                      │
│   - workout cards                    │
│   - 1RM grounding                    │
│   - pain blocks, mobility            │
│   - source-of-truth WOD parser       │
└──────────────────────────────────────┘
```

---

## Storage layers

```
┌──────────────────────────────────────────────────────────────────┐
│ vault/rahat.db (core.decisions, ledger)                          │
│   - One row per Turn (old plane writes; new plane mirrors when   │
│     NEW_MIYA_USE_LIVE_DB=1)                                      │
│   - Source for eval suite, replay regression, analytics          │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ ~/.rahat/new_plane_signals.db (new_plane.signals.store)          │
│   - Cross-agent signal bus                                       │
│   - PF-006 (2026-06-10): chat_id column scopes per chat          │
│   - PF-005 (2026-06-10): recent() called with agent= filter      │
│   - P1-2 (2026-06-10): pending_clarification rows (60s TTL)      │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ ~/.rahat/cost_router.log                                         │
│   - One JSONL line per synth-router decision                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Cross-cutting concerns

| Concern | Old plane (was) | New plane (now) |
|---|---|---|
| Routing brain | core.miya + core.dispatcher | new_plane.miya_runner.delegate_classifier |
| Voice / synthesis | Kobe / Fraser per-agent + LLM coach fallback | new_plane.miya_runner.synthesizer (Gemini) |
| Charter | Applied once per WorkOrder (kind-specific) | P0-3 fix 2026-06-10: kind now derived from intent |
| Decisions ledger | core.decisions.log() | Same — new plane mirrors |
| Signal bus | n/a (Kobe-local state) | new_plane.signals.store, scoped per agent + chat |
| Chat memory | core.chat_memory (flag-gated) | new_plane uses the same module via bridge |
| Nudges | Kobe `maybe_*` functions | P0-2 fix 2026-06-10: new plane runs them by default |
| Cost routing | n/a (always Flash) | new_plane.miya_runner.cost_router (Flash/Pro) |

---

## What lives where after cutover

| Surface | Module |
|---|---|
| Telegram poll loop | new_plane/miya_runner/__main__.py |
| Routing decision | new_plane/miya_runner/delegate_classifier.py |
| Intent classifier | new_plane/miya_sim/orchestrator.py (classify_intent) |
| Adapter (in-process) | new_plane/miya_runner/native_client.py |
| Adapter (HTTP fallback) | new_plane/miya_runner/adapter_client.py |
| Synth prompt builder | new_plane/miya_runner/synthesizer.py |
| Cost router | new_plane/miya_runner/cost_router.py |
| Signal store | new_plane/signals/store.py |
| Pending clarifications | new_plane/miya_runner/pending.py |
| Charter policies | core/charter.py (unchanged) |
| Kobe domain | agents/the_scientist/* (unchanged) |
| Fraser domain | agents/fraser/* (unchanged) |
| Huberman domain | agents/huberman/* (skeleton; via Kobe mesh) |

---

## What's deprecated after cutover

| Module | Status | Notes |
|---|---|---|
| `core/miya.py` | DEPRECATED | Old orchestrator. Kept for reference + tests for now. |
| `agents/bajrangi/` | DEPRECATED | Old Miya peer; mostly dead. |
| `agents/the_scientist/handler.maybe_*` nudges | DEPRECATED-as-driver | Still imported by new plane runner; the FUNCTIONS run, but they're now driven by the new plane's tick loop, not the old plane's. |
| `bridges/openclaw_adapters/server.py` | OPTIONAL | HTTP fallback path. Default new plane uses native_client. |

---

## The 4 paths after cutover

| Path | When fires | What runs |
|---|---|---|
| **kobe_route** | slash, plan mutation, weight/HRV/burn log, status query, pain/profile, recovery protocol, `@kobe`, skip/cancel/move/postpone | `agents.the_scientist.handler.route()`, no synth |
| **fraser_route** | `@fraser` explicit address | `agents.fraser.handler.route()`, no synth |
| **huberman_route** | `@huberman` explicit address | `agents.the_scientist.handler.route(@huberman ...)` (mesh-delegates) |
| **orchestrate** | open coaching, `@miya`, WOD lookup, design intent | full pipeline: facts → arbitrate → charter → synth (or verbatim WOD bypass) |

---

## Reading order for new contributors

1. `specs/ADR-013_migrate_to_new_plane.md` — why we cut over
2. This file — current shape
3. `new_plane/miya_runner/orchestrator.py` — the actual handle()
4. `new_plane/miya_runner/delegate_classifier.py` — routing brain
5. `agents/the_scientist/handler.py` — domain logic (Kobe)
6. `specs/test_lead/findings/GAP_MATRIX.md` — what's still
   different vs the old bot, ranked by priority.
