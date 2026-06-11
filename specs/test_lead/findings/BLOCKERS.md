# Blockers — 2026-06-10 agent shift

## B-01 — sandbox venvs are macOS-only (worked around)
The committed `.venv`/`venv` reference Homebrew pythons that don't exist
on the Linux test host. Built `/tmp/rvenv` on system Python 3.10.12.
Not a repo bug; the user's Mac venv is fine. No action needed.

## B-02 — 2 new_plane files fail collection in sandbox (env, not suite)
`tests/new_plane/test_adapter_integration.py` and
`test_openclaw_adapter.py` raise `RuntimeError: The starlette ...` at
collection under this sandbox's starlette/pytest-9 combo (FastAPI
`TestClient` construction). Both are HTTP-adapter / OpenClaw tests,
outside test-lead scope. I run new_plane with `--ignore` for these two.
Architect note: pin `starlette`/`httpx` in `requirements-dev.txt` or
gate `TestClient` construction so collection doesn't hard-fail on a
version skew. Does not affect any test I add.

## B-03 — `git push` has no credentials in sandbox (commit locally)
`git push origin test-lead-agent-2026-06-10` →
`could not read Username for 'https://github.com'`. The sandbox has no
GitHub auth. I commit every hour locally so the branch is intact; the
user/architect pushes it. Per AGENT_PROMPT §10, with no `gh`/push I
write the PR title+body to `findings/PR_DESCRIPTION.md` at hour 15.
Also: `.git/objects` carries files owned by the Mac user, so git prints
"Operation not permitted" on temp-object unlink — cosmetic; commits
land (verified via `git log`/`git show --stat`).

## B-04 — `new_plane/` is untracked in git (pre-existing)
The whole `new_plane/` tree (plus some scripts/specs) is untracked
working-tree content, not committed to `main`. Tests import it fine
from disk. All my deliverables live under tracked `tests/` and
`specs/test_lead/`, so `git diff origin/main` reflects exactly my test
additions. No action.
