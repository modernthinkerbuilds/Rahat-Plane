# Regression Registry — The Immutable Floor

Every file in this directory pins ONE named historical bug as a test.
If a test in this directory goes red, an old bug just walked back in.

## Convention

```
test_YYYY-MM-DD_short_bug_name.py
```

Each file MUST start with a docstring containing:

1. The date the bug shipped
2. The symptom the user saw (verbatim if possible)
3. The root cause
4. The fix
5. What this test asserts (the structural or behavioral pin)

## The Rule

**Every `fix:` commit must add at least one file here.**

The pre-merge CI gate (`scripts/check_bug_has_regression_test.py`) greps
the diff. No regression test → no merge. Period.

## Constraints

- **Hermetic.** No real LLM, no real Telegram, no live DB.
- **Fast.** Target <2s per test. The registry runs on every push.
- **Behavior, not implementation.** The assertion is about what the
  user experiences. A refactor that preserves the user experience
  must keep the test green.
- **Skip gracefully.** If a capability isn't wired yet, `pytest.skip()`
  rather than fail. The test becomes a tripwire that lights up when
  the capability lands.

## What's pinned so far

| File | Date | Class | Status |
|------|------|-------|--------|
| `test_2026_05_16_kobe_hallucinated_wod.py` | 2026-05-16 | classifier picks wrong agent | active |
| `test_2026_05_17_slash_bypass_dispatched_to_fraser.py` | 2026-05-17 | slash commands skipped classifier bypass | active |
| `test_2026_05_17_silent_response_natural_language.py` | 2026-05-17 | NL phrasings returned empty | active |
| `test_2026_05_17_clarification_tz_mismatch.py` | 2026-05-17 | TZ drift (Python local vs SQL UTC) | active |
| `test_2026_05_17_show_plan_lies_about_sync.py` | 2026-05-17 | show_plan ignored parse_gym_plan output | xfail until Day 9 Bug 1 |
| `test_2026_05_17_fraser_lookup_intent.py` | 2026-05-17 | Fraser claimed lookup intent | xfail until Day 9 Bug 3 |

## Running

```bash
# Run the registry alone (fast — designed for pre-push hook).
pytest tests/regression_registry -q

# Run with verbose output to see what each test pins.
pytest tests/regression_registry -v

# Run a single bug.
pytest tests/regression_registry/test_2026_05_16_kobe_hallucinated_wod.py -v
```

## What if a test is too restrictive?

Don't relax the test — write a clearer pin. If the original assertion
no longer matches the desired behavior, the system contract changed.
Document the change in a follow-up regression file (`test_YYYY-MM-DD_
revised_X.py`) and deprecate the old one with a comment, not deletion.

## What if a bug is too small to merit a test?

Then it's not a bug — it's a tweak. Bugs that warrant `fix:` commits
warrant a registry entry. If it doesn't, use `chore:` or `refactor:`.
