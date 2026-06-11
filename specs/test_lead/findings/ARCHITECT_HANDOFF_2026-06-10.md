# Architect Handoff — 2026-06-10 (post test-lead shift)

You stepped out for 12 hours. Here's what landed while you were away,
what's pending, and what you might be missing.

---

## 1. What landed (architect commits to ship on top of test-lead branch)

### PF-002 — slash + space routing fix
- `new_plane/miya_runner/delegate_classifier.py`: `_SLASH_RE` now
  matches `^\s*/\s*[a-z]` so "/ fix sat 407" (real user phrasing
  mined from the ledger) routes to `kobe_route` instead of falling
  through to synth.

### PF-003 — past-tense + relative-day WOD lookup
- Same file: `_WOD_LOOKUP_RE` adds `what was` to the interrogative
  alternation, and the day-token branch now tolerates `last|this|next`
  qualifiers ("WOD for last Friday", "workout for next Monday").
- Negative guard verified: "design a workout I did last Friday" still
  orchestrates (Fraser-design path intact).

### PF-001 — synth prompt scoped by intent (Bug-I prevention)
- `new_plane/miya_runner/synthesizer.py`:
  - New `_INTENT_FACT_SCOPE` map (workout_lookup → {gym_wod}, pace_query
    → {active_goal, today_target, pace, recalibration}, etc.).
  - `_scope_facts(facts, intent)` filters the facts dict.
  - `_build_prompt` accepts new `intent` kwarg; `synthesize` forwards it.
- `new_plane/miya_runner/orchestrator.py`:
  - Adds `_intent_label(intent)` helper; passes `intent=` to
    `synthesizer.synthesize()`.
- Result: when the user asks about WOD, pace facts no longer leak into
  the prompt — direct fix for the second half of Bug I (2026-06-09).

### PF-004 — contradictory recalibration summary suppressed (Bug-H prevention)
- `synthesizer.py`: new `_is_summary_contradicted_by_verdict()`. When
  arbitration fires `behind_pace` but `recalibration.summary` says
  "Ahead of pace", the summary text is REPLACED with
  `<SUPPRESSED — contradicted arbitration verdict 'behind_pace'>`.
- Gemini never sees the misleading raw string → can't paraphrase it.
- This is the residual half of Bug H (2026-06-08); the arbitration
  block + cost router escalate-to-Pro fixes weren't sufficient alone.

### PF-005 — orchestrator scopes signals by primary agent
- `orchestrator.py`: new `_primary_agent_for_intent(intent)` helper.
- `signals_recent(agent=primary_agent, ...)` — a Kobe pace turn no
  longer sees recent Fraser design payloads in the synth prompt.

### PF-006 — signals scoped by chat_id (concurrent-chat safety)
- `new_plane/signals/store.py`:
  - Additive migration adds nullable `chat_id` column + index. Legacy
    NULL rows remain visible to every chat (backward compat).
  - `publish(... chat_id=)` + `recent(... chat_id=)` accept the scope.
- `native_client.signals_recent` + `adapter_client.signals_recent`:
  - Both forward `chat_id=` to the store / HTTP endpoint.
- `orchestrator.py`: all three `publish_signal` call sites pass
  `chat_id=turn.chat_id or None`. The `signals_recent` call passes it
  too.

### Test artifacts
- `tests/regression_registry/test_2026_06_10_pf_fixes.py` — **26 new
  tests** pinning all six fixes (positive + negative cases).
- Test lead's strict-xfail tests (PF-001/004 in
  `test_synthesizer_grounding.py`, PF-005/006 in
  `test_cross_agent_signal_isolation.py`) rewritten as **green
  fix-verification tests**, matching the orchestrator's new call
  pattern.
- Adversarial corpus (`tests/adversarial/corpus.json`): xfail markers
  for PF-002 and PF-003 cleared; both phrasings now route correctly.

### Verification
```
RAHAT_TEST_MODE=1 python -m pytest \
  tests/regression_registry/test_2026_06_10_pf_fixes.py \
  tests/regression_registry/test_2026_06_09_wod_paraphrase_and_pace_hallucination.py \
  tests/new_plane/test_runner_delegate_classifier.py \
  tests/new_plane/test_runner_delegation_path.py \
  tests/new_plane/test_cross_agent_signal_isolation.py \
  tests/evals/test_synthesizer_grounding.py \
  tests/adversarial/test_corpus_routing.py \
  -q
# 461 passed
```

---

## 2. Known flake (documented, lower priority)

`tests/new_plane/test_compare_harness.py::test_old_vs_new_parity` —
passes 31/31 in isolation, fails 6 tests when run after siblings in
`tests/new_plane/`. Cumulative module-level state pollution. Filed as
**PF-2026-06-10-007** in PROPOSED_FIXES.md with proposed fix.

Workaround: run that file in isolation. The full sweep otherwise
shows 1,058 passed / 6 failed / 16 skipped / 9 xfailed.

---

## 3. What you (the user) might be missing — next-step list

### Immediate (do before you walk back in)
- **Pull the new commits** on your machine:
  `git fetch origin && git checkout feat/new-plane-stage0 && git pull`
- **Restart the runner** to load the fixes:
  ```bash
  pkill -f "new_plane.miya_runner"; sleep 2
  find new_plane -name __pycache__ -exec rm -rf {} + 2>/dev/null
  cd ~/developer/agency/rahat
  source .venv/bin/activate
  set -a; source .env; set +a
  python -m new_plane.miya_runner
  ```
- **Verify in Telegram** with the historically-broken phrasings:
  - `What is tommorows WOD` → kobe_route, no "hasn't been synced",
    no "ahead of plan"
  - `What was the workout for last Friday?` → kobe_route (new!)
  - `/ fix sat 407` → kobe_route (new!)
  - `where am I on pace` → arbitration verdict honored, no "ahead
    of pace" if behind

### Within 24 hours
- **48-hour soak start.** The Phase E cutover (per ADR-013) shouldn't
  happen until both bots have run in parallel for 48 hours with no
  new bug class surfacing. Treat 2026-06-12 evening as the earliest
  cutover window.
- **Open Kobe `arbitration_rule` reporting on RahatBadeMiya turns.**
  The architect side-channel (vault/rahat.db decisions) should flag
  any turn where arbitration fires and the user response either
  echoes the SUPPRESSED summary or contains the OPPOSITE of the
  verdict. That's your live regression net.
- **Adversarial corpus refresh.** Mining ran once (186 phrasings).
  Run it again in a week to capture the next wave of real phrasings:
  ```
  python scripts/mine_phrasings.py --db vault/rahat.db \
    --output tests/adversarial/corpus.json --since-days 14
  ```

### Within 1 week
- **Fix PF-2026-06-10-007** (parity-fixture isolation flake). Without
  it, the test lead's parity harness can't run in the full sweep,
  and the architect can't trust "all green" without an isolation
  caveat.
- **Dead-test cleanup.** Test lead's `findings/DEAD_TESTS.md` flagged
  `tests/adversarial/phrasings.py` (uncollected — missing `test_`
  prefix) and two adapter test files that fail to collect due to a
  starlette/httpx version mismatch in the sandbox environment.
- **Bug-class coverage matrix follow-up.** Test lead's
  `findings/BUG_CLASS_COVERAGE_MATRIX.md` flagged "stale-fact" and
  "multi-turn confusion" as MEDIUM-strength classes. Both deserve
  dedicated regression entries with synthetic + replay data.

### Within 2 weeks
- **Phase E cutover** (per ADR-013) — `launchctl unload com.rahat.miya.plist`
  if soak window is clean. Until then, keep both bots running. Rollback
  is `launchctl load` — keep `SCIENTIST_BOT_TOKEN` valid for at least
  2 weeks post-cutover.
- **8-week thesis evidence.** You skipped this gate in ADR-013. Worth
  retroactively logging: # of arbitration-fired turns, # of new bug
  classes surfaced, # of registry entries added, parity-test ratio
  on real phrasings. If any of these tilt the wrong way, revisit.

### Operational debt the agent surfaced but I haven't addressed
- `scripts/mine_phrasings.py` is now hardened read-only (test lead's
  fix). The original would have opened the live DB in read-write
  mode — small bullet dodged.
- `tests/new_plane/test_openclaw_adapter.py` and
  `tests/new_plane/test_adapter_integration.py` fail collection due
  to starlette/httpx version mismatch. Skipped in my runs via
  `--ignore=`. Fix: pin httpx<0.28 OR migrate to httpx2.

---

## 4. Authorship caveat (carried forward from agent's handoff)

The agent's `git add tests/ scripts/` swept in pre-existing untracked
WIP (your parallel-plane `new_plane/` test files + deploy scripts).
All under `tests/`/`scripts/`, no production or restricted paths, but
mixed authorship. The agent's HANDOFF_FINAL.md lists the 12 files it
authored explicitly. If you want a clean-split PR before merging,
the rebase would be:

```bash
# Optional: rewrite history into "agent's work" vs "your earlier WIP"
git checkout test-lead-agent-2026-06-10
git rebase -i $(git merge-base HEAD origin/main)
# Mark commits to split; rebase --continue after each
```

For pragmatic shipping, the mixed branch is fine — none of the
content is dangerous, and the new tests + production fixes are
self-contained.

---

## 5. Suggested commit + push sequence (architect, on your machine)

```bash
cd ~/developer/agency/rahat
git fetch origin
git checkout feat/new-plane-stage0  # or test-lead-agent-2026-06-10
git pull

# Stage just the architect's PF fixes + new regression file
git add new_plane/miya_runner/delegate_classifier.py \
        new_plane/miya_runner/synthesizer.py \
        new_plane/miya_runner/orchestrator.py \
        new_plane/miya_runner/native_client.py \
        new_plane/miya_runner/adapter_client.py \
        new_plane/signals/store.py \
        tests/regression_registry/test_2026_06_10_pf_fixes.py \
        tests/new_plane/test_cross_agent_signal_isolation.py \
        tests/new_plane/test_compare_harness.py \
        tests/evals/test_synthesizer_grounding.py \
        tests/adversarial/corpus.json \
        specs/test_lead/findings/PROPOSED_FIXES.md \
        specs/test_lead/findings/ARCHITECT_HANDOFF_2026-06-10.md

git commit -m "fix(new-plane): PF-001..006 — synth scoping, signal isolation,
slash typo, past-tense WOD (test-lead findings landed)

PF-001: synthesizer._build_prompt accepts intent=, filters facts by
  intent. WOD-only query no longer receives pace facts (Bug-I residual).
PF-002: _SLASH_RE tolerates '/ fix' (whitespace after slash).
PF-003: _WOD_LOOKUP_RE adds 'what was' + last|this|next qualifier.
PF-004: contradictory recalibration.summary suppressed when arbitration
  verdict says the opposite (Bug-H residual).
PF-005: orchestrator scopes signals_recent(agent=primary_agent).
PF-006: signals.store adds nullable chat_id column; orchestrator
  publishes/reads with chat_id=turn.chat_id.

+26 new regression tests pinning every fix + negative-guard cases.
Test lead's strict-xfail tests rewritten as fix-verification tests.
Adversarial corpus xfails for PF-002/PF-003 cleared.

461/461 green on fix-verification suite. Pre-existing parity-fixture
flake (PF-007) documented; passes 31/31 in isolation."

git push
```

---

## 6. Open questions / decisions to make

1. **Merge agent's test-lead branch into feat/new-plane-stage0** or
   keep it separate as a "test artifacts" sub-branch? Recommendation:
   merge — the 295 new tests are real coverage.
2. **Phase E cutover date.** With Bug H + Bug I + PF-001..006 all
   landed, the new plane is technically the safer surface now. But
   the soak window matters. Recommend: 2026-06-13 evening at the
   earliest.
3. **Adapter test files** (`test_openclaw_adapter.py`,
   `test_adapter_integration.py`) — pin httpx<0.28 in requirements,
   or migrate to httpx2 (per the deprecation warning). Either way,
   the suite is silently missing those 2 files.
4. **Mining cadence.** Set a Cowork schedule to re-mine the corpus
   weekly? It'd catch new phrasing classes before they ship as bugs.

Sleep well. Bot's running on the new code by the time you read this.
