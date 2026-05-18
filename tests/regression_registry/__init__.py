"""Regression Registry — the immutable floor.

Every file in this directory pins ONE named historical bug as a test.
Naming convention: YYYY-MM-DD_short_bug_name.py.

CONTRACT (enforced by scripts/check_bug_has_regression_test.py):
    Every commit with a "fix:" prefix MUST add at least one file
    under this directory. The pre-merge gate greps the diff. No
    regression test → no merge. Period.

WHY:
    Test suites historically passed for the wrong reasons (sandbox vs
    macOS TZ drift, canonical phrasings only, agent stubs that looked
    like answers). This directory is the ONE place where every test
    is anchored to a real bug that shipped to production at least once.

    If a test in this directory goes red, an old bug just walked back
    in. The pre-push hook blocks the push. The CI gate blocks the
    merge. No exceptions.

CONVENTIONS:
    - Each file has a top-of-file docstring naming the bug, the date
      it shipped, and the symptom the user saw.
    - Tests must be hermetic (no real LLM, no real Telegram, no live DB).
    - Tests must run in <2s each.
    - Tests pin BEHAVIOR not implementation — the assertion is about
      what the user experiences, not how the code is structured.
    - When a fix is in flight, use @pytest.mark.xfail(strict=True,
      reason="...") so the test passes-as-xpassed when the fix lands
      and the marker is removed.

RUNNING:
    pytest tests/regression_registry -v       # local
    pytest tests/regression_registry -q       # CI
"""
