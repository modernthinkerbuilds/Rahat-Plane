# Rahat — test suite

The Rahat control plane is tested in five layers. Each layer is one
file or one directory under `tests/`, runs offline, and is wired
together by `tests/run_all.py`.

| # | Layer       | Where                                       | What it proves |
|---|-------------|---------------------------------------------|----------------|
| 1 | unit        | `tests/test_voice.py`                       | Pure functions: voice dressing, idempotency, neutrality. |
| 2 | contract    | `tests/test_miya_routing.py`, `tests/test_charter_policies.py` | Agent ABI + Charter ABI invariants — registry, route(), tick(), policy fan-out, governance_log. |
| 3 | eval        | `tests/evals/test_scientist_conversation.py` | Scenario fidelity against the months-long Gemini coaching thread (PDF). Burn math, HRV bands, plan ops, weight timeline, coaching protocols. |
| 4 | adversarial | `tests/evals/test_adversarial.py`           | Prompt injection, persona drift, charter jailbreaks, PII/secret leakage, hallucinated math, trace forgery, garbage-in robustness. |
| 5 | regression  | `tests/test_replay_regression.py`           | Replay golden inputs through the live router; diff against frozen fixtures (`tests/fixtures/replay_golden.json`). |

## Running

```
# everything, with a markdown report at tests/last_run_report.md
python -m tests.run_all

# one layer
python -m tests.run_all --layer eval

# bypass the optional LLM-as-judge probes (default-off anyway)
python -m tests.run_all --no-llm-judge

# direct pytest works too
pytest -q
pytest tests/evals/test_adversarial.py
```

## Hermetic guarantees

Per `tests/conftest.py`:

- `RAHAT_TEST_MODE=1` is forced before any rahat module imports — see `core/io.py:_resolve_db_path`. The live `vault/rahat.db` is NEVER touched, even when a test passes a path that points at it. This guard exists to prevent the 2026-05-08 corruption class of incident from recurring.
- `google.genai` is stubbed with a `_StubClient` that returns a stable `[LLM-FALLBACK]` marker. Tests that need a controlled response use the `fake_llm` fixture (rule-based mock).
- Telegram I/O is replaced by an in-memory `captured_tg.outbox`. No test ever hits the wire.
- Each test starts with an empty Miya registry (`autouse` fixture in conftest).
- Each test gets its own SQLite file when it asks for `sandbox_db`.

A clean `pytest -q` should produce zero network calls and zero writes to `vault/rahat.db`. CI fails the build if either is observed.

## Adding new tests

- **A coaching call you want memorialized?** Add a record to `tests/fixtures/replay_golden.json`. The regression layer will then enforce that the live router reproduces it indefinitely.
- **A new policy in the Charter?** Add a class to `tests/test_charter_policies.py` covering both the positive (it fires when it should) and the negative (it doesn't fire when it shouldn't) paths.
- **A new agent under Miya?** Add a synthetic agent to `tests/test_miya_routing.py` and pin the four routing properties (single-match skips LLM, multi-match goes through classifier, zero-match goes through full mesh, garbage-LLM falls back to first candidate).
- **A safety probe?** Add to `tests/evals/test_adversarial.py`. Anything that regresses a probe is a P0 — the Charter or an agent guard is broken and the change must revert before merge.

## Nightly agentic run

A Cowork scheduled task fires at 02:22 AM local every night. It:

  1. Runs `tests/nightly.sh` — stashes any uncommitted work, branches off `origin/main` as `nightly/<date>`, **pops the stash onto the nightly branch so the suite tests against the uncommitted work**, runs the full hermetic suite (`RAHAT_TEST_MODE=1`, `GEMINI_API_KEY` unset, `RAHAT_RUN_JUDGE` off).
  2. **If every layer passed and uncommitted work was present:** auto-commits that work to the nightly branch in scoped groups (`core/`, `agents/`, `tests/`, `specs/`, `root`) with `Nightly auto-commit (<group>): …` messages, then commits the report. PR carries both your work and the green report.
  3. **If anything failed:** the uncommitted work is restored to your working tree (never lost) and only the report is committed to the nightly branch. PR shows the failure for triage.
  4. Hard-coded denylist for auto-commit: `.env`, `.env.*`, `vault/*`, `staging/*`, `*.db`, `*.db-shm`, `*.db-wal`, `*.sqlite*`, `tests/last_run_*`, `__pycache__/*`, `*.pyc`, `.DS_Store` — these paths are never touched regardless of test outcome.
  5. Reads `tests/last_run_status.json`. If any layer failed, the agentic auto-fix loop kicks in.
  6. The auto-fix agent reads the failing test output, attempts a fix in `core/`, `agents/`, or `tests/` (other paths off-limits), re-runs the failing layer, and iterates up to 3 times per layer. `.env`, `vault/`, `pytest.ini`, and the hermetic region of `conftest.py` are explicitly off-limits.
  7. Opens a PR against `main` titled `Nightly <date> — PASS` or `Nightly <date> — FAIL[fixed N/M]`. Never pushes to `main` directly.

The runbook the agent follows is `tests/NIGHTLY_PROMPT.md`. Edit it to change behavior; the scheduled task re-reads it on every run.

To pause the schedule, open the Scheduled section in Cowork's sidebar and disable `rahat-nightly-tests`. To run on demand, invoke `bash tests/nightly.sh` directly — that does the mechanical layer without spawning the auto-fix agent.

## LLM-as-judge (optional)

The `eval` layer ships with a `TestLLMJudge` class that runs scenario
replies through Gemini Flash using a 1-5 rubric (factual, tone,
actionable). It's gated behind two env vars:

```
GEMINI_API_KEY=...      # real key, not the stub
RAHAT_RUN_JUDGE=1
```

Default is OFF so CI stays free and deterministic. When enabled, judge
scores below 3 are surfaced as test failures.
