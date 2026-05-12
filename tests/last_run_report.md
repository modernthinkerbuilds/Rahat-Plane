# Rahat test report — ✅ PASS (Nightly 2026-05-12)

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | ✅ | 28 | 0 | 0 | 0.15s |
| `contract` | ✅ | 40 | 0 | 0 | 0.26s |
| `eval` | ✅ | 43 | 0 | 1 | 0.23s |
| `adversarial` | ✅ | 14 | 0 | 0 | 0.26s |
| `regression` | ✅ | 17 | 0 | 0 | 0.18s |
| **total** | ✅ | **142** | **0** | **1** | **1.07s** |

## Layers
- **unit** — Pure-function unit tests (voice, cost, helpers, no I/O).
- **contract** — Agent ABI + Charter ABI + decisions-ledger invariants.
- **eval** — Scenario-fidelity evals against the Sports Scientist.
- **adversarial** — Prompt injection / jailbreak / PII / hallucination probes.
- **regression** — Replay regression — golden fixtures vs. live router.

> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in `tests/conftest.py`. No test can write to `vault/rahat.db`.

## Nightly 2026-05-12 status — git ceremony deferred

The hermetic test suite ran cleanly (142 passed, 1 skipped, ~1.07s wall-clock).
However, the `tests/nightly.sh` git ceremony (stash → branch off `origin/main` →
auto-commit grouped uncommitted work → push → open PR) could **not** be executed
from the scheduled-task sandbox because the working-copy fuse mount refuses
`unlink(2)`. A previous attempt at 09:22 UTC left a stale `.git/index.lock`
in place; the sandbox cannot remove it, so any operation that touches the
ref/index lock pathway fails ("Unable to create … index.lock: File exists").

Current working-tree state (preserved untouched, nothing was lost):

- Branch: `nightly/2026-05-11` (left over from yesterday's PASS run)
- Modified: `agents/the_scientist/handler.py`,
  `scripts/register_telegram_commands.py`,
  `tests/test_handler_regressions.py`
- Untracked: `specs/Rahat_NextTen.docx`, `specs/Rahat_PRD_v2.docx`,
  `specs/Rahat_PRD_v2.md`
- Test reports refreshed: `tests/last_run_report.md`,
  `tests/last_run_status.json`, `tests/last_run.json`,
  `tests/last_run_stdout.log`

### What you need to do manually (one-time)

From a normal shell (not the sandbox) on the user's mac, run:

```bash
cd /Users/venkat/developer/agency/rahat
rm -f .git/index.lock .git/packed-refs.lock .git/refs/heads/*.lock
find .git/objects -name 'tmp_obj_*' -delete
bash tests/nightly.sh        # will re-run, stash, branch, commit, push
```

The runbook is at `tests/NIGHTLY_PROMPT.md`. Since the tests are already
green, the script's pass branch should fire and produce
`nightly/2026-05-12` with the auto-committed work + a green PR against `main`.

### Why this happened

The scheduled-task runtime placed this job inside a sandbox whose mount of
the repo allows `creat(2)` / `write(2)` but blocks `unlink(2)`. `git` cannot
release its own lock files in that mode, so every operation that needs to
move HEAD, write refs, or rotate the index halts. The test runner itself
only reads source files (and writes report artifacts), so the suite passed.

Filed as a follow-up: the nightly job should either run outside the cowork
sandbox, or `nightly.sh` should detect the unlink restriction up front and
abort early with a clear message instead of half-running.
