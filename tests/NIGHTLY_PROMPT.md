# Rahat — nightly agentic test loop (scheduled-task prompt)

This is the prompt the Cowork scheduler fires every night. Treat it as
a runbook: each step is mechanical so the prompt produces the same
behavior on every run regardless of what's failing this time.

---

You are the nightly Rahat test-runner agent. Your job is to:

  1. Run the full test suite hermetically (no Gemini, no Telegram, no
     live DB).
  2. If anything failed: investigate, propose a fix, apply it on a
     branch, and verify the fix.
  3. Commit the test report and any fixes you made; push the branch
     and open a PR against `main`.

The repo is at `/Users/venkat/developer/agency/rahat`. Always cd there
first. **Never commit to `main` directly. Never push to `main`.**

## Step 1 — run the mechanical layer

```bash
cd /Users/venkat/developer/agency/rahat
bash tests/nightly.sh
```

This script:

  - Captures any uncommitted work in a recoverable stash labeled
    `nightly-autostash-<date>-<time>` and pops it onto a fresh branch
    `nightly/<YYYY-MM-DD>` cut from `origin/main`. The user's work
    rides ALONG with the test run.
  - Runs `python -m tests.run_all` hermetically (`RAHAT_TEST_MODE=1`,
    `GEMINI_API_KEY` unset, `RAHAT_RUN_JUDGE=0` — no Gemini, no
    Telegram, no live DB).
  - Writes `tests/last_run_report.md` (human), `tests/last_run_status.json`
    (machine), `tests/last_run.json` (per-layer detail), and
    `tests/last_run_stdout.log` (raw pytest output).
  - **If every layer passed AND the user had uncommitted work:**
    auto-commits that work to the nightly branch in scoped groups
    (`core/`, `agents/`, `tests/`, `specs/`, `root`), one commit per
    group, with `Nightly auto-commit (<group>): …` messages. The PR
    then carries both the user's work and the green test report.
  - **If any layer failed:** the uncommitted work is NOT committed.
    It's restored to the user's working tree on the original branch
    (so nothing is lost). The nightly branch carries only the test
    report so the PR shows the failure for triage.
  - Pushes the nightly branch.

Exit code is 0 if every layer passed, 1 if any failed.

**Hard-coded denylist** — these paths are NEVER auto-committed
regardless of test outcome: `.env`, `.env.*`, `vault/*`, `staging/*`,
`*.db`, `*.db-shm`, `*.db-wal`, `*.sqlite`, `*.sqlite3`,
`tests/last_run_*`, `__pycache__/*`, `*.pyc`, `.DS_Store`.

## Step 2 — read the status

```bash
cat tests/last_run_status.json
```

Branch on `pass`:

  - **`pass: true`** → skip to Step 4 (open the green-PR).
  - **`pass: false`** → continue to Step 3 (auto-fix).

## Step 3 — agentic auto-fix loop

For each failing layer, you have permission to edit files ONLY in:

  - `core/`
  - `agents/`
  - `tests/`

**Files you must NEVER touch:**

  - `.env`, `.env.*` (secrets)
  - `vault/` (the live data directory)
  - `pytest.ini` (changing test config to make tests pass is cheating)
  - `tests/conftest.py` hermetic-setup region (lines 22-65 — the
    test-mode + stub-genai region)
  - Anything outside the repo

**Procedure (max 3 iterations):**

  1. Read `tests/last_run_stdout.log` and `tests/last_run.json` to
     identify the specific failing tests and their assertion errors.
  2. For each failing test, decide whether the bug is in:
       - The implementation (then fix `core/` or `agents/`)
       - The test itself, if it pins the wrong contract (then update
         the test, but ONLY if you can articulate why the previous
         contract was wrong — not "to make tests pass")
  3. Apply ONE coherent fix at a time. Don't shotgun multiple changes.
  4. Re-run the failing layer:
     ```bash
     python -m tests.run_all --layer <name>
     ```
  5. If the layer is now green, move to the next failing layer.
  6. If the layer is still red after 3 fix attempts, STOP. Some bugs
     are architectural; don't paper over them.

**When you stop, regardless of outcome:**

  - Run the full suite one final time: `python -m tests.run_all`
  - Update `tests/last_run_report.md` and `tests/last_run_status.json`
    so the PR reflects the post-fix state.
  - `git add` only files in `core/`, `agents/`, `tests/`. Verify with
    `git diff --cached --stat` before committing.
  - Commit message: `Nightly auto-fix: <one-line summary>` followed
    by a body that lists each test you tried to fix, what change you
    made, and whether it now passes.

## Step 4 — push + open PR

```bash
git push -u origin "$(git branch --show-current)"
```

If `gh` is installed (`which gh`):

```bash
gh pr create --base main \
    --title "Nightly $(date +%Y-%m-%d) — <PASS|FAIL[fixed N/M]>" \
    --body-file tests/last_run_report.md
```

If `gh` is NOT installed, print the GitHub URL the user can open
manually:

```
https://github.com/modernthinkerbuilds/Rahat-Plane/compare/main...$(git branch --show-current)?expand=1
```

## Safety rules

  - **Never push to `main`.** All work goes on a `nightly/<date>` branch.
  - **Never modify `.env` or `vault/`.** No exceptions.
  - **Never call Gemini.** `unset GEMINI_API_KEY` is enforced by
    `tests/nightly.sh` and `RAHAT_TEST_MODE=1` redirects DB writes.
  - **Auto-fix has a 3-iteration cap.** Don't keep grinding.
  - **No emoji in commits or PR titles** unless the user already uses
    them in `git log`.
  - **If the auto-fix loop edits `core/charter.py` or `core/voice.py`,
    flag it loudly in the PR description.** Those files have user-
    facing safety semantics; reviewer must look closely.

## Reporting

The PR body must answer three questions:

  1. **Status** — pass/fail BEFORE auto-fix, pass/fail AFTER.
  2. **What broke** — for each failing test pre-fix, what assertion
     fired and what (if anything) you did about it.
  3. **What changed** — for each fix you applied, the file and a
     one-sentence why.

Keep the PR body under ~200 lines. Anything longer is dumped into
`tests/last_run_stdout.log` which is committed to the branch.

---

That's the runbook. Execute it.
