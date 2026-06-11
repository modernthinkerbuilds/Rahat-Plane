# test-lead-agent-15hr-pass-2026-06-10

**Branch:** `test-lead-agent-2026-06-10` → **base:** `main`

(`gh` CLI and `git push` are unavailable in the agent sandbox — this file
is the PR title + body for the human to open the PR. Full detail in
`specs/test_lead/findings/HANDOFF_FINAL.md`.)

## What this PR does
Hardens the Rahat test suite against the defect class that shipped Bug H
(2026-06-08) and Bug I (2026-06-09): the synth layer turning unsupported
facts into user-read assertions. Tests only — **no production code
changed**.

## Tests added (count by area)
- Property-based classifier fuzz: **15** (`test_delegate_classifier_properties.py`)
- Adversarial corpus (186 real mined phrasings) + routing net: **189** (`test_corpus_routing.py`, `corpus.json`)
- Synthesizer grounding evals: **9** (7 + 2 xfail)
- Telegram history replay: **17**
- Cross-agent signal isolation: **6** (4 + 2 xfail)
- Bug-H regression registry: **2**
- Telegram poll-loop chaos: **22**
- Old↔new parity fixtures: **+24**
- Synth prompt snapshot: **11**

**Total: 295 new test items** (6 strict `xfail` bug-tripwires).

## Coverage gaps identified
`specs/test_lead/findings/COVERAGE_AUDIT.md` — 12 ranked gaps;
`BUG_CLASS_COVERAGE_MATRIX.md` — 12 bug classes with strength + next steps;
`DEAD_TESTS.md` — 6 dead/low-signal/flaky items (incl. an entire
adversarial harness that was never being collected).

## Bugs found, not fixed (test-lead scope → architect)
6 entries in `PROPOSED_FIXES.md`, each a strict xfail with a repro:
PF-001 (intent-unscoped synth prompt, Bug-I), PF-002 (`/ fix` space-slash),
PF-003 (past-tense WOD lookup), PF-004 (contradictory summary verbatim,
Bug-H), PF-005 (unscoped signal pull), PF-006 (no chat_id on signals).

## Suite runtime before/after
Layer gate ~18s (baseline) → ~30s (with +2 registry tests, subprocess
variance). New-plane direct ~17s → ~21s for ~290 more tests.

## Proof every layer is green
```
$ RAHAT_TEST_MODE=1 python -m tests.run_all        # exit 0
total ✅ 1011 passed / 0 failed / 18 skipped
$ RAHAT_TEST_MODE=1 pytest tests/new_plane/ tests/adversarial/ \
    tests/evals/test_synthesizer_grounding.py \
    --ignore=tests/new_plane/test_adapter_integration.py \
    --ignore=tests/new_plane/test_openclaw_adapter.py
734 passed, 6 xfailed
```

## Reviewer notes
- See HANDOFF_FINAL.md "Authorship note": a scoped `git add tests/
  scripts/` also captured pre-existing untracked parallel-plane WIP
  (architect's new_plane test files + deploy scripts). All under
  `tests/`/`scripts/`, no production/restricted paths. My authored files
  are the 12 listed in the handoff.
- This PR layers on the (still-untracked) `new_plane/` production code, so
  it is not independently mergeable to `origin/main` until that lands.
