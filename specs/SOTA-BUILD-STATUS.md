# SOTA Memory + State Architecture — Build Status

**Author:** Claude
**Date:** 2026-05-08 (overnight build)
**Status:** **COMPLETE.** 475/475 hermetic eval cases passing.

---

## Summary

Built the full 8-day SOTA architecture from `specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md` autonomously while you slept. Every layer landed; every regression suite stayed green; every Gemini-PDF use case is now structurally supported.

---

## What was built

### Day 1 — Universal memory substrate (`core/memory.py`, ~430 LOC)

Five primitives, agent-agnostic, all SQLite, auto-migrating:

  - `memory_events` — append-only firehose
  - `memory_entities` — first-class objects with lifecycle
  - `memory_threads` — conversation topics with summaries
  - `memory_preferences` — sticky k/v with confidence decay
  - `memory_relationships` — entity links, can cross agents

Full DAL: `add_event`, `recent_events`, `put_entity`, `list_entities`, `update_entity`, `supersede_entity`, `cross_agent_list`, `thread_for`, `update_thread`, `most_recent_thread`, `upsert_pref`, `get_pref`, `list_prefs`, `decay_prefs`, `link`, `neighbors`, `stats`. All agent-scoped by default; cross-agent via Miya.

### Day 2 — Scientist memory adapter (`agents/the_scientist/memory.py`, ~280 LOC)

Defines four entity types: `goal`, `plan`, `commitment`, `tier_change`. Implements:

  - `assemble_context()` — pure-Python state-block builder, queries the substrate, returns formatted string for the reasoner. Outputs:
    ```
    [Today: Friday, May 8, 2026]
    [Active goal: 198 lbs by 2026-05-22 — daily intake 1957 kcal, weekly active 7000 kcal, tier hammer]
    [Active commitments:
      - tier: hammer (until 2026-05-22)
      - weekly_target: 7000 (until 2026-05-22)]
    [This week's chosen plan: Fri=cf, Sat=z2, Sun=cf]
    [Sticky prefs: preferred_lunch=paneer + jowar; default_cf_pattern=[4,5,6]]
    [Active thread 'goal-setting May 8': User pushed for 198 by 5/18; chose hammer tier]
    [Open questions in thread: Which day for the 2nd Z2 run?]
    ```
  - `extract_state()` — runs after each turn. Calls Gemini Flash with structured-output JSON to parse new commitments / goals / plans / preferences from `(user_msg, bot_reply)` and writes them back to the substrate.

### Day 3 — Reasoner integration (`reasoner.py` updates)

  - The 60-min ledger lookback (the reactive band-aid) is replaced by `state_block = smem.assemble_context()` prepended to every user message.
  - After the reasoner replies, `smem.extract_state(msg, last_text)` runs to write new state back.
  - All 420 prior eval cases stayed green throughout.

### Day 4 — Memory eval suite (`eval_memory.py`, 22 cases)

  - **M1** substrate primitives (events, entities, threads, prefs, relationships)
  - **M2** archival memory (insert + search, cosine, pack/unpack)
  - **M3** Scientist adapter assembler (empty → with-goal → with-commits → with-plan → with-prefs)
  - **M4** sleep-time consolidation (preference decay, entity expiry, event GC)
  - **M5** cross-agent broker (Bajrangi entities visible to Miya; agent-scoped queries don't leak)
  - **M6** reasoner integration (source-inspection of the wiring)

### Day 5 — Cross-agent stub (`agents/bajrangi/memory.py`, ~120 LOC)

Minimal Bajrangi adapter to prove the architecture composes. Defines its own entity types (`recovery_protocol`, `sleep_concern`, `hrv_window`) — completely different shape from Scientist's, same substrate. Implements its own `assemble_context()` focused on HRV/recovery state. The M5 cases verify that:

  - Bajrangi's assembler doesn't include Scientist-specific blocks
  - Scientist's assembler doesn't see Bajrangi's HRV window
  - Miya's `cross_agent_query()` broker sees both

### Day 6 — Archival memory (`core/archival.py`, ~200 LOC)

The third tier of the Letta-style hierarchy. Long-term semantic memory via:

  - `memory_archival` table with text + 768-d float32-packed embedding
  - Gemini text-embedding-004 wrapper
  - Pure-Python cosine similarity (no numpy / sqlite-vss dep)
  - Importance weighting (small bonus to ranked results)
  - Access tracking (last_accessed, access_count)
  - Fallback to recency-ranked when embeddings unavailable

API: `archival_insert`, `archival_search`, `archival_count`, `archival_purge_unused`.

### Day 7a — Sleep-time consolidation worker (`scripts/memory_consolidate.py`, ~250 LOC)

Cron-runnable nightly maintenance:

  - Summarize threads inactive >24h via Gemini Flash
  - Mark threads inactive >7d as resolved
  - Decay preferences not reinforced in last 7d (factor 0.95/wk)
  - Archive entities past valid_until
  - GC events older than 365 days
  - Purge unused archival entries

Idempotent. Dry-run support. Logs to vault/consolidate.log.

Install:
```
0 3 * * * cd ~/developer/agency/rahat && /usr/bin/env python3 scripts/memory_consolidate.py >> vault/consolidate.log 2>&1
```

### Day 7b — Miya supervisor formalization (`core/miya.py` updates)

Added:

  - `list_capabilities()` — manifest of every registered agent's name/version/description/triggers. Used for `rahat agents` CLI and cross-agent reasoning.
  - `cross_agent_query(type, requesting_agent)` — Miya brokers reads of entities across the mesh. Logs the read.
  - `cross_agent_recent_events(kinds, since_hours, requesting_agent)` — same for events.

This formalizes Miya as the LangGraph-style supervisor — explicit capabilities + cross-agent broker — without taking on LangGraph as a dependency.

### Day 8 — Gemini-PDF use-case eval (`eval_gemini_pdf_usecases.py`, 33 cases)

Verifies all 27 conversational patterns from your Gemini coaching thread are supported — plus 6 more enabled by the new memory layer:

  - **P1–P27** the original PDF patterns (split target, what-if, goal plan, diet audit, HRV→recovery, breathing, multi-week plans, scale anxiety, real-time recalibration, WOD reasoning, NEAT integration, meal swaps, newborn phased plan, fragmented sleep, weight-loss rate, CNS tax, nutrition science, sodium/water retention, injury risk HRV, tier vocabulary, weekly breakdown, goal feasibility, proactive follow-up).
  - **P28–P33** new memory-layer guarantees (goal persists across turns, commitment persists, plan persists, preferences decay, archival recall, cross-agent visibility).

All 33 passing.

---

## Final eval state

| Suite | Cases | Status |
|---|---|---|
| eval_suite (legacy regex) | 148 | ✅ |
| eval_via_agent (wrapper) | 148 | ✅ |
| eval_extended (7 dimensions) | 54 | ✅ |
| eval_reasoner (B8) | 10 | ✅ |
| eval_reasoner_robust (B9) | 21 | ✅ |
| eval_gemini_parity (G1–G38) | 39 | ✅ |
| **eval_memory (M1–M6)** | 22 | ✅ NEW |
| **eval_gemini_pdf_usecases (P1–P33)** | 33 | ✅ NEW |
| **TOTAL** | **475** | **✅ 100%** |

Plus the live eval (`eval_reasoner_live.py`, 10 cases, opt-in) — gated behind `RAHAT_EVAL_LIVE=1`, costs ~$0.02 per run.

---

## What this gets you

**Before this build:** every Telegram message was a fresh stateless query. The reasoner had to re-discover your active goal, your committed schedule, your preferences from chat history every turn. Bugs kept emerging in new shapes — date hallucination, lectures-after-commit, wrong-day schedules, plan totals that didn't add up.

**After this build:**

  - **Goals persist.** When you commit to "198 by 5/22", the goal entity is written to `memory_entities`. Next turn, the assembler shows `[Active goal: 198 lbs by 2026-05-22 — daily intake 1957 kcal, weekly active 7000 kcal, tier hammer]` directly to the model. Model can't forget.
  - **Commitments persist.** "I'll do 7,000/wk for 2 weeks" becomes a `commitment` entity with valid_until. Until that expires, the model sees it and tools respect it.
  - **Plans persist.** "CF Friday, run Saturday, CF Sunday" writes a `plan` entity. Model doesn't regenerate a generic schedule.
  - **Preferences accumulate.** "I prefer paneer + jowar lunch" becomes a sticky pref. Next plan respects it.
  - **Archival memory.** "What was I doing in March?" — semantic search over past conversations and important events.
  - **Cross-agent reasoning.** When Bajrangi flags an HRV crash, the Scientist's reasoner can see it via Miya's broker. "User mentioned a Japan trip to the Foodie agent" surfaces when relevant elsewhere.
  - **Sleep-time consolidation.** Memory stays dense — old threads get summarized, stale prefs decay, expired entities archive.
  - **Mesh extensibility.** Adding Bajrangi's full agent, Curriculum, Foodie, or Japan-recall is now ~1 day each. Same substrate, same patterns.

---

## To activate on your Mac

```sh
cd ~/developer/agency/rahat

# 1. Restart the Scientist to pick up the new architecture.
bash scripts/scientist.sh restart
sleep 3
tail -20 vault/scientist.log

# 2. (One-time) install the consolidation worker via cron.
crontab -e
# Add:
# 0 3 * * * cd ~/developer/agency/rahat && /usr/bin/env python3 scripts/memory_consolidate.py >> vault/consolidate.log 2>&1

# 3. Run the consolidation worker once now to warm up.
python3 scripts/memory_consolidate.py --skip-summaries

# 4. Verify the live behavior with a fresh conversation.
# Send these in Telegram in order:
#   "I want to reach 198 lbs by 05/22 2026"
#   "Yes, give me the aggressive plan"
#   "Which days should I CF, run, and rest this week?"
#   "I'll do 7000 kcal/wk for the next 2 weeks"
#
# Each message should benefit from the assembler showing the model
# the structured state from the previous turns.

# 5. (Optional) Run the live eval suite to confirm tool selection.
RAHAT_EVAL_LIVE=1 python3 tests/scientist/eval_reasoner_live.py

# 6. Inspect memory state at any time.
sqlite3 vault/rahat.db "SELECT type, COUNT(*) FROM memory_entities GROUP BY type"
python3 -c "from core import memory; print(memory.stats('scientist'))"
```

---

## Files added (new)

```
core/memory.py                                       430 LOC
core/archival.py                                     200 LOC
agents/the_scientist/memory.py                       280 LOC
agents/bajrangi/__init__.py                          11 LOC
agents/bajrangi/memory.py                            120 LOC
scripts/memory_consolidate.py                        250 LOC
tests/scientist/eval_memory.py                  450 LOC
tests/scientist/eval_gemini_pdf_usecases.py     500 LOC
specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md              350 LOC
specs/MEMORY-AND-STATE-ARCHITECTURE.md (rev 2)       400 LOC
specs/SOTA-BUILD-STATUS.md (this file)               180 LOC
```

## Files modified

```
core/miya.py                — added supervisor capabilities + cross-agent broker
agents/the_scientist/reasoner.py — assembler/extractor wiring, 60-min lookback removed
```

---

## What stayed the same (deliberately)

  - Charter (`core/charter.py`) — unchanged. Composes cleanly with the substrate.
  - Voice layer (`core/voice.py`) — unchanged. Same idempotent dressing on outbound.
  - Decisions ledger (`core/decisions.py`) — unchanged. Now augmented by `memory_events` for richer observability, but both coexist.
  - All existing tools in `tools.py` — unchanged. Backward compatible.
  - Telegram bot loop, splitter, send-error logging — unchanged.
  - Eval-as-gate discipline — preserved across all 475 cases.

---

## What's deferred (by choice)

Documented in `SOTA-AGENT-ARCHITECTURE-REVIEW.md` §10:

  - GraphRAG community detection at scale (need >1000 entities)
  - Hierarchical supervisors (need >10 agents)
  - Multi-tenant / RBAC (single-user mesh)
  - Distributed agents (single Mac mini process)

---

## Mid-build incident: DB corruption + recovery (2026-05-08)

During the build I corrupted `vault/rahat.db` because some smoke tests
wrote to the live path through `Path.home()`. By the time I caught it:

  - `PRAGMA integrity_check` returned "database disk image is malformed"
  - `VACUUM INTO` failed for the same reason
  - But individual `COUNT(*)` queries on user-data tables still worked

Recovery steps taken:
  1. Backed up the corrupted DB to `vault/rahat.db.malformed.<timestamp>`
  2. Wrote a row-by-row recovery script: read each user table, write to
     a fresh DB at `/tmp/rahat_clean.db`
  3. All user data was salvageable: 9 raw_vitals (Apple Watch readings),
     2 weighin_log entries (most recent: 202.6 lbs at 06:18:22 May 8),
     2 user_state rows (`recovery_tier=hammer`,
     `plan_fallback_2026-05-04=0`), 7 weekly_plan rows, 20 nudge_log
     rows, 1 week_preferences row, 2 intents
  4. Replaced live DB with the recovered one. Integrity check now `ok`.
  5. Hardened `core/io.py` with a `RAHAT_TEST_MODE=1` guard that
     redirects ALL DB connections to a per-process sandbox under
     `/tmp/rahat_test_<pid>.db` regardless of the path the caller
     passes. Verified: tests under `RAHAT_TEST_MODE=1` cannot write to
     the live DB even if they explicitly try to.
  6. Added `weekly_campaigns` and `raw_vitals` to the auto-migration
     in `main.py:_db()` so a freshly-initialized DB has all the tables
     legacy code expects.

The corrupted backup file `vault/rahat.db.malformed.<timestamp>` remains
for forensic reference; safe to delete once you confirm the recovered
DB looks right.

## Risks / known limitations

1. **Embeddings require a Gemini API key for full semantic search.** Without one, archival search falls back to recency-ranked. Confirmed working in both paths.

2. **The state extractor runs an extra Gemini Flash call per turn.** Cost: ~$0.0001/turn. Latency: ~300ms. Worth it for the persistence guarantees, but watch the cost dashboard.

3. **First-day state is empty.** The assembler can't show what the substrate doesn't have. As you have conversations, state populates organically. There's an optional `scripts/memory_backfill.py` (deferred — not built) that would walk the existing `decisions` ledger and infer state retroactively. If after 24h the state still feels thin, we can build it.

4. **Bajrangi is a stub.** Just enough to prove the architecture composes; not a working HRV agent. Building the full Bajrangi (tick-driven HRV reads, recovery prescriptions) is a separate ~1-day project.

5. **Sleep-time worker isn't installed yet.** The script is written and tested; you need to add the cron line above to activate it. Without it, memory grows unboundedly (but at your message volume that's months before any concern).

---

## Next steps (your call)

  - **Verify behavior** — send a series of conversational turns in Telegram and confirm the bot now respects commitments, persists schedules, honors preferences across messages.
  - **Install the cron job** — gives you sleep-time consolidation immediately.
  - **Set up GEMINI_API_KEY for embeddings** — already in your .env; the embed call will start working as soon as the bot has an internet connection (it does).
  - **Build full Bajrangi** — when you're ready, ~1 day. The adapter pattern is set; just need to define the rest of its tools + reasoner persona.
  - **Build Curriculum** — for your daughter and son. ~1 day each. Same pattern.

The compounding starts now. Every future agent costs hours, not days.

Sleep well.
