# Scope Boundaries — What You CAN and CAN'T Fix

You are the Test Lead, not the Architect. The line between those two
roles is rigid because we tried doing both and got slower, not
faster. Read this file before you start the 15-hour plan.

---

## Rule of thumb

> "If a fix changes user-facing behavior, the architect ships it.
>  If a fix changes how we *verify* user-facing behavior, you ship it."

When in doubt, write the failing test, file the proposed fix in
`PROPOSED_FIXES.md`, and move on.

---

## What you CAN touch

### Directories you fully own

```
tests/                              # all of it
specs/test_lead/                    # your workspace; create freely
scripts/mine_phrasings.py           # corpus mining (read-only DB access)
```

### Directories you can read but not write (with one exception)

```
agents/                             # READ ONLY (architect-thread territory)
core/                               # READ ONLY (architect-thread territory)
new_plane/                          # READ ONLY (architect-thread territory)
specs/                              # READ ONLY except specs/test_lead/*
```

**Exception:** the architect's specs sometimes reference test names
that don't exist yet. If you create those tests, you may add cross-
reference links in `specs/test_lead/findings/*` — never in the
architect's spec files themselves.

---

## What you CAN do

| Action | Where | Notes |
|---|---|---|
| Add new test files | `tests/`, `tests/new_plane/`, `tests/evals/`, `tests/regression_registry/`, `tests/adversarial/` | Use the existing layer conventions |
| Add new fixtures | `tests/fixtures/`, `tests/cassettes/` | If you record a cassette, do it once and check in |
| Add new corpora | `tests/adversarial/corpus.json`, etc. | Generated artifacts go in dedicated subdirs |
| Modify your own previous tests | `tests/**/test_<your-additions>.py` | Refactor away |
| Rewrite a test's docstring | Anywhere | Improves discoverability — no behavioral risk |
| Mark a test `pytest.xfail` | Anywhere | Use when a real bug is surfaced but not yet fixed by architect |
| Mark a test `pytest.skip` | Anywhere | Use rarely; always with `reason="blocked-by: <issue>"` |
| Add `# blocked-by: <issue>` comments | Anywhere in `tests/` | Helps the architect prioritize |
| Run scripts under `scripts/` | If they're read-only against vault/ | Mining, analysis, coverage |
| Create new files in `scripts/` | Yes — analytics, mining, coverage report generators | Don't put production logic here |
| Update `tests/README.md` | Yes | But only for testing policy / how-to-add-tests guidance |
| Add per-layer README files | `tests/new_plane/README.md` etc. | New documentation welcome |
| Improve test naming, organization | Anywhere in `tests/` | But preserve the registry naming convention |
| Add Hypothesis property-based tests | Anywhere | `pip install hypothesis`; check it into requirements if new |
| Add coverage reports | `specs/test_lead/findings/COVERAGE_AUDIT.md` | Concrete file:line gaps |
| Update test docstrings | Anywhere | Make them describe the *contract*, not the *implementation* |

---

## What you CAN'T do

| Action | Why | What to do instead |
|---|---|---|
| Modify `agents/the_scientist/*.py` | KTLO architect's territory | Write a failing test; file proposed fix |
| Modify `agents/fraser/*.py` | Architect's territory | Same |
| Modify `core/*.py` | Architect's territory | Same |
| Modify `new_plane/miya_runner/*.py` (the implementation) | New-plane architect's territory | Same |
| Modify the hermetic region of `tests/conftest.py` | This is the live-DB safety guard | Don't touch; document concerns in findings |
| Add real `GEMINI_API_KEY` to any test environment | Tests must be deterministic + hermetic | Use cassettes for recorded LLM responses |
| Delete a registry test | Once pinned, always pinned | If obsolete, mark `pytest.skip("reason: superseded by <new test>")` |
| Modify `tests/last_run_*` (status, report) | These are runner output artifacts | Let the runner regenerate them |
| Commit `.env`, `vault/*.db`, `staging/*` | Hard denylist in `.gitignore` | If gitignored ever fails, escalate immediately |
| Push to `main` | Branch-protected | Push your branch; open PR |
| `git push --force` | Could lose architect's concurrent work | Rebase locally, then `git push` plain |
| Run the nightly job (`tests/nightly.sh`) for testing | It auto-commits + opens PRs | Use direct pytest invocation for iteration |
| Change `pytest.ini`, `pyproject.toml` test config | Coordinated change | Propose in `PROPOSED_FIXES.md` |
| Modify the bug-to-test pre-push hook (`scripts/check_bug_has_regression_test.py`) | Process enforcement | Propose in findings |
| Touch `agents/the_scientist/handler.py` to "fix" a bug | Even if you see it, it's not yours | Test it failing; the architect commits the fix |
| Add LLM-as-judge tests that aren't gated by `RAHAT_RUN_JUDGE=1` | Burns budget; nondeterministic | Always gate them |
| Add tests that depend on real Telegram polling | Hermetic-violation | Use the captured outbox |

---

## How to file a proposed fix

When you find a bug, write the failing test, then add an entry to
`specs/test_lead/findings/PROPOSED_FIXES.md`:

```markdown
## PF-2026-06-10-001 — <short slug>

**Symptom (test name):**
`tests/new_plane/test_<file>.py::<TestClass>::<test_method>`

**Reproduction:**
```
RAHAT_TEST_MODE=1 pytest tests/new_plane/test_<file>.py::<test_method> -v
```

**Root cause hypothesis:**
[where you think the bug is — file:line]

**Proposed fix:**
[concrete patch description — not code, but enough that the architect
can implement in 30 minutes]

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_<slug>.py`

**Status:**
- [ ] Test added (xfail until fixed)
- [ ] Architect notified
- [ ] Fix shipped (commit hash)
- [ ] Registry entry created
- [ ] xfail removed; test now green
```

The architect picks these up. **Don't try to fix them yourself.** A
test lead who edits implementation code is impossible to evaluate —
when something breaks, we don't know if it's your fix or the
architect's.

---

## How to mark a test xfail correctly

```python
import pytest

@pytest.mark.xfail(
    reason="blocked-by: PF-2026-06-10-001 — synth-prompt arbitration weak",
    strict=True,
)
def test_arbitration_overrides_recalibration_summary():
    ...
```

`strict=True` means the test will FAIL the run if it suddenly passes —
that's the architect's signal to remove the xfail marker.

---

## How to mark a test skipped correctly

Only when the dependency literally doesn't exist:

```python
import pytest

@pytest.mark.skip(
    reason="blocked-by: feature flag not yet implemented (PF-2026-06-10-007)"
)
def test_concurrent_chat_memory_writes():
    ...
```

Always pair with a PROPOSED_FIXES.md entry. The skip should be
removable by someone reading the PROPOSED_FIXES.md.

---

## Gray-zone cases

### "The test setup needs a new helper fixture in conftest.py"

OK if it's a new fixture in `conftest.py` that doesn't touch the
hermetic region. Add at the bottom of the file. Document in the
fixture's docstring why it exists.

### "I want to mine the live DB"

Read-only access via SQLite URI: `file:...?mode=ro`. NEVER pass
`mode=rw` or default. The mining script template in your 15HR plan
hour 4 shows the pattern.

### "I want to add an integration test that touches the live Gemini API"

Don't. Use cassettes. If a cassette doesn't exist, record one once
(under your own GEMINI_API_KEY, separately, not in CI) and check
it in.

### "I want to refactor an existing test that's wrong"

OK if:
- It's not a registry test (those are pinned forever).
- You document the original behavior in the test's git blame or
  docstring.
- The refactor preserves what the test asserted previously.

NOT OK if it changes what's being asserted. That's a behavior change.

### "I want to delete a flaky test"

Don't. Find the flakiness root cause and pin it (random seed,
date-of-day mock, etc.). If you genuinely believe the test is
worthless, `pytest.skip("worthless — see PF-2026-06-10-NNN")` and
file the PF for the architect to review.

### "I think the architecture is wrong"

You may be right. File it in `findings/ARCHITECTURE_CONCERNS.md` with
specific file:line refs and a proposed alternative. The architect
reads it and decides. **Do not refactor production code.**

---

## End-of-shift verification

Before opening your PR:

```bash
# 1. All layers green
RAHAT_TEST_MODE=1 python -m tests.run_all
test $? -eq 0 || { echo "RED — fix or xfail before opening PR"; exit 1; }

# 2. No untracked files except your findings
git status --porcelain | grep -v 'specs/test_lead/findings/' \
    | grep -v 'tests/' || echo "clean except expected"

# 3. No real API keys committed
grep -r 'AIza' . --include='*.py' --include='*.md' \
    --exclude-dir='.venv' --exclude-dir='node_modules' \
    && { echo "FOUND API KEY IN COMMITS — REMOVE NOW"; exit 1; } \
    || echo "no api keys leaked"

# 4. No vault/ writes
ls -la vault/rahat.db | head -1
# Compare to before-shift modification time; should match
```

If all four pass, push your branch and open the PR.

---

## What "great" looks like (repeated from PROMPT.md for emphasis)

- 200+ new tests
- 8+ documented coverage gaps
- 2+ new registry entries (Bug H, Bug I)
- 1 new eval harness (synthesizer grounding)
- 1 new adversarial corpus populated with ≥75 real phrasings
- Suite still green
- Findings doc gives the next person a clear runway

You've got 15 hours. Go.
