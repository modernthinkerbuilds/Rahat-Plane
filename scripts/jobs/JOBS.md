# Rahat nightly jobs — operations guide

Four launchd jobs run every night on your Mac. Together they catch
regressions, keep main moving, sweep cruft, and propose new tests.

## Schedule at a glance

| Time  | Job          | What it does                                                   | Touches main? |
|-------|--------------|----------------------------------------------------------------|---------------|
| 23:00 | regression   | Runs `python -m tests.run_all` hermetically; writes the report | no            |
| 23:30 | greenstreak  | If suite passed, commits uncommitted work + pushes main        | yes           |
| 00:30 | hygiene      | Sweeps caches, vacuums DB, prunes branches, rotates logs       | no            |
| 02:00 | evolve       | (Every other day) Scans recent commits, proposes new tests     | branch only   |

## What each job is for

### regression — the canary
Runs the full 5-layer suite (unit, contract, eval, adversarial, regression)
in hermetic mode (`RAHAT_TEST_MODE=1`, no Gemini, no Telegram). Writes:

- `tests/last_run_report.md` — human summary
- `tests/last_run_status.json` — `{pass: true|false, layers: [...]}` machine read
- `tests/last_run.json` — per-layer pass/fail/skip counts
- `tests/last_run_stdout.log` — full pytest output for triage
- `vault/jobs/regression.log` — this script's own activity

Exit code is 0 only if every layer was green.

### greenstreak — the contribution-graph keeper
Reads `tests/last_run_status.json`. If `pass: true`:
1. Fast-forward main to `origin/main`
2. Stage every modified/untracked file MINUS the denylist
3. Commit one group at a time (`core`, `agents`, `tests`, `specs`, `profile`, `bridges`, `scripts`, `root`)
4. Commit the test-report files in their own tiny "daily heartbeat" commit
5. Push main

If `pass: false` it bails immediately — red builds never land on main.

**Knobs** (env vars set in the plist):
- `GREENSTREAK_DOCS_ONLY=1` — only commit docs/tests/specs paths to main; code changes get skipped (defaults to 0 = aggressive, per your 2026-05-09 choice)
- `GREENSTREAK_PUSH=0` — commit locally without pushing (defaults to 1)

**Hard denylist** — never auto-committed regardless of any setting:
`.env`, `.env.*`, `vault/*`, `staging/*`, `*.db*`, `*.sqlite*`,
`tests/last_run_*` (in normal commits — they get their own heartbeat
commit), `__pycache__/*`, `*.pyc`, `.DS_Store`.

### hygiene — the janitor
Idempotent cleanup. Touches the filesystem only:
- Sweeps `__pycache__/`, `*.pyc`, `.DS_Store`
- Sweeps Cowork-sandbox leftovers in `.git/` (`.lock.cleared.*`, `tmp_obj_*`)
- `VACUUM`s `vault/rahat.db` to reclaim pages
- Rotates `vault/jobs/*.log` files larger than 5 MB
- `git fetch --prune` then deletes local branches merged into main
- `git gc --auto`
- If last regression was red, drops a marker file `vault/jobs/ALERT_REGRESSION_RED`

### evolve — the test author (scaffold only)
Currently writes a proposal file listing files that changed in the last
48h that may need new tests. Does NOT yet auto-author tests or open PRs —
that's the next session's work. Safe to install but it's a no-op until
someone wires up the LLM author step.

## Install

```bash
cd ~/developer/agency/rahat
bash bootstrap.sh                                              # renders templates
cp scripts/jobs/com.rahat.regression.plist  ~/Library/LaunchAgents/
cp scripts/jobs/com.rahat.greenstreak.plist ~/Library/LaunchAgents/
cp scripts/jobs/com.rahat.hygiene.plist     ~/Library/LaunchAgents/
cp scripts/jobs/com.rahat.evolve.plist      ~/Library/LaunchAgents/
for j in regression greenstreak hygiene evolve; do
    launchctl load ~/Library/LaunchAgents/com.rahat.$j.plist
done
```

## Uninstall / disable one

```bash
# Pause without uninstalling
launchctl unload ~/Library/LaunchAgents/com.rahat.<job>.plist

# Permanent removal
launchctl unload ~/Library/LaunchAgents/com.rahat.<job>.plist
rm ~/Library/LaunchAgents/com.rahat.<job>.plist
```

## Run one manually

Useful for testing a change before installing the plist:

```bash
bash scripts/jobs/regression.sh    # canary
bash scripts/jobs/greenstreak.sh   # auto-commit (will push if main passes!)
bash scripts/jobs/hygiene.sh       # safe — never touches commits
bash scripts/jobs/evolve.sh        # safe — currently scaffold only
```

To run greenstreak without pushing (local-only commit):
```bash
GREENSTREAK_PUSH=0 bash scripts/jobs/greenstreak.sh
```

## When something fails at 2 AM

**1. Check which job complained:**
```bash
ls -lt vault/jobs/
# Look for the most recent .log; tail it.
tail -50 vault/jobs/regression.log     # or greenstreak / hygiene / evolve
```

**2. If `regression` failed:**
- `tests/last_run_stdout.log` — full pytest output, scroll to the FAILURES section
- `tests/last_run_status.json.failed_layers` — names the broken layer(s)
- Re-run just that layer: `RAHAT_TEST_MODE=1 venv/bin/python -m tests.run_all --layer <name>`

**3. If `greenstreak` failed:**
- Most likely cause: push rejected because someone else pushed to main between regression and greenstreak. Pull and let it retry tomorrow.
- Second most likely: a denylisted path snuck in — check `vault/jobs/greenstreak.log` for `DENY:` lines.

**4. If `hygiene` failed:**
- Almost always a `git fetch` failure (offline). Safe to ignore — it'll retry tomorrow.
- If `git gc` is the culprit, you have a corrupted .git/ — that's a real problem, not a hygiene problem.

**5. The `ALERT_REGRESSION_RED` marker:**
- Hygiene drops `vault/jobs/ALERT_REGRESSION_RED` whenever the last
  regression run was red. Existence of this file = "you have a broken
  test that's been broken for at least one day." Wire a notifier (email,
  Telegram nudge) to read this file and alert.

## Risk posture (read me)

You chose "everything to main if all 5 layers green" on 2026-05-09. That
means greenstreak.sh will push CODE to main whenever the suite passes,
not just docs. The suite quality is now the only gate. Two consequences:

1. **A bug the suite doesn't catch will reach main without review.** This
   is mitigated by: (a) hardening the suite continuously via `evolve` once
   that's wired up, and (b) the adversarial layer's hard refusal floor.
2. **A flaky test that fires red on a slow night blocks all autocommit.**
   That's the right failure mode — better than a flake silently letting
   bad code through.

If you ever feel the risk is wrong, flip `GREENSTREAK_DOCS_ONLY=1` in
the greenstreak plist — code changes will then go through your normal
manual review and only docs/tests/specs auto-merge.

## Logs to grep when triaging a regression you discover later

```bash
# What did the suite say last week?
ls -1 vault/jobs/regression.log* | head
# Which tests failed on 2026-05-08?
git log --all --oneline -- tests/last_run_status.json | head
git show <sha>:tests/last_run_status.json
```

The `last_run_*` files are committed by greenstreak as the daily
heartbeat — every day's status is preserved in the commit graph, so
you can replay history.
