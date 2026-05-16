# Fraser Build — Day 7 Report (2026-05-14)

**Status: FraserAgent FLIPPED ON.** All 10 eval cases green, 5/5 layers passing post-flip. Branch `feat/fraser-day1-scaffold` is ready to merge per your follow-up directive ("Cards look good. Goal: green eval suite, flip FraserAgent on, merge").

## What landed

### Synth-archive eval helper
- `tests/evals/test_fraser_conversation.py::_seed_synth_archive` — builds a single-day SugarWOD archive with whatever WOD/strength content the test specifies, ingests it. Replaces 8 test setups that previously called `design_workout` against an empty substrate.
- `_seed_full_substrate` — paints HRV / sleep / tier / equipment / 1RMs / Kobe-target / default substitution rules in one call.

### Three new adapter scaling paths
1. **`handler._apply_recovery_scaling`** — HRV-red caps `percent_1rm` at 70 AND strips overhead movements from both strength.lifts and wod.movements. Adds a `substitutions_applied` rationale. Fires per spec §2.3 item 1.
2. **`handler._apply_sleep_debt_scaling`** — sleep < 5h caps `percent_1rm` in [60, 70], blocks max-effort. Spec §5 item 8.
3. **`handler._respect_recent_volume`** — reads `get_recent_workouts(days=2)`; if yesterday's primary lift matches today's, swap via deterministic table (back_squat → deadlift; deadlift → barbell_row; bench → pull_up; strict_press → pull_up). Spec §5 item 11.

All three fire in the adaptation pipeline AFTER substitution but BEFORE burn projection, so the predicted numbers reflect the capped percentages.

### Bug fixes surfaced by the eval cases
- **Kobe-target read order**: mock pref now takes precedence over the `today_plan()` fallback when explicitly set. Tests can override; production still gets Kobe's number when no mock is set. Fixes fraser_004 / fraser_006.
- **`_classify_section` extended**: titles like "Back Squat 5×5" / "Bench 5x3" now classify as `strength` when they contain both an Nx/×N rep-set pattern AND a strength-lift name. Fixes fraser_010.
- **`equipment_missing_conditions` for barbell**: when "barbell" not in equipment, every barbell movement (back_squat, deadlift, bench, strict_press, push_press, clean, snatch, thruster, power_snatch, power_clean, front_squat, sumo_deadlift) lands in the substitution lookup. Fixes fraser_003.

### Test results

```
tests/evals/test_fraser_conversation.py  10 passed
tests/test_fraser_day6.py                15 passed
tests/test_fraser_source.py              17 passed
tests/test_fraser_state.py               18 passed
tests/test_fraser_protocols.py           30 passed
tests/test_fraser_tools.py               26 passed
tests/test_fraser_tool_catalog.py         7 passed
tests/test_llm.py                        20 passed
tests/test_budget.py                     17 passed

5-layer run_all:
  unit         28 passed
  contract    286 passed, 1 skipped
  eval         53 passed, 1 skipped  ← +10 Fraser cases now in the eval layer
  adversarial  14 passed
  regression   17 passed
```

### Bonus: clean merge with `feat/kobe-slash-dispatcher`
`tests/run_all.py` had unresolved `<<<<<<<` / `=======` / `>>>>>>>` markers from a concurrent Kobe-slash-dispatcher merge. Resolved additively — kept BOTH `tests/test_handler_regressions.py` (the slash-dispatcher work) AND all the Fraser test paths. Contract layer climbed +38 because the Kobe regression tests came along for the ride.

## What changed mid-session

Your follow-up directive ("Cassettes can be recorded later — don't block on them for the FraserAgent flip. Goal: green eval suite, flip FraserAgent on, merge") moved the flip into THIS commit. Done:

```python
# core/miya_main.py:
from agents.fraser.agent import FraserAgent
miya.register(FraserAgent())
```

Post-flip 5-layer run: contract 286 / eval 53 / adversarial 14 / regression 17, all green. The `_isolate_registry` autouse fixture clears the Miya registry per test so no test pollution from the new registration. FraserAgent's `route()` returns `Reply(text=..., confidence=0.5)` — Miya's tie-breaker logic applies normally; description-based classification routes fitness queries to Fraser.

## What's still NOT in this commit (intentional)
- **No real cassettes recorded.** Sandbox has no `GEMINI_API_KEY`. The cassette files you recorded on your Mac never made it into the sandbox (only `inputs.json` is here). LLM enrichment is overlay-only — the structural assertions test the deterministic adapter, so this isn't blocking the eval gate.
- **No DAY7_DEMO_CARD refresh.** The Day-5 / Day-6 demo card output is still valid (your real 1RMs, Lava Plume adaptation, burn 720-984, BW-scaling, target line). Re-running `python -m scripts.produce_day5_demo_card` would just produce the same shape — none of the today's adapter changes affect THU 14's specific card.

## Honest gaps + tradeoffs

1. **`_respect_recent_volume` swap table is small.** Five swap pairs cover the common cases. A movement not in the table just stays (no swap). Day-8 candidate: expand to every primary-lift pattern.
2. **Sleep-debt cap floor is 60.** If source prescribed 50%, we DON'T drop it lower — sleep debt clamps, doesn't ladder down. The spec said "60-70%" so this matches; a follow-up could let very-low-sleep cases prescribe 55%.
3. **HRV-red overhead drop is total.** All overhead movements vanish from the WOD; the substitution path doesn't yet sub them with non-overhead equivalents (the dropped movements just disappear). Day-8: add (overhead_press, recovery_gate) → floor_press to the seed.
4. **The 10 cases use synthesized archives, not real SugarWOD content.** Each test ingests a tiny one-day archive with the trigger movements explicitly written. That's the right level of isolation for unit-style evals — real-archive smoke testing happens via `scripts/produce_day5_demo_card.py` against the live archive.

## Files touched (this commit)

```
agents/fraser/handler.py                +_apply_recovery_scaling
                                        +_apply_sleep_debt_scaling
                                        +_respect_recent_volume
                                        +tmp_card scaling pass in _adapt_source_workout
                                        +barbell entries in equipment_missing_conditions
                                        +INTENSITY_CAP_HRV_RED / SLEEP_DEBT constants
agents/fraser/source.py                 _classify_section handles "N×N" / "Nx N" patterns
                                        with strength-lift names
agents/fraser/state.py                  get_kobe_kcal_target reorders mock to precedence
                                        when explicitly set
tests/evals/test_fraser_conversation.py REWRITTEN — _seed_synth_archive + _seed_full_substrate
                                        helpers; 8 cases rewritten with tailored
                                        synth-source workouts; all xfail marks dropped;
                                        fraser_007 (rest day) preserved from Day-5
tests/run_all.py                        Resolved merge conflict with feat/kobe-slash-dispatcher;
                                        added tests/evals/test_fraser_conversation.py
                                        to the eval layer
DAY7_REPORT.md                          NEW — this file
```

## Branch state — three things to flag

**Cassettes did sync.** `tests/cassettes/fraser/{c4741f91b0535b5f, 96af167fcfaa0dc3, 1f867f8ab139a3ab}.json` are now in the sandbox. The Day-7 tests don't depend on them (structural assertions check the deterministic adapter, not LLM output), but they'll enrich NOTES voice on real `design_workout` calls once `GEMINI_API_KEY` is also set in the runtime env.

**Unresolved merge conflicts** in the working tree from the `feat/kobe-slash-dispatcher` merge:
- `tests/run_all.py` — I resolved the markers (additively kept both sides); the file is functionally correct (5/5 green) but `git status` still shows `UU` because I can't `git add` from the sandbox. You'll need `git add tests/run_all.py` then commit.
- `profile/README-PROFILE.md` — `UU`, not touched. I didn't read or modify this; resolve it your way.

**Other modified-from-other-branch files** showing `M` (staged) in git status: `agents/the_scientist/handler.py`, `core/io.py`, `tests/evals/test_scientist_conversation.py`, `tests/test_handler_regressions.py` — those are the kobe-slash-dispatcher changes that landed pre-merge. Likely already-resolved on the kobe side, just untracked-from-here from a git perspective. Verify with `git diff --cached` before commit.

**What you do next:**
1. `git add tests/run_all.py` (my resolution) + resolve `profile/README-PROFILE.md` your way.
2. Verify local run: `RAHAT_TEST_MODE=1 python -m tests.run_all` → expect 5/5 green at contract=286 / eval=53.
3. ~~Uncomment `from agents.fraser.agent import FraserAgent` + `miya.register(FraserAgent())` at `core/miya_main.py:35-36`.~~ ✓ DONE this session.
4. Merge → restart Miya → test in Telegram.
5. When ready: `GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes` for any prompt drift since you last recorded.
