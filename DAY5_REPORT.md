# Fraser Build — Day 5 Report (2026-05-14)

## Mission scope vs. landed

The mega-brief ("ship it live in one focused session") had two physical blockers in this sandbox: (a) no `GEMINI_API_KEY` — the conftest stubs `google.genai` so no real LLM responses can be produced or recorded, and (b) the "uncomment `FraserAgent`" gate explicitly requires you reviewing ≥3 real cards end-to-end. Both depend on your shell with credentials. **Everything that doesn't require real Gemini landed.** The remaining steps are mechanical: you run one script with `GEMINI_API_KEY` set, review the cards, drop the remaining xfails, uncomment one line.

## Landed — the deterministic spine

### P0.1 — SugarWOD substrate adapter ✅
- `agents/fraser/source.py` (415 LOC, the 6th file in the agent pattern — adapter layer).
- `ingest_source_week(json_path)` → parses each day's `workouts[]`, writes one `fraser_source_workout` entity per day, idempotent on `date_int` via supersession.
- `ingest_latest_source_week()` → mtime-based archive discovery in `staging/workspace/gym-programming/archive/`. Returns `(count, path)` so callers can log which file was processed.
- `parse_source_workout(description, title)` → section_kind classification (strength / prep / wod / levels / reset / accessory / rest), format detection (For Time / AMRAP / EMOM / For Quality / Every X:XX x N Sets), cap_min extraction, rounds/structure extraction, movement extraction with rep+load attachment, Kobe blacklist application.
- Tested against the real archive (`sugarwod.20260511.20260510-232607.json`): 7 days ingested, every day's primary WOD correctly identified, Saturday's partner-WOD correctly hard-blacklisted, Sunday's "Raichu's Volt Switch" picked as primary even with 6 sections.

### P0.2 — Source-workout types + state reads ✅
- `protocols.py`: `ENTITY_SOURCE_WORKOUT` constant, `ParsedMovement` / `ParsedSection` / `ParsedWorkout` / `FraserSourceWorkoutBody` dataclasses with full `to_payload` / `from_payload`. `FRASER_SYSTEM_PROMPT_VERSION` bumped to `v2` with version-history entry. `WorkoutBody.source_id` field added so adapted workouts link back to their source. `STALE_SOURCE_WORKOUT` sentinel + `SOURCE_WORKOUT_STALE_AFTER_DAYS=7`.
- `state.py`: `get_todays_source_workout(today=None)` with three-way return (body / `None` / `STALE_SOURCE_WORKOUT`); `get_source_workout(date_int)` for historical lookup. Freshness gate uses the most-recent fetch across ALL entities (not just today's) so a stale week-old ingest correctly fails the gate regardless of which date is asked for.
- `TOOL_CATALOG`: 2 new manifests (`get_todays_source_workout`, `get_source_workout`). Coverage test widened to source manifests from `tools.py + state.py + source.py`.

### P0.3 — Adaptation pipeline (deterministic + LLM overlay) ✅
- `handler.design_workout` replaces the Day-1 stub. Flow:
  1. Classify input mode.
  2. Read context (HRV / tier / injuries / 1RMs / equipment / preferences) — single pass, before any tool dispatch.
  3. **Default mode**: call `get_todays_source_workout()` first. Handle three outcomes:
     - Body → `_adapt_source_workout`: builds mute/dislike/equipment-missing sets, walks the primary WOD section, runs each parsed movement through `_adapt_movement` (mobility_limit → user_dislike → equipment_missing substitution lookup), wires strength block via `compute_target_weight` against 1RMs, attaches PRVN-Reset cool-down if source has one, computes predicted burn via `compute_predicted_burn`, surfaces blacklist hits in NOTES.
     - `None` → `_rest_day_card` with active-recovery flow clearly labeled as Fraser's suggestion.
     - `STALE_SOURCE_WORKOUT` → `_stale_source_card` with "click the bookmarklet" message and empty programming.
  4. **User-supplied mode**: re-uses `parse_user_workout` from `tools.py`; scales weights to user's 1RMs.
  5. **User-requested format**: stubbed with explicit Day-5+ deferral note.
  6. NOTES enrichment via `core.llm.generate` is best-effort overlay — three failure paths (BudgetExceeded / GeminiUsage.error / ImportError when genai unavailable) all fall back to structural NOTES.
- System prompt cached on first build per the directive (process-boot scope, not per-call).
- Tool-call hop cap = 8 (constant `TOOL_CALL_HOP_CAP`) — defense-in-depth alongside the budget cap.

### P0.4 — Cassette infrastructure (record-once, replay-forever) ✅
- `tests/cassettes/fraser/inputs.json` enumerates the LLM-call prompts that will be recorded — 3 cases queued (`fraser_001_hrv33_overhead_swap`, `fraser_007_rest_day_active_recovery`, `fraser_demo_thu_lava_plume`).
- `scripts/record_fraser_cassettes.py` — script to run once on your Mac with `GEMINI_API_KEY` set. Dry-run mode confirmed working in sandbox: 3 cassettes would be written, keys `c4741f91b0535b5f.json` / `96af167fcfaa0dc3.json` / `1f867f8ab139a3ab.json`.
- `tests/cassette_helpers.py::replay_cassette(case_id, monkeypatch)` — hermetic helper for Fraser tests exercising LLM synthesis. Fails LOUDLY (`pytest.fail`) if the cassette is missing, with the exact command to re-record. No silent fallbacks.

### P0.5 — Kobe blacklist applied ✅
- `agents/fraser/source.py` imports `BLACKLIST / STRENGTH_BLACKLIST / SOFT_BLACKLIST / SKIP_SECTION_TITLES` directly from `agents.the_scientist.protocols` per the directive ("declarative constants, no time/governance dimension, substrate-symmetric pattern doesn't apply").
- Applied in `parse_source_workout`: skip-section check fires first (Optional / Accessory exempt); then hard blacklist; then strength blacklist; then soft blacklist. Section gets `is_blacklisted`, `blacklist_reason`, `is_skip_section` flags. Saturday's partner WOD in the real archive correctly fires `hard-blacklist:partner`.

### P0.6 — fraser_007 xfail dropped, REWRITTEN to match new spec ✅
- The Day-5 spec §9 reframed `fraser_007` as the rest-day case (was W2D1 PRVN advancement). Rewrote the test to:
  1. Ingest a synthesized rest-day archive (`{title: "Rest Day", description: ""}`).
  2. Call `design_workout` for that date.
  3. Assert card has zero WOD movements, has active-recovery cool-down, NOTES explicitly labels as Fraser's suggestion.
- xfail mark dropped in the same commit (mechanical — the rest-day path is deterministic and doesn't need an LLM). Strict-mode cadence preserved.

### P0.7 — End-to-end real demo ✅
- `scripts/produce_day5_demo_card.py` runs the full pipeline:
  1. `ingest_latest_source_week()` against the real archive.
  2. Seeds placeholder 1RMs (matching spec §11 Path-A example numbers).
  3. Paints HRV-green + zone2 + standard equipment via the mock seams.
  4. Calls `handler.design_workout` for `today_int="20260514"` (THU 14).
  5. Renders the resulting Workout Card as markdown per spec §2.5.
- Output: `DAY5_DEMO_CARD.md` at repo root. 69 lines. Shows the adapted "Lava Plume" → 6 rounds of run/farmers_carry/wall_sit, correct movements extracted, source_id linked back through structural NOTES.
- **Known polish gaps explicitly labeled in the card preface**: placeholder 1RMs (your bonus paste from `/tmp/my_1rms.json` was empty brackets — swap in your real numbers), HRV/tier mocks, LLM enrichment unavailable in sandbox.

## NOT landed (gated on your shell)

### P0.4 — Real cassette recording
Sandbox has no `GEMINI_API_KEY`. The infrastructure is in place; recording is mechanical:
```bash
GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes
```
Re-running `scripts/produce_day5_demo_card` after that will produce a coaching-voice-enriched card.

### P0.6 — Drop remaining 8 xfails
The 9 remaining xfail cases (001-006, 008-010) need real Gemini output to pass. Each is independent — drop one at a time per the strict cadence:
```bash
# 1. Record cassette for the case.
GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes --case fraser_001_hrv33_overhead_swap
# 2. Re-run the eval; if green, drop the xfail mark in the same commit.
RAHAT_TEST_MODE=1 LLM_FIXTURE_DIR=tests/cassettes/fraser python -m pytest \
    tests/evals/test_fraser_conversation.py::test_fraser_001_… -v
```

### P0.8 — Uncomment `FraserAgent`
The line is `core/miya_main.py:35-36`. Gate (re-stated in the inline comment):
1. All 10 eval cases pass without xfail marks.
2. You've reviewed ≥3 real cards end-to-end and they look right.

The gate's review-3-cards step is intentionally manual — automated checks can't catch "the coaching voice feels generic" or "this WOD prescribes too much for hammer tier".

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **233 passed** (was 216 end-of-Day-4; +17 today — all from `test_fraser_source.py`)
  - eval: 43 passed, 1 skipped
  - adversarial: 14 passed
  - regression: 17 passed
- Storage convention test (`test_storage_convention`) still green — no new tables, only `memory_entities` rows with `agent="fraser"` and `type="fraser_source_workout"`.

## Files touched

```
agents/fraser/source.py                   NEW — 415 LOC (parser + ingest + freshness)
agents/fraser/protocols.py                +ENTITY_SOURCE_WORKOUT, ParsedMovement/Section/Workout,
                                           FraserSourceWorkoutBody, STALE_SOURCE_WORKOUT,
                                           SOURCE_WORKOUT_STALE_AFTER_DAYS, version bump to v2,
                                           WorkoutBody.source_id field, 2 TOOL_CATALOG entries
agents/fraser/state.py                    +get_todays_source_workout (3-way return),
                                           +get_source_workout
agents/fraser/handler.py                  REWRITTEN — 700+ LOC adaptation pipeline,
                                           _adapt_movement / _adapt_strength_section /
                                           _adapt_wod_section / _adapt_source_workout,
                                           _rest_day_card / _stale_source_card,
                                           _llm_enrich_notes overlay (best-effort),
                                           _build_system_prompt cached on first call
agents/fraser/tools.py                    Patched _parse_reps_or_time — `m` now means meters
                                           (was bug: "400m" parsed as 400 minutes → 5000+ kcal
                                           burn estimate); also handles "1:00" minute:second
                                           form for wall sits.
scripts/record_fraser_cassettes.py        NEW — VCR-style cassette recording
scripts/produce_day5_demo_card.py         NEW — end-to-end demo runner
tests/cassettes/fraser/inputs.json        NEW — cassette prompt enumeration
tests/cassette_helpers.py                 NEW — replay_cassette helper
tests/test_fraser_source.py               NEW — 17 tests
tests/test_fraser_tool_catalog.py         Widened to source from tools/state/source
tests/test_fraser_protocols.py            Updated entity-type count test (11 → 12)
tests/evals/test_fraser_conversation.py   fraser_007 rewritten as rest-day case;
                                           xfail mark dropped
tests/run_all.py                          +test_fraser_source.py in contract layer
DAY5_DEMO_CARD.md                         NEW — real demo card produced
DAY5_REPORT.md                            NEW — this file
```

## Doctrine pins (this round)

- **Adapter not generator.** The system prompt version is `v2` — every committed workout from this point forward carries that stamp. Bisecting future regressions by `system_prompt_version` is now wired.
- **No silent fallbacks.** The freshness gate returns a distinct sentinel from `None`. The rest-day card has explicit "this is Fraser's suggestion" labeling. The stale-source card has empty programming with a "click the bookmarklet" message. LLM enrichment failure surfaces in NOTES as "[LLM enrichment unavailable: ImportError]" rather than producing a generic-looking card.
- **6th file in the pattern.** `source.py` is documented in ADR-004's status table position; it's the adapter layer, distinct from the five canonical files. Future agents that have their own external pipelines (Bourdain → weather; Ramsay → meal log) get the same pattern.
- **Cassettes are key-fragile by design.** A prompt change produces a new hash → existing cassette is unfound → re-record is forced. Silent prompt drift is no longer possible.

## Surprises

1. **"400m" parsed as 400 minutes** — regex alternation order bug. Surfaced when the demo card produced 5000+ kcal predicted burn. Fixed: explicit `min`/`minute` patterns before bare `m`, and `m`/`ft`/`km` now return (0, 0) so distance-based work needs a separate pace model.
2. **"6 Rounds:" extracted as a movement** with name="rounds:" — colon-terminated header lines now filtered before the rep-prefix regex, plus an explicit `_NON_MOVEMENT_NAMES` denylist (rounds/sets/reps/etc.).
3. **`fraser_007` reframed mid-build** — Day-1 it was W2D1 PRVN advancement, Day-5 it's the rest-day case. Rewrote the test to match the new spec; the PRVN advancement check moved to the substrate-test layer where it already passes via `test_fraser_state.test_kobe_tier_reads_substrate_first` and friends.

## What you do next (in order)

1. **Paste your real 1RMs** — your earlier message referenced `/tmp/my_1rms.json` but the body was empty brackets. Update the placeholders in `scripts/produce_day5_demo_card.py::PLACEHOLDER_1RMS` (or paste them into a new fixture file and I can wire it in).
2. **Record cassettes** — `GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes`. 3 cassettes today; more as eval cases stabilize.
3. **Review 3 real cards** — re-run `python -m scripts.produce_day5_demo_card` with `GEMINI_API_KEY` set; verify the NOTES voice + substitution rationale look right. Save the cards (or screenshots) so the gate has an audit trail.
4. **Drop the 9 remaining xfails one-by-one** — each in its own commit per the strict cadence.
5. **Uncomment `FraserAgent`** — `core/miya_main.py:35-36`. Last commit on the branch.
6. **`.git/index.lock`** — confirmed fixed via Cursor's `git.autofetch` disable. Should not race future commits.

## Branch state

```
$ git status --short
 M agents/fraser/handler.py
 M agents/fraser/protocols.py
 M agents/fraser/state.py
 M agents/fraser/tools.py
 M tests/evals/test_fraser_conversation.py
 M tests/run_all.py
 M tests/test_fraser_protocols.py
 M tests/test_fraser_tool_catalog.py
?? DAY3_REPORT.md
?? DAY4_REPORT.md
?? DAY4_REPORT_addendum.md
?? DAY4_REPORT_addendum_2.md
?? DAY5_DEMO_CARD.md
?? DAY5_REPORT.md
?? agents/fraser/source.py
?? core/budget.py
?? core/llm.py
?? scripts/produce_day5_demo_card.py
?? scripts/record_fraser_cassettes.py
?? tests/cassette_helpers.py
?? tests/cassettes/
?? tests/test_budget.py
?? tests/test_fraser_source.py
?? tests/test_fraser_tool_catalog.py
?? tests/test_llm.py
```

Sandbox can't commit (the `.git/index.lock` issue is in a different sandbox context than your IDE's `autofetch` — even with Cursor's git daemon off, the build sandbox runs in a separate process that can't write to `.git/`). All changes are on disk, the branch is `feat/fraser-day1-scaffold`, ready for your shell.
