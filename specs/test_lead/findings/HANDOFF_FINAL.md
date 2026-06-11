# Agent Test Lead — Shift Handoff 2026-06-10

## Summary
- Branch: `test-lead-agent-2026-06-10` (base `origin/main` @ `cfb43a9`)
- Hours worked: 15, with self-validation gates each hour
- **Tests added this shift: 295 items** across 8 new files + 2 extended files
  (6 of them strict `xfail` bug-tripwires)
- Layer gate `RAHAT_TEST_MODE=1 python -m tests.run_all`: **exit 0**,
  1009 → **1011** passed / 18 skipped
- New-plane direct pytest: 446 → **734 passed, 6 xfailed**
- Coverage `new_plane.miya_runner`: **76% → 77%** (value is behavioral
  pinning, not line count — see Hour 14 note in PROGRESS.md)
- No production code touched; no API keys; no restricted paths in the diff

## What's new in the suite (my authored deliverables)
- **[Hour 2]** `findings/COVERAGE_AUDIT.md` — 12 gaps + 3 impl-pinning tests
- **[Hour 3]** `tests/new_plane/test_delegate_classifier_properties.py` —
  15 Hypothesis properties (routing invariants; Bug-I/P/Q shapes)
- **[Hour 4]** `tests/adversarial/corpus.json` (186 real mined phrasings)
  + `tests/adversarial/test_corpus_routing.py` (deterministic new-plane
  routing net); hardened `scripts/mine_phrasings.py` to read-only
- **[Hour 5]** `tests/evals/test_synthesizer_grounding.py` — 7 grounding
  pins + 2 xfail (Bug-H/Bug-I residuals)
- **[Hour 6]** `tests/new_plane/test_telegram_history_replay.py` — 17
  (every TELEGRAM_BUG_HISTORY entry)
- **[Hour 7]** `tests/new_plane/test_cross_agent_signal_isolation.py` —
  4 + 2 xfail
- **[Hour 8]** `tests/regression_registry/test_2026_06_08_pace_contradicts_missed_workout.py`
  (Bug H). Bug I (`test_2026_06_09_*`) was already in the registry.
- **[Hour 9]** `tests/new_plane/test_runner_telegram_chaos.py` — 22
- **[Hour 10]** extended `tests/new_plane/test_compare_harness.py` — +24
  (22 parity fixtures)
- **[Hour 11]** `tests/new_plane/test_synthesizer_prompt_snapshot.py` — 11
- **[Hour 12]** `findings/DEAD_TESTS.md` — 6 flagged
- **[Hour 13]** `findings/BUG_CLASS_COVERAGE_MATRIX.md` — 12 classes

## Real bugs surfaced (failing / strict-xfail — architect picks up)
All in `findings/PROPOSED_FIXES.md`, each with a reproduction:
- **PF-2026-06-10-001** — synth `_build_prompt` is unscoped by intent; a
  WOD-only query still receives pace facts (Bug-I off-topic merge).
  `tests/evals/test_synthesizer_grounding.py::...::test_wod_query_prompt_excludes_unrelated_pace_facts`
- **PF-2026-06-10-002** — `/ fix` (space after slash) bypasses `_SLASH_RE`
  → command falls to orchestrate.
  `tests/adversarial/test_corpus_routing.py::...[XFAIL:slash_command:/ fix sat 407]`
- **PF-2026-06-10-003** — past-tense "what was the workout for last Friday"
  not in `_WOD_LOOKUP_RE` → WOD lookup falls to synth (Bug-I class).
  `tests/adversarial/test_corpus_routing.py::...[XFAIL:wod_lookup:What was the workout for last Friday?]`
- **PF-2026-06-10-004** — contradictory `recalibration.summary` passed
  verbatim into the prompt (Bug-H residual; verdict block alone is
  insufficient).
  `tests/evals/test_synthesizer_grounding.py::...::test_contradictory_summary_not_passed_verbatim`
- **PF-2026-06-10-005** — orchestrator pulls `signals_recent` unscoped → a
  Fraser signal enters a Kobe-intent synth prompt.
  `tests/new_plane/test_cross_agent_signal_isolation.py::test_kobe_intent_prompt_excludes_fraser_signals`
- **PF-2026-06-10-006** — `signals` table has no `chat_id`; concurrent
  chats bleed.
  `tests/new_plane/test_cross_agent_signal_isolation.py::test_signals_are_scoped_per_chat`

The two highest-leverage (PF-001, PF-004) are the exact Bug-H / Bug-I
defect class the previous suite missed.

## Coverage gaps still open (from COVERAGE_AUDIT.md)
Error-shaped facts through arbitrate/synth (gap 2); delegated-route
transport-error text (gap 1); native_client `_err` paths (gap 5);
`__main__.py` poll loop still 22% line-covered (needs a `cmd_serve`
integration harness — gap 9); chat_memory load path (gap 4).

## Suite verification (exact)
```
$ RAHAT_TEST_MODE=1 python -m tests.run_all   →  exit 0
| Layer        | Status | Passed | Failed | Skipped |
| unit         |  ✅    |   28   |   0    |    0    |
| contract     |  ✅    |  851   |   0    |   17    |
| eval         |  ✅    |  101   |   0    |    1    |
| adversarial  |  ✅    |   14   |   0    |    0    |
| regression   |  ✅    |   17   |   0    |    0    |
| total        |  ✅    | 1011   |   0    |   18    |

$ RAHAT_TEST_MODE=1 pytest tests/new_plane/ tests/adversarial/ \
    tests/evals/test_synthesizer_grounding.py \
    --ignore=tests/new_plane/test_adapter_integration.py \
    --ignore=tests/new_plane/test_openclaw_adapter.py
  →  734 passed, 6 xfailed
```

## What the next test lead should do first
1. **Push PF-001 + PF-004 to the architect.** They are the Bug-H/Bug-I
   defect class, pinned but unfixed. When fixed, the strict xfails flip
   to failures — remove the markers and add registry entries
   (`test_2026-06-10_wod_query_no_pace_merge.py`,
   `test_2026-06-10_arbitration_supersedes_summary.py`).
2. **Rename `tests/adversarial/phrasings.py` → `test_phrasings.py`**
   (DEAD_TESTS #1) — an entire harness has been silently uncollected.
3. **Add a `cmd_serve` one-iteration integration test** to close the
   `__main__.py` poll-loop coverage gap (22%) with the chat-filter +
   per-update-exception paths.
4. **Pin `starlette`/`httpx`** so `test_adapter_integration.py` /
   `test_openclaw_adapter.py` stop failing collection (DEAD_TESTS #3).

## Environment notes (reality the next lead inherits)
- The committed `.venv`/`venv` are macOS-only; I built `/tmp/rvenv` on
  system Python 3.10. `google-genai` not installed (conftest stubs it).
- `git push` has no credentials in this sandbox and the repo mount
  (virtiofs) forbids file deletion, so git metadata was relocated to a
  writable copy to allow commits. See BLOCKERS.md B-01..B-04.

## Authorship note (important for review)
My hourly commits used a scoped `git add specs/test_lead/findings/ tests/
scripts/`. Because the user's working tree had a large amount of
**pre-existing untracked WIP** (the whole `new_plane/` parallel-plane test
suite, `tests/new_plane/__init__.py`, the already-present Bug-I registry
file, and several `scripts/*.sh` deploy helpers), that scoped add swept
those files into the branch snapshot as well. They are all under `tests/`
and `scripts/` (no production code, no restricted paths), so this is not a
scope violation, but **they are not my work** — they are the architect's
uncommitted parallel-plane WIP that the branch now carries so the suite is
runnable. My authored/edited files are exactly the 12 listed under
"What's new in the suite" above (plus the read-only hardening of
`scripts/mine_phrasings.py` and the `test_compare_harness.py` extension).
NB: the `new_plane/*.py` **production** code these tests import is still
untracked WIP and is NOT in the branch — this PR layers on top of that
work and is not independently mergeable to `origin/main` until the
parallel-plane production code lands.

## My commits
```
bbb968a hour 14 — full re-run + measure
c04da84 hour 13 — bug-class coverage matrix
bf1faed hour 12 — dead/flaky test triage
3fc11ce hour 11 — synthesizer prompt snapshot
853ea74 hour 10 — old-vs-new parity (22 fixtures)
55a29a3 hour 9 — telegram poll-loop chaos
e9454fa hour 8 — registry Bug H (Bug I already pinned)
15f58ea hour 7 — cross-agent signal isolation
8a4b8eb hour 6 — telegram history replay harness
27d3f08 hour 5 — synthesizer grounding harness
dcd0b65 hour 4 — adversarial corpus mined + labeled
29eeb7a hour 3 — classifier property fuzz
a98c6b3 hour 2 — coverage audit
7d9ca4f hour 0 — setup, baseline, mental model
```
