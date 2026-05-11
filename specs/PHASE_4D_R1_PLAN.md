# Phase 4d (R1) — `main.py` god-file split: execution plan

**Status:** ✅ **EXECUTED 2026-05-11.** Five commits on origin/main:
- `11317c9` — Step 0a: centralize DB_PATH via core.io
- `ca0e758` — Step 1a: extract DB helpers → `state.py`
- `bb8231c` — Step 1b: extend `state.py` with prefs + logs
- `ca4c7b1` — Step 2a: move planning + recalibration math → `state.py`
- `54a63d5` — Step 2b: extract handlers + router + nudges + loop → `handler.py`

**Result:** `main.py` shrunk from **2,930 LOC to ~140 LOC** (95% reduction). Four-file shape (protocols / state / handler / main thin) now active. 142 tests passed before and after every step — byte-identical eval-suite behavior (148 cases) preserved end-to-end.

The plan-as-written below was a slight overestimate of step granularity (we needed an extra "Step 0a" to centralize DB_PATH before the state extraction could proceed safely, because 12 tests patched `sci.DB_PATH` as a module attribute). The five-step actual sequence is documented in the commits above.

**Source:** R1 of `specs/ARCH_REVIEW_2026-05-08.md`.
**Constraint:** Eval suite at `tests/scientist/eval_suite.py` (148 cases) must pass byte-identical pre and post. Voice/router/charter behavior cannot change. ← **Constraint held.**

---

## 1. Current shape (HEAD = `ab30feb`)

`agents/the_scientist/main.py` is **2,930 LOC across 11 sections**:

| Section | Lines | LOC | Role |
|---|---|---|---|
| Path bootstrap + imports | 1–95 | 95 | Module-load setup |
| Config | 97–138 | 42 | `_active_model`, gym-plan parsing |
| DB helpers | 139–377 | 239 | `_db`, `state_get/set`, burn math, intent ledger |
| Weekly plan | 378–588 | 211 | `replan_week`, `current_plan`, `today_plan`, day types |
| Week recalibration | 589–837 | 249 | `detect_missed_workouts`, `compute_week_recalibration`, `handle_recalibrate` |
| Per-week preferences | 838–1047 | 210 | get/set/clear prefs, weight/HRV log, nudge state |
| Gym plan | 1048–1054 | 7 | Tiny marker |
| Handlers | 1055–1955 | 901 | **35+ `handle_*` functions** — the bulk |
| Intent router | 1956–2548 | 593 | `route`, `_legacy_route`, `llm_coach`, Hindi parsing |
| Nudges | 2549–2804 | 256 | `maybe_*` ambient nudges |
| Loop | 2805–end | 125 | `start`, `send`, `_split_for_telegram` |

The file mixes pure math, DB I/O, Telegram I/O, intent dispatch, and the polling loop. That's why agent.py has to load it via importlib as `sys.modules['sci']` — there's no clean import surface.

## 2. Target shape — three files (excluding main.py)

Per the user's "keep folder structure simple" directive, **no new directory**. Three files at the same flat level inside `agents/the_scientist/`:

```
agents/the_scientist/
    protocols.py    extended  — pure math, constants, day-type rules, week-recalibration math
    state.py        NEW       — DB connection + state get/set + I/O-bound helpers
    handler.py      NEW       — all handle_* + route/_legacy_route + llm_coach + nudges + Hindi parsing
    main.py         shrunk    — launchd entry: imports handler.start, owns __main__ guard
    agent.py        unchanged — ScientistAgent wrapper still loads main.py as 'sci'
```

**Final main.py shape (target ~150 LOC):**

```python
"""The Scientist — launchd entry point + thin re-export surface.

After the R1 split, all logic lives in:
    protocols.py  — pure math + constants
    state.py      — DB + state I/O
    handler.py    — handle_* + route + nudges + voice dispatch

main.py exists for two reasons:
    1. Launchd / agent.py loads it as `sys.modules['sci']` — that import
       contract must keep working.
    2. Top-level re-exports preserve `sci.handle_*`, `sci.route`, etc., so
       no eval case has to change.
"""
from agents.the_scientist.protocols import *      # math + constants
from agents.the_scientist.state import *          # DB + state helpers
from agents.the_scientist.handler import *        # handlers + router + nudges
from agents.the_scientist.handler import start    # explicit — entry point

if __name__ == "__main__":
    start()
```

The `from X import *` style is the single concession to backward-compat. Each child module exports a careful `__all__` so namespaces don't collide.

## 3. Migration order — five small commits

Each step keeps the test suite green; if any goes red, revert that one commit only.

### Step 1: Extract pure math → `protocols.py` (extends existing)

Move into `protocols.py`:
- Section 4 (Weekly plan): `day_type_target`, `replan_week`, `current_plan`, `today_plan`
- Section 5 math (NOT `handle_recalibrate`): `detect_missed_workouts`, `compute_week_recalibration`
- Pure helpers from Section 3: `weekly_target`, anything that doesn't touch the DB

Add explicit `__all__` to `protocols.py`. Re-export from `main.py` via `from agents.the_scientist.protocols import *`. **No behavior change.** Run eval suite.

**Commit:** `refactor(scientist): extract pure math into protocols.py (R1 step 1/5)`

### Step 2: Extract state/DB I/O → `state.py` (NEW)

Move into `state.py`:
- Section 3: `_db`, `state_get`, `state_set`, `get_active_intent`, `check_external_veto`, `burn_for_date`, `burn_for_range`, `burn_this_week`, `burn_last_week`
- Section 6: `get_prefs`, `set_prefs`, `clear_prefs`, `latest_weight`, `sync_weight`, `recalibrate_intents`, `log_hrv`, `log_workout`, `last_hammer_day`, `nudge_already_sent`, `mark_nudge`

Re-export via `from agents.the_scientist.state import *`. Run eval suite.

**Commit:** `refactor(scientist): extract DB and state I/O into state.py (R1 step 2/5)`

### Step 3: Extract handlers + router → `handler.py` (NEW)

Move into `handler.py`:
- Section 8: all 35+ `handle_*` functions
- Section 9: `route`, `_legacy_route`, `llm_coach`, Hindi/Hyderabadi parsing helpers
- Section 10: all `maybe_*` nudge functions
- Section 11: `start`, `send`, `_split_for_telegram` (the loop is dispatch-adjacent)

Re-export. Run eval suite. **This is the largest single move (~1,800 LOC).** The risk concentrates here.

**Commit:** `refactor(scientist): extract handlers, router, nudges, loop into handler.py (R1 step 3/5)`

### Step 4: Slim `main.py`

After steps 1–3, `main.py` is mostly imports + `if __name__ == "__main__"`. Remove the original dead bodies (now duplicated). End shape ~150 LOC.

**Commit:** `refactor(scientist): main.py becomes thin entry — final R1 step (4/5)`

### Step 5: Validate the import contract end-to-end

ScientistAgent's `agent.py` does `importlib.util.spec_from_file_location("sci", _SCI_MAIN_PATH)` and runs the module. After the split, `sys.modules["sci"]` must still expose every attribute the eval suite reads (e.g., `sci.TESTS`, `sci.route`, every `handle_*`). Run the full hermetic stack + smoke-test `tests.scientist.eval_suite` — should still report 148 cases.

If green: write the R1 summary commit message. If anything is red, the commit-per-step structure means the regression is bisected to one move.

**Commit:** `test(scientist): full eval-suite parity check post-R1 split (5/5)`

## 4. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Circular import: `state.py` imports from `protocols.py` and `handler.py` imports both | Strict layering: protocols < state < handler < main. No upward imports. |
| `from X import *` collisions if two modules export the same name | Explicit `__all__` per module. Run `python -c "import sci; print(set(dir(sci)))"` and diff against pre-split snapshot. |
| Eval suite reads `sci.TESTS` (defined inside `eval_suite.py`, not `main.py`) | Already independent. No risk. |
| `handle_recalibrate` calls `compute_week_recalibration` (pure math) | After step 1, handler.py imports from protocols.py. Standard cross-file call. |
| Nudges in `handler.py` call `state.py` helpers (DB writes) | Standard cross-file call. Layering allows it. |

## 5. Validation gates

Before each step's commit:

```
RAHAT_TEST_MODE=1 ./venv/bin/python -m tests.run_all
```

Expected: 116 passed, 1 skipped (current baseline).

After step 5 specifically:

```
./venv/bin/python -m tests.scientist.eval_suite     # 148 cases via legacy sci.route()
./venv/bin/python -m tests.scientist.eval_extended  # 7-dimension sweep
```

Both should report the same pass counts as pre-split.

## 6. Out of scope (deferred)

- **Rewriting `from X import *` into explicit imports.** Possible follow-up; not blocking. Star imports are normally a smell, but here they exist solely to preserve the `sci.*` import contract for the legacy ScientistAgent loader. Once a future commit removes the legacy importlib loader (rewriting `agent.py` to import from the new module names directly), the stars can be replaced with explicit lists.
- **Splitting `handler.py` further.** It will be ~1,800 LOC after R1 — still big, but coherent (all dispatch logic lives there). A natural next pass would group handlers by domain (`handler/burn.py`, `handler/plan.py`, `handler/voice.py`), but that's a Phase 5+ refactor. R1 is just getting things out of `main.py`.

---

## Resume command for next session

```
Continue Rahat refactor. HEAD is ab30feb. Next is Phase 4d (R1) per
specs/PHASE_4D_R1_PLAN.md — five-commit split of agents/the_scientist/main.py
(2,930 LOC) into protocols.py + state.py + handler.py + thin main.py.
Eval suite at tests/scientist/eval_suite.py (148 cases) is the
byte-identical contract. Start with Step 1 (pure math → protocols.py).
```
