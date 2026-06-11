# Doc 1 — Test Suite Map

This is the landscape doc. Read it cover-to-cover before writing any
new tests. Hour 1 of the 15-hour plan.

---

## 1. Layout

```
tests/
├── run_all.py                 # Five-layer runner (subprocess-isolated)
├── nightly.sh                 # Nightly Cowork-scheduled wrapper
├── NIGHTLY_PROMPT.md          # Agent runbook for auto-fix loop
├── conftest.py                # Hermetic guarantees, RAHAT_TEST_MODE=1
├── README.md                  # Suite policy doc
│
├── test_voice.py              # LAYER 1: pure-function unit tests
├── test_cost.py               # LAYER 1: cost router
│
├── test_miya_routing.py       # LAYER 2: Miya orchestrator contract
├── test_charter_policies.py   # LAYER 2: Charter ABI
├── test_decisions.py          # LAYER 2: decisions-ledger invariants
├── test_rebrand_aliases.py    # LAYER 2: ADR-002 alias contract
├── test_storage_convention.py # LAYER 2: ADR-003 substrate
├── test_dislikes.py           # LAYER 2: dislike-capture round-trip
├── test_handler_regressions.py# LAYER 2: slash dispatcher, prorated math
├── test_kobe_*.py             # LAYER 2: Kobe surfaces (mesh, reasoner, show_plan, description)
├── test_fraser_*.py           # LAYER 2: Fraser protocols/state/tools/source/day6
├── test_capability_router.py  # LAYER 2: ADR-006 classifier
├── test_delegation.py         # LAYER 2: ADR-007 cross-agent
├── test_clarification.py      # LAYER 2: ADR-008 multi-turn flow
├── test_budget.py             # LAYER 2: ADR-005 enforcement
├── test_llm.py                # LAYER 2: wire-call chokepoint
│
├── evals/
│   ├── test_scientist_conversation.py   # LAYER 3 (424 LOC) — Sports Scientist replay
│   ├── test_fraser_conversation.py      # LAYER 3 (466 LOC) — Fraser replay
│   ├── test_fraser_grounding_evals.py   # LAYER 3 (210 LOC) — grounding
│   └── test_adversarial.py              # LAYER 4 (400 LOC) — injection/jailbreak/PII
│
├── regression_registry/       # LAYER 5 — one file per shipped bug
│   ├── README.md
│   ├── conftest.py
│   └── test_2026_MM_DD_*.py   # 33 files; date-prefixed
│
├── test_replay_regression.py  # LAYER 5: golden-fixture replay vs router
│
├── new_plane/                 # New-plane Miya v2 tests (19 files, 472 tests)
│   ├── test_runner_orchestrator.py
│   ├── test_runner_delegate_classifier.py   # *** 163 tests, central routing ***
│   ├── test_runner_delegation_path.py
│   ├── test_runner_synthesizer.py
│   ├── test_runner_chat_memory_bridge.py
│   ├── test_runner_cost_router.py
│   ├── test_runner_native_client.py
│   ├── test_runner_adapter_client.py
│   ├── test_runner_telegram.py
│   ├── test_runner_live_db.py
│   ├── test_runner_nudges.py
│   ├── test_runner_wod_lookup.py
│   ├── test_transcript_scenarios.py         # Fraser/Scientist/Miya scenario coverage
│   ├── test_regression_equivalents.py       # New-plane equivalents of registry bugs
│   ├── test_adapter_integration.py
│   ├── test_compare_harness.py              # Side-by-side old vs new
│   ├── test_openclaw_adapter.py
│   ├── test_miya_sim.py
│   └── test_signal_store.py
│
├── adversarial/
│   └── phrasings.py            # Bootstrapped — corpus EMPTY (your job to fill)
│
├── scenarios/
│   ├── eval_fraser_doc_scenarios.py
│   └── journey_harness.py
│
├── fixtures/
│   ├── replay_golden.json      # LAYER 5 inputs
│   └── sugarwod_archive_2026-05-11.json
│
├── cassettes/
│   └── fraser/                 # Recorded LLM responses for offline replay
│
├── production_parity/          # Old-vs-new parity tests
├── scientist/                  # Sports-Scientist-specific tests
└── silent_failure/             # Empty-reply detectors
```

**Total file count:** 112 `*.py` files under `tests/`.

---

## 2. The five layers (what each guarantees)

| # | Layer       | Time budget | Failure means |
|---|-------------|-------------|---------------|
| 1 | Unit        | <1s/test    | A pure function is wrong. Look at the helper module. |
| 2 | Contract    | <2s/test    | An agent's surface API drifted. Usually agents/* or core/*. |
| 3 | Eval        | <5s/test    | Scenario fidelity broke against the months-long Gemini coaching thread. Usually a synth-prompt regression. |
| 4 | Adversarial | <5s/test    | A safety probe regressed. The Charter or an agent guard is broken — P0. |
| 5 | Regression  | <2s/test    | A historical bug came back. The registry file's docstring tells you what. |

Per-layer subprocess isolation means a crash in one layer doesn't take
down the rest. Per-layer pass/fail is captured in
`tests/last_run_report.md`.

---

## 3. The runner

```bash
# Full suite, markdown report at tests/last_run_report.md
RAHAT_TEST_MODE=1 python -m tests.run_all

# One layer
RAHAT_TEST_MODE=1 python -m tests.run_all --layer eval

# Skip optional LLM-as-judge (requires GEMINI_API_KEY and RAHAT_RUN_JUDGE=1)
RAHAT_TEST_MODE=1 python -m tests.run_all --no-llm-judge

# Direct pytest for iteration
RAHAT_TEST_MODE=1 pytest tests/new_plane/ -q

# Bug-to-test pre-push gate
python scripts/check_bug_has_regression_test.py
```

**Exit code is 0 only if every layer passed.** No partial credit.

---

## 4. Hermetic guarantees (from `tests/conftest.py`)

- `RAHAT_TEST_MODE=1` is forced before any rahat module imports.
  `core/io.py:_resolve_db_path` honors this and redirects writes to a
  temp DB.
- `google.genai` is stubbed with `_StubClient`. Real LLM calls return
  the literal `[LLM-FALLBACK]` marker.
- For controlled responses, use the `fake_llm` fixture (rule-based mock).
- Telegram I/O is replaced by `captured_tg.outbox` (in-memory list).
- Each test starts with an empty Miya registry (autouse fixture).
- `sandbox_db` fixture gives each test its own SQLite file.

**Tripwire:** a clean `pytest -q` should produce zero network calls and
zero writes to `vault/rahat.db`. CI fails the build if either is
observed.

**Off limits for the test lead** (per scope boundaries):
- The hermetic block at the top of `conftest.py`. Touch it and you've
  reintroduced the 2026-05-08 corruption class.
- `.env`, `pytest.ini`.

---

## 5. The new plane (where most of your work lands)

`new_plane/miya_runner/` is the orchestrator that powers RahatBadeMiya.
The 472-test suite under `tests/new_plane/` covers:

| File | What it pins | LOC |
|------|---|---|
| `test_runner_delegate_classifier.py` | All routing decisions in `classify_delegation` | 163 tests, ~370 LOC |
| `test_runner_orchestrator.py` | `handle()` end-to-end (intent → facts → arbitrate → synth) | medium |
| `test_runner_delegation_path.py` | Kobe/Fraser delegation paths integration | medium |
| `test_runner_synthesizer.py` | Prompt construction, chat-memory block | medium |
| `test_runner_chat_memory_bridge.py` | RAHAT_XAGENT_MEMORY flag behavior | 6 tests |
| `test_runner_cost_router.py` | Flash vs Pro escalation | medium |
| `test_runner_native_client.py` | Direct-import path (default) | 23 tests |
| `test_runner_adapter_client.py` | HTTP adapter fallback | medium |
| `test_runner_telegram.py` | Poller mechanics | medium |
| `test_runner_live_db.py` | NEW_MIYA_USE_LIVE_DB flag | 7 tests |
| `test_runner_nudges.py` | NEW_MIYA_NUDGES_ENABLED flag, tick cadence | 7 tests |
| `test_runner_wod_lookup.py` | Orchestrate-path WOD mechanics (legacy, mostly via @miya prefix) | 6 tests |
| `test_transcript_scenarios.py` | Fraser/Scientist/Miya transcript replay | 66 tests |
| `test_regression_equivalents.py` | Registry bugs verified in new plane | 38+ tests |
| `test_compare_harness.py` | Side-by-side old vs new responses | small |

The central object you will spend the most time with is
`new_plane/miya_runner/delegate_classifier.py:classify_delegation()`.
It is the routing brain. Every regression in routing manifests as a
defect here. Treat it as the highest-leverage surface to fuzz.

---

## 6. The regression registry (your "immutable floor")

`tests/regression_registry/` contains one file per historical bug. Each
file MUST start with a docstring containing:

1. The date the bug shipped.
2. The symptom the user saw (verbatim if possible).
3. The root cause.
4. The fix.
5. What this test asserts (structural or behavioral pin).

**Naming convention:** `test_YYYY-MM-DD_short_bug_name.py`

**The rule:** every `fix:` commit must add at least one file here. The
pre-push gate (`scripts/check_bug_has_regression_test.py`) greps the
diff. No regression test → no merge.

**Current bug log (33 files):**

| Date | Bug |
|------|-----|
| 2026-05-16 | Kobe hallucinated WOD instead of delegating to Fraser |
| 2026-05-17 | Slash bypass dispatched to wrong agent |
| 2026-05-17 | Silent natural-language response |
| 2026-05-17 | Clarification TZ drift |
| 2026-05-17 | show_plan lied about sync |
| 2026-05-17 | Fraser claimed lookup intent |
| 2026-05-18 | Pick-days didn't recalibrate |
| 2026-05-18 | Plan showed gym alongside cadence |
| 2026-05-18 | Weekday index case mismatch |
| 2026-05-19 | Chat memory coherence |
| 2026-05-21 | Kobe bridge imports |
| 2026-05-23 | Composer follow-up mode |
| 2026-05-23 | Plan mutations |
| 2026-05-23 | Relative-day WOD lookup |
| 2026-05-23 | Pain/profile slash |
| 2026-05-23 | E2E mesh flows |
| 2026-05-24 | Structured day picks |
| 2026-05-24 | Voice and render |
| 2026-05-24 | Planner single-render |
| 2026-05-24 | Weight grounding directive |
| 2026-05-24 | xagent memory |
| 2026-05-24 | workout_on surfaces WOD |
| 2026-05-24 | plan tools |
| 2026-05-24 | archive fixture hermetic |
| 2026-05-25 | Cool-down yield flag |
| 2026-05-25 | Goal-driven weekly target |
| 2026-05-25 | Pace verdict consistent |
| 2026-05-25 | parse_request negation |
| 2026-05-25 | project_goal_eta |
| 2026-05-25 | walk_nudge daily cap |

**Missing from registry (you should add):**
- 2026-06-08: Bug H — missed workout called "ahead of pace" (synth ignored arbitration verdict).
- 2026-06-09: Bug I — WOD lookup paraphrased + pace fact hallucinated.

---

## 7. The eval layer (your second-most leverage)

`tests/evals/` has four files. Two — adversarial and grounding — are
where you'll add most of your new safety nets.

### `test_adversarial.py` (LAYER 4, 400 LOC)

Probes that a passing run is supposed to make impossible:
- Prompt injection (user message contains "ignore previous
  instructions").
- Persona drift (bot claims to be a different assistant).
- Charter jailbreak (user requests prohibited action).
- PII / secret leakage (response contains an API key fragment).
- Hallucinated math (asks for a calorie figure with no source).
- Trace forgery (user spoofs a trace_id).
- Garbage-in robustness (Unicode soup, empty strings, multi-line).

**Gap:** the corpus is small. You will expand it.

### `test_adversarial.py` lives alongside `adversarial/phrasings.py`

The phrasings module is supposed to mine real messages from the user's
decisions ledger and assert every unique phrasing routes correctly.
**The corpus file is empty.** Bootstrap is in place; mining script is
at `scripts/mine_phrasings.py`. Filling this is one of your hour
blocks.

### `test_scientist_conversation.py` (LAYER 3, 424 LOC)

The "months-long Gemini coaching thread" replay. Source-of-truth for
how Kobe should behave on real coaching scenarios. Strong but not
hallucination-resistant — that's where Bug H and Bug I slipped
through.

### `test_fraser_grounding_evals.py` (LAYER 3, 210 LOC)

Tests that Fraser composes workouts using real 1RMs and constraints.
"Grounding" here means *the output references inputs that exist*.
That's exactly the framework we want for the synthesizer too — which
brings us to your next file.

### **The file you will create:** `test_synthesizer_grounding.py`

Same shape as `test_fraser_grounding_evals.py`, but probing the new
plane's `synthesize()`. Given a facts dict with `arbitration={"rule":
"behind_pace"}` and `recalibration={"summary": "Ahead of pace"}`, the
output MUST contain "behind" and MUST NOT contain "ahead". That kind
of assertion would have caught Bug H.

---

## 8. The adversarial corpus (empty — fill it)

`tests/adversarial/phrasings.py` is the test side. The corpus file at
`tests/adversarial/corpus.json` is missing. To populate:

```bash
RAHAT_TEST_MODE=1 python scripts/mine_phrasings.py \
    --db ~/developer/agency/rahat/vault/rahat.db \
    --output tests/adversarial/corpus.json \
    --since-days 30
```

**Caveat:** this reads the live DB read-only. RAHAT_TEST_MODE=1
prevents writes. The script normalizes (lowercase, whitespace) and
dedupes.

After mining, every entry has `{text, expected_agent, intent, first_seen}`.
The test iterates and asserts:
1. The router picks `expected_agent`.
2. The reply is non-empty and non-stub (no `[LLM-FALLBACK]`).

**Recommend (hour 6 of your plan):** mine, audit, hand-label the top
75 most-frequent phrasings, and add them to the corpus.

---

## 9. Known weak spots (where bugs have shipped)

1. **Synthesizer hallucination.** Synth ignores arbitration verdicts
   (Bug H) and invents tool-call results (Bug I). The synth layer is
   under-tested. Your `test_synthesizer_grounding.py` is the
   high-leverage fix.

2. **Routing typo tolerance.** `_DAY_TOKEN_RE` in the simulator
   doesn't catch "tommorow". Bug I exploited this. Property-based
   fuzzing of the classifier with mutated day tokens is high-value.

3. **Multi-fact prompts.** When Gemini sees pace facts AND a WOD
   facts in the same prompt, it sometimes merges them inappropriately.
   No test currently asserts "if X is asked, the response talks ONLY
   about X". Add one.

4. **Old-plane vs new-plane parity.** `tests/production_parity/`
   exists but is thin. The cutover is real now; this is the layer
   that catches "the migration broke something old Kobe handled
   fine".

5. **Live-mode flags.** `NEW_MIYA_USE_LIVE_DB=1`,
   `NEW_MIYA_NUDGES_ENABLED=1`, `RAHAT_XAGENT_MEMORY=1`. Each flag has
   ≤7 tests. Coverage is thin — you should add at minimum a smoke
   test per flag for the "what happens at minute 60 / day 7 / first
   restart" cases.

6. **Telegram poll loop.** `test_runner_telegram.py` tests the poller
   but not the long-poll timeout, the offset persistence under crash,
   or the multi-message-per-poll handling. Adversarial-style chaos
   here would surface real bugs.

7. **Chat-memory bridge under contention.** Two messages arriving at
   the same chat in the same second. Does the bridge serialize? We
   don't know — no test pins it.

---

## 10. The cassette directory

`tests/cassettes/fraser/` contains recorded LLM responses for offline
replay. Pattern: a test asks "what would Gemini have said?" and the
cassette replays a recorded answer. Saves API budget; makes tests
deterministic.

**You should:**
- Familiarize yourself with the `cassette_helpers.py` module (top
  level of `tests/`).
- When adding eval tests that need LLM responses, record cassettes,
  don't burn live tokens.

---

## 11. The nightly job

`tests/nightly.sh` is invoked by a launchd job at 02:22 local nightly.
It:
1. Stashes uncommitted work.
2. Branches off `origin/main` as `nightly/<date>`.
3. Pops the stash so the suite tests against uncommitted work.
4. Runs `RAHAT_TEST_MODE=1 python -m tests.run_all`.
5. If green: auto-commits in scoped groups (`core/`, `agents/`,
   `tests/`, `specs/`, `root`). Opens PR.
6. If red: restores work to working tree (never lost), commits only
   the report, PR shows failure for triage.
7. If red, agentic auto-fix loop kicks in per `NIGHTLY_PROMPT.md` —
   up to 3 attempts per failing layer.

**Your interaction with the nightly:**
- If you ship a test that fails on a real bug the architect hasn't
  fixed yet, mark it `pytest.xfail` with `reason="blocked by
  <issue>"`. The nightly skip-counts go in the report.
- Don't run the nightly manually unless you're ready to potentially
  commit your branch's stashed work. Read `nightly.sh` first.

---

## 12. The bug-to-test gate

`scripts/check_bug_has_regression_test.py` runs in the pre-push hook.
It greps the diff for any `fix:` commit and looks for a corresponding
`tests/regression_registry/test_YYYY-MM-DD_*.py` file. If absent, the
push is rejected with a message telling you to add one.

**For the test lead:** this means if you write a failing test that
the architect later fixes, you also need to ensure the architect's
fix commit references your test (or adds a new one).

---

## 13. Where to start

1. Open `tests/run_all.py` and read top-to-bottom (20 min).
2. Open `tests/conftest.py` and read it fully (15 min) — pay attention
   to the hermetic block.
3. Open `tests/new_plane/test_runner_delegate_classifier.py` — this
   is the largest, most central test file. Spend 15 minutes
   understanding the regex patterns and how each is tested.
4. Open `tests/regression_registry/test_2026_05_16_kobe_hallucinated_wod.py` —
   the canonical "this is what a registry file looks like" example.
5. Open `new_plane/miya_runner/delegate_classifier.py` — read top-to-
   bottom. You'll be writing property-based tests against this.
6. Open `new_plane/miya_runner/synthesizer.py` — same.

Total reading time: ~90 minutes. Block out hour 1 for this.

Then move to `specs/test_lead/15HR_PLAN.md`.

---

## 14. Things that will trip you up

- **Tests pass on your machine but fail nightly** → you forgot to
  `RAHAT_TEST_MODE=1` (a leak to the live DB) or you import a module
  that touches `vault/`. Read `tests/conftest.py`.

- **`pytest tests/new_plane/` fails but `python -m tests.run_all
  --layer new_plane` doesn't run** → the new plane isn't in the layer
  runner. Add it via `run_all.py` if you grow new-plane testing or
  use direct pytest invocation.

- **A regression test you wrote starts passing without you fixing
  anything** → either the bug got fixed elsewhere (check git log on
  the relevant module) or your test is wrong (probably it).

- **A test fails the first time you run it and passes the second** →
  flake. Almost always due to time-of-day code (`date.today()`) or
  random seed. Pin those.

- **A test silently passes because the import failed** → pytest will
  emit a collection error but still exit 0 if no tests were collected.
  Always check the summary line. `pytest --collect-only` shows what's
  actually being run.

That's the map. Hour 1 of your shift starts now.
