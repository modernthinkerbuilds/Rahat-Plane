"""tests.scientist — Sports Scientist agent's standalone eval suites.

These are NOT pytest-shaped tests (no `def test_*` functions). They're
deterministic-input regression scripts that the user runs directly:

    python3 -m tests.scientist.eval_suite
    python3 -m tests.scientist.eval_reasoner

They were originally at `agents/the_scientist/eval_*.py`; moved here in
2026-05 (R2 of specs/ARCH_REVIEW_2026-05-08.md) so the agents/ tree
contains only agent code. Pytest auto-discovery (test_*.py pattern)
intentionally skips them.

Wiring them into the five-layer test runner is a deferred refactor —
each file would either become a pytest layer (or be rewritten as proper
`def test_` functions). For now they remain runnable scripts.
"""
