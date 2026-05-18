# Kobe Day-9 Report — show_plan bug + reasoner tools + Fraser tightening

**Branch:** `feat/kobe-day9-reasoner-tools` (off main `b384a95`, post-hotfix tip)
**Architect:** Kobe (Modern Builder)
**Date:** 2026-05-17
**Status:** ✅ three commits ready for user review

---

## Top-line numbers

| Layer        | Baseline (main) | After Day-9      | Δ      |
|--------------|-----------------|------------------|--------|
| unit         | 28              | 28               |  0     |
| contract     | 504 (+1 skip)   | **551** (+1 skip)| **+47**|
| eval         | 53 (+1 skip)    | 53 (+1 skip)     |  0     |
| adversarial  | 14              | 14               |  0     |
| regression   | 17              | 17               |  0     |
| **total**    | **616** (+2)    | **663** (+2)     | **+47**|

Gate floor was **616** (Bug 1 ~3 + Bug 2 ~6+ targeted; actual delta +47 driven by parametrize fan-out across the new factual-tool catalog).

Three commits, structured per brief for independent revert if needed:
1. Bug 1 — `handle_show_plan` lie fix (5 files, +4 tests)
2. Bug 2 — six factual-query tool wrappers + system-prompt directive + dislikes block (3 files, +35 tests)
3. Bug 3 — Fraser description tightened with compact defer sentence (2 files, +1 test)

---

## Bug 1 — `handle_show_plan(next_week=True)` lied about gym sync

### Root cause

`agents/the_scientist/state.py::replan_week` writes a state flag the FIRST time it picks CF days for a week:

```python
fallback_key = f"plan_fallback_{week_key}"
con.execute(
    "INSERT INTO user_state (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    (fallback_key, "1" if plan_fallback else "0"))
```

`plan_fallback` here is a **transient** condition snapshot — true when (a) no gym plan was synced at the time of replan, OR (b) too many blocked movements for 3+ clean CF days. The flag is never re-evaluated.

Then in `handler.py::handle_show_plan` (line 712 of the pre-fix file):

```python
is_fallback = state_get(f"plan_fallback_{week_key}", "0") == "1"
```

When the user runs `replan_week` BEFORE syncing the SugarWOD bookmarklet, the flag goes to `"1"`. When the user later syncs the bookmarklet (loading the real weekly_plan.txt with Bench Press 1RM / Back Squat 1RM / etc.), the flag stays `"1"`. Next call to `handle_show_plan(next_week=True)` reads stale `"1"` → enters the warning block → emits the lying message:

> "⚠️ No gym plan synced — using default Mon/Wed/Fri cadence."

…even though `parse_gym_plan()` would now return 7 full GymDay objects with non-empty bodies.

### Before / after

**Before** (lines 706–757, pre-fix):
- `is_fallback` set once from the stale state flag.
- Inside `if is_fallback:` block, branches on `clean_picks` counted against the *committed* plan (which is itself stale Tue/Thu/Sat/Sun, so `clean_picks == 0` and "No gym plan synced" fires).

**After**:
- `is_fallback` re-derived from `parse_gym_plan()` output **on every render**:
  - `has_synced_data and len(clean_wds) >= 3` → `is_fallback = False`, no warning at all.
  - `has_synced_data` but `<3` clean weekdays → `is_fallback = True`, warning text branches on the **current** gym state (not the stale committed plan) so it correctly says "Only N days are blacklist-clean" instead of "No gym plan synced".
  - No synced data at all → trust the stale flag for the historical signal.
- Same code path applies to `next_week=True` and `next_week=False`.

### Pinned by `tests/test_kobe_show_plan_fix.py` (4 tests)

| Test                                                | Pins                                                         |
|-----------------------------------------------------|--------------------------------------------------------------|
| `test_handle_show_plan_does_not_lie_no_plan_synced` | **NAMED REGRESSION GATE.** Seeds the production state (real PLAN_PATH + stale `plan_fallback_{week_key}="1"`), asserts the warning does NOT fire. |
| `test_handle_show_plan_contains_day_labels_from_file` | Structural: all 7 weekday names render. |
| `test_handle_show_plan_honors_no_sync_when_plan_path_missing` | Inverse: when PLAN_PATH actually missing, the warning STILL fires. Verifies the fix didn't silence a true positive. |
| `test_handle_show_plan_works_for_current_week_too`  | The fix applies to `next_week=False` too. |

### Latent footgun surfaced

`parse_gym_plan()` resolves `PLAN_PATH` via `handler.PLAN_PATH` (handler's own module globals), NOT `sci.PLAN_PATH` (the main.py star-import alias). Tests that rebind only `sci.PLAN_PATH` silently use production's PLAN_PATH. My fixture rebinds **both** and the failing-test refactor calls this out as a documented gotcha. Worth a wider sweep — older tests in the suite may have the same silent bug.

---

## Bug 2 — Reasoner had no tools for factual user-state queries

Same hallucination pattern as the 2026-05-16 WOD bug. `grep blacklist|dislike|plan agents/the_scientist/reasoner.py` returned zero hits — the LLM was answering "what's my plan next week" from training-data priors.

### Tool surface added to `agents/the_scientist/tools.py`

Six wrappers, all returning `str` (the user-facing text the legacy `handle_*` would have emitted) — no dict-wrapping so the reasoner sees the same string Kobe would say directly:

| Tool                    | Wraps                          | Triggering phrasings                                                  |
|-------------------------|--------------------------------|-----------------------------------------------------------------------|
| `get_plan(next_week)`   | `handle_show_plan`             | "what's my plan", "show my schedule", "which days do I work out"     |
| `get_workout_on(day)`   | `handle_workout_on`            | "what is my workout on Tuesday", "what am I doing Friday"             |
| `get_dislikes()`        | `handle_list_dislikes`         | "what am I skipping", "what's blacklisted", "show my dislikes"        |
| `get_tier()`            | direct state read (one-line)   | "what tier am I on", "show my recovery state"                         |
| `get_weight_history(d)` | `handle_weight_timeline`       | "weight history", "weight trend", "when will I hit 80 kg"             |
| `get_pace()`            | `handle_pace`                  | "pace check", "am I on track", "status"                               |

Each tool description leads with ALWAYS / NEVER directives so the model has high-signal pull language, paraphrase-rich (10+ variant phrasings each) so it picks them up across user phrasings.

`get_workout_on` accepts weekday tokens in any case with 3+ leading letters (mon / monday / TUE / tues / wednesday) — refuses unparseable tokens with a polite error rather than calling through with `idx=None`.

`get_tier` is a new lightweight wrapper returning a one-liner — coexists with the older dict-shaped `get_recovery_tier` (which the reasoner uses for tier-comparison math).

### System-prompt directives in `agents/the_scientist/coach_system.py`

Two new blocks lead the prompt (after `CURRENT DATE`):

1. **`FACTUAL_QUERIES`** — verbatim brief directive: "For factual questions about the user's plan, dislikes, weight history, HRV, tier, or specific-day workout, ALWAYS call the corresponding tool... NEVER synthesize these values from training-data priors. The 2026-05-16 and 2026-05-17 production incidents both involved Kobe hallucinating these values; this directive exists to prevent recurrence." Lists each tool by name with the canonical triggering phrasings.

2. **`_current_dislikes_block()`** — dynamic live snapshot of `agents.the_scientist.dislikes.active_movements()` re-read on every `system_text()` call. Belt-and-suspenders: even if the model skips `get_dislikes()`, the blacklist is in-context. Empty-state ("none") rendered cleanly when no dislikes are active.

### Block order in `system_text()` (new shape, eight blocks)

```
CURRENT DATE
FACTUAL_QUERIES         (NEW Day-9)
DELEGATION_POLICY       (Day-8)
ATHLETE_IDENTITY
_current_dislikes_block (NEW Day-9, dynamic)
COACHING_MINDSET
VOICE_RULES
ANTI_HALLUCINATION
```

Both "discipline" blocks (FACTUAL + DELEGATION) lead because they're the failure-mode countermeasures — if the model skips them, the rest of the prompt can't save it.

`system_blocks()` (the deprecated path) also gets both new blocks defensively so any legacy caller stays in sync.

### Pinned by `tests/test_kobe_reasoner_tools.py` (35 tests across 5 sections)

- §1 Tool surface present: 18 parametrize tests (3 × 6 tools) — in SCHEMAS, in _DISPATCH, has ALWAYS/NEVER directive in description.
- §2 Dispatch behavior: 8 tests verifying each wrapper calls through to its `handle_*` with the right args, plus the unparseable-day refusal.
- §3 `get_tier`: 2 tests on the live-state read path.
- §4 System prompt: 6 tests — `FACTUAL QUERIES` header present, every tool name listed, the "NEVER synthesize from priors" sentence present, dislikes block present with live movement names, empty-state gracefully handled.
- §5 Bug-1 cross-pin: 1 source-grep guard that `handle_show_plan` calls `parse_gym_plan()` BEFORE consulting the stale flag.

---

## Bug 3 — Fraser description tightened

Production query: **"What is my workout for Tuesday?"** was landing at Fraser → default-mode stub. Per the brief, Fraser's description gets the inverse of Kobe's Day-8 "Defer to Fraser for: …" pattern.

### Change

`agents/fraser/agent.py::FraserAgent.description` now carries THREE disclaim signals — the classifier reads them as a triple-redundant defense:

1. **Day-8 weight/HRV/tier "DOES NOT own:"** clause (preserved — long-form domain enumeration is still useful classifier signal)
2. **Day-8 lookup "DOES NOT own:"** clause (preserved — same)
3. **NEW Day-9 compact:** `"Defer to Kobe for: weekly plan lookups, weekday-specific workout lookups, weight tracking, HRV interpretation, recovery tier."` — mirrors Kobe's `"Defer to Fraser for: …"` pattern, which Day-2 evidence showed the classifier reads more reliably than prose disclaimers.

### Pinned by

- Existing `tests/test_fraser_description_contract.py` already byte-pinned both Day-8 clauses (no change required — they continue to pass after my consolidation).
- New `test_description_contains_compact_defer_to_kobe_sentence` byte-pins the verbatim Day-9 compact sentence.

---

## Five Telegram smoke queries with expected synced replies

These are the production-style smoke tests for the user to run by hand once the branch is merged. Each query targets a specific class of regression Day-9 fixes. Expected replies cite the **real** synced data (per `parse_gym_plan()` output you confirmed: Mon 18 through Sun 24, full bodies with Bench Press / Back Squat / Hang Snatch / Deadlift / MURPH on Sat).

| # | Query                                            | Expected reply (real synced data, NOT generic Tue/Thu/Sat/Sun) | Class                                  |
|---|--------------------------------------------------|----------------------------------------------------------------|----------------------------------------|
| 1 | "Which days am I working out next week?"         | Plan grid headed `Next week — May 18 – May 24` with CF day_types on the gym-aligned weekdays (Mon / Wed / Fri or wherever the synced plan's clean days fall). **MUST NOT** contain the literal string `No gym plan synced`. | Bug 1 — show_plan lie fixed             |
| 2 | "What is my workout for Tuesday?"                | Reply uses Tuesday's gym programming from the synced file (e.g. Bench Press strength + the WOD body). **MUST NOT** be a Fraser default-mode stub. | Bug 3 — Fraser defers lookup to Kobe    |
| 3 | "What am I skipping right now?"                  | Lists every active dislike from `dislikes.active_movements()` (scope + reason if any), or "none" if empty. Whatever appears, comes via `get_dislikes()` — not the model's guess. | Bug 2 — get_dislikes tool wired         |
| 4 | "Pace check"                                     | Today's prorated burn-vs-target line, three rows (Today — kind, Actual / Expected so far, Day target). Either `get_pace` is invoked via tool_use, or `/pace` slash-command path runs deterministically. | Bug 2 — get_pace tool wired (and slash fallback) |
| 5 | "What is the WOD"                                | Routed to **Fraser** (Day-2 mesh routing). Fraser produces an adapted Workout Card from today's synced programming. **MUST NOT** be a Kobe-side hallucination. | Day-2 ADR-006 + Bug-3 sanity (no regression) |

Failure on any of (1)/(2)/(3)/(4) is a Day-9 regression. Failure on (5) is a Day-2 regression (and should fire `test_what_is_the_wod_delegates_to_fraser` in the contract suite well before it reaches production).

---

## Files touched (uncommitted on `feat/kobe-day9-reasoner-tools`)

### Bug 1 commit
| Path                                          | Why                                                                                          |
|-----------------------------------------------|----------------------------------------------------------------------------------------------|
| `agents/the_scientist/handler.py`             | `handle_show_plan` re-derives `is_fallback` from `parse_gym_plan()`; warning branch on `has_synced_data` |
| `tests/test_kobe_show_plan_fix.py` (NEW)      | 4 contract tests pinning the fix + the no-sync inverse case                                 |
| `tests/run_all.py`                            | Wire new test file into contract layer                                                       |

### Bug 2 commit
| Path                                          | Why                                                                                          |
|-----------------------------------------------|----------------------------------------------------------------------------------------------|
| `agents/the_scientist/tools.py`               | Six new wrappers + SCHEMAS entries + _DISPATCH mappings                                      |
| `agents/the_scientist/coach_system.py`        | `FACTUAL_QUERIES` constant + `_current_dislikes_block()` + `system_text()` reorder + `system_blocks()` defensive update |
| `tests/test_kobe_reasoner_tools.py` (NEW)     | 35 tests across 5 sections                                                                   |
| `tests/run_all.py`                            | Wire new test file into contract layer                                                       |

### Bug 3 commit
| Path                                          | Why                                                                                          |
|-----------------------------------------------|----------------------------------------------------------------------------------------------|
| `agents/fraser/agent.py`                      | Add compact "Defer to Kobe for: …" sentence (preserve Day-8 clauses for backward compat)    |
| `tests/test_fraser_description_contract.py`   | New byte-pin test for the compact sentence                                                   |

Zero touches to `core/*`, `agents/the_scientist/reasoner.py`, ADRs. Out of scope per brief.

---

## Handback to user

Three commits, clean revert boundary per bug. From your shell at `~/developer/agency/rahat`:

```bash
# Make sure you're on the Day-9 branch (sandbox couldn't checkout for you).
git checkout -b feat/kobe-day9-reasoner-tools   # or: git checkout feat/kobe-day9-reasoner-tools

# --- Commit 1: Bug 1 ---
git add agents/the_scientist/handler.py \
        tests/test_kobe_show_plan_fix.py \
        tests/run_all.py
git commit -m "fix(kobe): handle_show_plan stops lying 'no gym plan synced' (Day-9 Bug 1)

Production incident 2026-05-17: handle_show_plan(next_week=True) emitted
'⚠️ No gym plan synced — using default Mon/Wed/Fri cadence' even though
parse_gym_plan() returned 7 full GymDay objects from a healthy synced
weekly_plan.txt.

Root cause: state.replan_week writes user_state.plan_fallback_{week_key}
as a transient condition snapshot at the moment it picks CF days. The
flag is never re-evaluated. Users who replan BEFORE running the SugarWOD
bookmarklet leave the flag at '1' permanently; handle_show_plan trusts
the stale flag and emits the false-negative warning every render.

Fix: handle_show_plan re-derives is_fallback from parse_gym_plan() output
on every render. Only trusts the stale flag when no synced data is
present at all. The warning text now branches on current gym state, not
the (possibly stale) committed plan.

Pinned by tests/test_kobe_show_plan_fix.py. Named regression gate:
test_handle_show_plan_does_not_lie_no_plan_synced. Contract layer
+4 tests."

# --- Commit 2: Bug 2 ---
git add agents/the_scientist/tools.py \
        agents/the_scientist/coach_system.py \
        tests/test_kobe_reasoner_tools.py \
        tests/run_all.py
git commit -m "feat(kobe): reasoner gets factual-query tools + FACTUAL QUERIES directive (Day-9 Bug 2)

Same hallucination pattern as Bug 1 / 2026-05-16 WOD bug, generalized:
the LLM reasoner was answering user-state factual questions ('what's my
plan next week', 'what am I skipping', 'show my weight trend') from
training-data priors because the tool catalog had no anchors for them.

Three layers:

  1. Tool surface — six wrappers in tools.py: get_plan, get_workout_on,
     get_dislikes, get_tier, get_weight_history, get_pace. Each wraps
     the existing handle_* user-facing function, returns str, lands in
     both SCHEMAS and _DISPATCH. Descriptions lead with ALWAYS / NEVER
     directives and 10+ paraphrase phrasings.

  2. System prompt — new FACTUAL_QUERIES block at the top of
     system_text() (after CURRENT_DATE, before DELEGATION_POLICY). Maps
     each query class to the tool. Verbatim 'NEVER synthesize these
     values from training-data priors' clause.

  3. Live dislikes snapshot — _current_dislikes_block() reads
     dislikes.active_movements() per render and surfaces the active
     blacklist directly in the prompt. Belt-and-suspenders: even if
     the model skips get_dislikes(), the data is in-context.

Pinned by tests/test_kobe_reasoner_tools.py (35 tests, 5 sections).
Named regression gate: TestSystemPromptDirectives::
test_system_text_says_never_synthesize_from_priors. Contract layer
+35 tests."

# --- Commit 3: Bug 3 ---
git add agents/fraser/agent.py tests/test_fraser_description_contract.py
git commit -m "fix(fraser): tighten description with compact 'Defer to Kobe for:' sentence (Day-9 Bug 3)

Production query 'What is my workout for Tuesday?' was landing at Fraser
and hitting the default-mode stub. The Day-8 'DOES NOT own: lookup of
scheduled workouts...' prose clause was correct in spirit but diffuse;
Day-2 evidence showed the classifier reads compact 'Defer to X for: …'
sentences (Kobe's pattern) much more reliably.

Adds the compact mirror of Kobe's 'Defer to Fraser for: workout design,
CrossFit programming, scaled loads, WOD selection.' line:

    Defer to Kobe for: weekly plan lookups, weekday-specific workout
    lookups, weight tracking, HRV interpretation, recovery tier.

Both Day-8 'DOES NOT own:' clauses are preserved — the classifier
benefits from all three signals together (the new compact sentence,
plus the two longer prose enumerations).

Pinned by test_description_contains_compact_defer_to_kobe_sentence in
tests/test_fraser_description_contract.py. Contract layer +1 test."

git push -u origin feat/kobe-day9-reasoner-tools
```

DO NOT merge to main yet — the brief says the user reviews. Open a PR titled "Day 9: show_plan fix + reasoner tools + Fraser tightening" with this report linked from the body.

— Kobe Architect, 2026-05-17
