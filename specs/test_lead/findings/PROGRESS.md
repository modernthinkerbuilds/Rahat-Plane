# Agent shift — 2026-06-10

Test Lead agent. Branch `test-lead-agent-2026-06-10`, base `origin/main`
@ `cfb43a9`. Budget: 15 hours. Goal: measurably stronger suite + clean
handoff. Tests only — no production edits.

## Baseline (before any agent work)

Layer runner (`RAHAT_TEST_MODE=1 python -m tests.run_all`) — exit 0:

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | PASS | 28 | 0 | 0 | 0.46s |
| `contract` | PASS | 849 | 0 | 17 | 12.69s |
| `eval` | PASS | 101 | 0 | 1 | 3.75s |
| `adversarial` | PASS | 14 | 0 | 0 | 0.55s |
| `regression` | PASS | 17 | 0 | 0 | 0.33s |
| **total** | PASS | **1009** | **0** | **18** | **17.77s** |

New plane (run via direct pytest — NOT in the layer runner):
`RAHAT_TEST_MODE=1 pytest tests/new_plane/` → **446 passed in 16.96s**
(excluding 2 files that fail *collection* in this sandbox — see Env notes).

**Baseline gate: GREEN.** Shift starts.

## Environment notes (sandbox divergence — reality wins)

- The committed `.venv` / `venv` point to macOS Homebrew pythons
  (`python@3.11`, `python@3.12`) that don't exist in the Linux sandbox.
  Built a fresh venv at `/tmp/rvenv` on system Python 3.10.12 with
  `pytest hypothesis pytest-cov pytest-xdist requests python-dotenv
  fastapi uvicorn pydantic`. `google-genai` intentionally NOT installed —
  `tests/conftest.py` stubs `google.genai`, so the suite is hermetic
  without it.
- `tests/new_plane/test_adapter_integration.py` and
  `test_openclaw_adapter.py` fail at COLLECTION with
  `RuntimeError: The starlette ...` (FastAPI `TestClient`/starlette
  version mismatch under pytest 9 in this sandbox). This is an env/dep
  issue, not a suite defect, and both files are outside the test-lead
  focus areas (HTTP adapter / OpenClaw). I run new_plane with
  `--ignore` for those two. Logged in BLOCKERS.md.

## Agent mental model: 2026-06-10 hour 0

How one test call reaches a verdict, and the hermetic guard:

1. `tests/conftest.py` runs at import time **before any rahat module**:
   sets `RAHAT_TEST_MODE=1` (this is the hermetic block — it forces
   `core/io.py:_resolve_db_path` to redirect every write to a per-process
   tempfile, so a buggy test can't corrupt `vault/rahat.db`; this guard
   exists because of the 2026-05-08 corruption incident). It also stubs
   `google.genai` with `_StubClient` (real LLM calls return the literal
   `[LLM-FALLBACK]`), and adds repo root to `sys.path`.
2. `python -m tests.run_all` runs 5 layers, **each in its own
   subprocess** (`run_layer` → `subprocess.run([python, -m, pytest, -q,
   <paths>])`), so a crash in one layer doesn't sink the others. Exit
   code is 0 only if every layer passed. Results render to
   `tests/last_run_report.md`.
3. **The layer runner does NOT include `tests/new_plane/`.** Only
   `tests/regression_registry/` (inside the `contract` layer) overlaps
   with my work. So my new_plane / adversarial / synth-grounding files
   must be validated with direct `pytest`, and my Bug-H/Bug-I **registry**
   files must stay green under `run_all` (contract layer).

### Routing & synth wiring (the surfaces I'll test most)

- `new_plane/miya_runner/delegate_classifier.py:classify_delegation(msg)`
  → returns `(path, stripped)` where path ∈ {`kobe_route`,
  `fraser_route`, `orchestrate`}. (`huberman` funnels to `kobe_route`;
  `@miya`/empty/whitespace → `orchestrate`.) 9 ordered checks: @-address,
  slash, plan-mutation, state-logs, status-query, pain/profile, recovery,
  WOD-lookup (guarded against design intent), else orchestrate.
- `new_plane/miya_runner/orchestrator.py:handle(Turn)` → `Response`
  (fields incl. `text`, `arbitration_rule`, `used_tools`, `routing`).
  On a delegation path it calls `adapter.kobe_route`/`fraser_route`
  (native_client, direct import) and returns deterministic text. On
  `orchestrate` it pulls facts via `adapter.kobe_*`, calls
  `arbitrate(facts)`, charter-checks, cost-routes, and `synthesize()`s.
- `arbitrate(facts)` (in `new_plane/miya_sim/orchestrator.py`) fires
  `behind_pace` when `recalibration.behind_pace is True`; `goal_close`
  when an active goal has `0 < weeks_to_target < 1`. Pure function.
- `synthesizer._build_prompt(*, user_message, facts, arbitration,
  fraser_text, recent_signals, chat_memory_block=None)` is keyword-only.
  **`arbitration` dict must carry BOTH `rule` and `guidance`** (it reads
  `arbitration['guidance']` — passing only `rule` KeyErrors). The
  `SYSTEM_PROMPT` already carries the load-bearing grounding lines:
  honesty ("do not say 'ahead of pace' … when recalibration says
  behind"), "Synced WOD is the source of truth … do not paraphrase".
- **Offline grounding gap:** `synthesizer._structured_fallback` (used
  when no GEMINI_API_KEY) dumps `recalibration.summary` verbatim. So with
  a misleading summary ("Ahead of pace"), offline `Response.text`
  contains "ahead of pace" even when arbitration fired `behind_pace`.
  Text-level Bug-H prevention is therefore a *live-model* property; the
  deterministic pin is `arbitration_rule`. The text gap is exactly what
  the synth-grounding harness flags (xfail + PROPOSED_FIX).

Hermetic block answer: enforced at the top of `tests/conftest.py`
(`os.environ["RAHAT_TEST_MODE"] = "1"` + `google.genai` stub), which
prevents (a) writes to the live `vault/rahat.db` and (b) live network /
LLM calls. Off-limits to me.

---

## Hour log

### Hour 3 — property-based fuzz of classify_delegation
File: `tests/new_plane/test_delegate_classifier_properties.py` — **15
Hypothesis properties, all green** (200 examples each). Covers: valid
path sentinels, slash→Kobe, @fraser/@miya strip+route, deterministic,
whitespace-invariant, design-intent-never-Kobe, realistic day-typo→Kobe
(Bug-I shape), bare-number weight log (Bug-P), HRV log (Bug-Q),
unicode/long-input no-crash.

Two BY-DESIGN boundaries surfaced by fuzzing (not bugs, not PFs):
1. `_SLASH_RE` is `^\s*/[a-z]` (re.I) — ASCII only. `/ü…` falls through
   to orchestrate. No unicode slash commands exist; correct as-is.
2. `_WOD_LOOKUP_RE` branch-1 caps the verb→noun gap at 40 chars, so a
   pathological 30+-char "tomorrrr…row" exceeds the window and
   orchestrates. Realistic typos (tomorow/tommorow/tmrw) all route Kobe.
   The 40-char cap is a deliberate guardrail; left as-is.
Generators were scoped to the specified input domain (ASCII slash,
realistic typos) rather than xfail'ing non-bugs.

### Hour 4 — adversarial corpus mined + labeled
- Hardened `scripts/mine_phrasings.py` to open the live DB **read-only**
  (`file:...?mode=ro`) per SCOPE_BOUNDARIES (2026-05-08 corruption guard).
- Mined 364 real `miya.route` messages → **186 unique phrasings** into
  `tests/adversarial/corpus.json`, each labeled by product contract:
  `expected_path` (99 kobe / 81 orchestrate / 6 fraser), `expected_agent`,
  `intent`, `first_seen`, `occurrences`.
- New deterministic harness
  `tests/adversarial/test_corpus_routing.py`: **187 passed, 2 xfailed**.
  Labels are independent intent judgments; an independent rule-labeler
  agreed with the classifier on 184/186, and the 2 deltas are genuine
  gaps → **PF-2026-06-10-002** (`/ fix` space-slash) and **-003**
  (past-tense WOD lookup) — xfail(strict) + PROPOSED_FIXES.
- FINDING for Hour 12: `tests/adversarial/phrasings.py` is **never
  collected** by pytest — the filename lacks the `test_` prefix, so its
  `test_*` functions never run. The whole old-plane adversarial harness
  has been inert. (My corpus test is correctly named.)

### Hour 5 — synthesizer grounding harness
File: `tests/evals/test_synthesizer_grounding.py` — **7 passed, 2 xfailed**.
Green pins: arbitration verdict surfaces + "Honor this"; goal_close verdict;
SYSTEM_PROMPT honesty directive byte-present; prompt never injects "hasn't
been synced"; anti-fabrication directive present on error fact; gym_wod
marked SOURCE OF TRUTH + read back verbatim; user question quoted.
2 xfail tripwires surface real defects → PF-2026-06-10-001 (prompt unscoped
by intent — Bug-I off-topic pace merge) and PF-004 (contradictory
recalibration.summary passed verbatim — Bug-H residual). Both confirmed by
direct _build_prompt inspection.

### Hour 6 — Telegram history replay harness
File: `tests/new_plane/test_telegram_history_replay.py` — **17 passed**.
13 routing contracts (bugs H/I/J/K/L/M/N/O/P/Q/R/S — every entry in
TELEGRAM_BUG_HISTORY.md), 1 arbitration contract (Bug-H behind_pace), 3
behavior contracts via handle() (Bug-J no old-router fallback; Bug-K no
fraser_design tool; Bug-I no synth hallucination on WOD lookup). Routing
is the guarantee mechanism: a kobe_route turn never reaches synth, so the
paraphrase hallucinations cannot appear. Bug-H text-level suppression is a
live-model property pinned as xfail PF-004 in the grounding evals.

### Hour 7 — cross-agent signal isolation
File: `tests/new_plane/test_cross_agent_signal_isolation.py` — **4 passed,
2 xfailed**. Green: store agent-filter excludes other agents (both
directions), trace_id isolation, type isolation. Xfail tripwires expose
two real leaks → PF-005 (orchestrator pulls signals_recent unscoped → a
Fraser signal enters a Kobe-intent synth prompt) and PF-006 (signals have
no chat_id column → concurrent chats bleed). Both are the SUITE_MAP §9.7
weak spot, now pinned.

### Hour 8 — regression registry: Bug H (Bug I already pinned)
- Added `tests/regression_registry/test_2026_06_08_pace_contradicts_missed_workout.py`
  (Bug H) — 2 tests, green: arbitrate() sides with the structured
  behind_pace field over the misleading "Ahead of pace" summary; the
  orchestrator propagates `arbitration_rule=behind_pace`. Verbatim symptom
  + root cause + fix in the docstring.
- REALITY CHECK (SUITE_MAP §6 said Bug I was "missing"): the architect
  already shipped `test_2026_06_09_wod_paraphrase_and_pace_hallucination.py`
  (tracked in git, 47 tests, green). I did NOT duplicate it. My Hour-6
  replay harness + the new corpus test add complementary new-plane
  coverage of the same bug.
- Full `run_all` re-run: **exit 0**, contract layer 849→851, total 1011
  passed / 18 skipped. Gate green.

### Hour 9 — Telegram poll-loop chaos
File: `tests/new_plane/test_runner_telegram_chaos.py` — **22 passed**.
Scenarios: long-poll timeout, network error, not-ok response, offset+timeout
pass-through (offset persistence), multi-message-in-one-poll ordering;
parse_update edge cases (empty text/voice-photo, missing chat id, non-message
update, edited_message, unicode/emoji/RTL/ZWJ, chat_id stringification);
4096-char splitting (exact boundary, oversized single paragraph, never-exceed
property); send_message multi-chunk + Markdown→plain fallback; and the
monotonic offset invariant under a backwards/reset update_id. All hermetic.

### Hour 10 — old-vs-new parity
Extended `tests/new_plane/test_compare_harness.py` — **31 passed** (was 7).
Added 22 parity fixtures across all major intents (slash/pace/plan/wod/
mutation/weight/hrv/burn/pain/recovery/design/@fraser/casual), a coverage
test, and an aggregate silent-failure guard. Both planes call the real
Kobe/Fraser tools under RAHAT_TEST_MODE; the invariant pinned is "new
plane never returns empty + takes the expected deterministic route".
Parity observations (→ BUG_CLASS_COVERAGE_MATRIX): the harness's old side
is a structured-fallback proxy (under-represents prod old plane, so
fine-grained text parity isn't asserted); on a fresh hermetic DB several
log-shaped prompts ("154", "burned 800 cal") fall to Kobe's reasoner
rather than a deterministic confirmation (no user state), and "what is the
WOD" makes Kobe attempt a delegation to an unregistered Fraser. These are
env/registration artifacts, not silent failures, and are noted for the
architect rather than filed as PFs.

### Hour 11 — synthesizer prompt snapshot
File: `tests/new_plane/test_synthesizer_prompt_snapshot.py` — **11 passed**.
10 scenarios pin the load-bearing prompt strings (verified against source,
not the plan's placeholder wording): Bug-H (arbitration verdict dominates +
"Do not say 'ahead of pace'" honesty line), Bug-I (gym_wod "SOURCE OF TRUTH"
+ "paraphrase it into something else" guard + WOD read-back), system-prompt
invariants, goal_close, chat-memory block (Bug-J), Fraser draft label,
recent-signals "may or may not be relevant", recalibration summary render,
empty-facts scaffolding, direct-question-first directive.

### Hour 12 — dead/flaky test triage
File: `specs/test_lead/findings/DEAD_TESTS.md` — 6 items flagged (≥5
required). Highest signal: (#1) `tests/adversarial/phrasings.py` never
collected (filename lacks `test_` prefix → its 186-case harness has been
inert); (#3) `test_adapter_integration.py` + `test_openclaw_adapter.py`
fail collection under starlette skew → contribute zero. Plus a placeholder
`cassette_helpers.py::test_something`, teardown-dominated adapter_client
tests, the permanently-skipped LLM-judge, and a date-of-day flakiness
class for the 02:22 nightly. Nothing deleted (architect's call).

### Hour 13 — bug-class coverage matrix
File: `specs/test_lead/findings/BUG_CLASS_COVERAGE_MATRIX.md` — 12 bug
classes (≥8 required), each with incidents, catching layer/files, strength
(STRONG/MEDIUM/WEAK), and concrete additions. Throughline: Bug H/I lived
one layer PAST routing (synth↔facts), which is why a routing-strong suite
caught nothing. This shift moves classes 2/7/8/9 toward MEDIUM/STRONG and
pins residual defects as strict xfails (PF-001..006) so fixes have a
finish line. Under-served priorities flagged for the next lead.

### Hour 14 — full suite re-run + measure

**Layer gate (`python -m tests.run_all`): exit 0**
- BEFORE: total 1009 passed / 18 skipped
- AFTER:  total 1011 passed / 18 skipped  (+2 = Bug-H registry, runs in contract layer)

**New-plane direct pytest** (excl. 2 starlette-broken files):
- BEFORE: 446 passed
- AFTER (new_plane + adversarial + new grounding eval): 734 passed, 6 xfailed

**Tests added this shift: 295 items** (≥200 target met), of which 6 are
strict xfail bug-tripwires (PF-2026-06-10-001..006):
| File | Items |
|---|---|
| test_delegate_classifier_properties.py | 15 |
| adversarial/test_corpus_routing.py | 189 (186 real phrasings + 3 meta) |
| evals/test_synthesizer_grounding.py | 9 (7 + 2 xfail) |
| test_telegram_history_replay.py | 17 |
| test_cross_agent_signal_isolation.py | 6 (4 + 2 xfail) |
| regression_registry/test_2026_06_08_*.py | 2 |
| test_runner_telegram_chaos.py | 22 |
| test_synthesizer_prompt_snapshot.py | 11 |
| test_compare_harness.py (extended) | +24 |

**Coverage delta — `new_plane.miya_runner`: 76% → 77%** (classifier 100%,
synthesizer 95→96%, native_client 82→83%, telegram 97%). The delta is
deliberately modest: the new value is *behavioral* (property fuzz,
real-corpus routing, grounding tripwires, poll-loop chaos), not line
coverage — most target surfaces were already line-covered but not
behavior-pinned. The remaining big line gap is `__main__.py` poll-loop
(22%), which needs a `cmd_serve` integration harness (noted for next lead).

**Suite runtime:** layer gate ~18s→~30s wall (subprocess variance);
new-plane direct ~17s→~21s for ~290 more tests.

### Hour 15 — handoff + PR
Files: `findings/HANDOFF_FINAL.md`, `findings/PR_DESCRIPTION.md`.
End-of-shift safety checks: run_all exit 0; no API keys in diff; committed
delta touches only tests/, scripts/, specs/test_lead/ (no agents/, core/,
new_plane/miya_runner/*.py, .env, vault/, pytest.ini). `git push` has no
sandbox credentials → PR_DESCRIPTION.md written per AGENT_PROMPT §10.
