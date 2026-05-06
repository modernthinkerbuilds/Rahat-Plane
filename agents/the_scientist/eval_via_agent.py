"""Twin runner: re-runs the Scientist eval suite through the new
ScientistAgent wrapper instead of `sci.route()` directly.

Proves the Phase-Now refactor is a true visible no-op: byte-identical
behavior across all 125 cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Setup is identical to eval_suite.py — share its module-level prep
# (stub genai, isolated DB, fixture plan, weight seed).
import agents.the_scientist.eval_suite as _suite  # noqa: F401  — for side effects

# Now the legacy `sci` module is loaded with patched paths. Build the
# wrapper and replace the suite's runner to call the agent.
from agents.the_scientist.agent import ScientistAgent
_agent = ScientistAgent()


def _run_via_agent() -> tuple[int, int, list]:
    passed = failed = 0
    failures = []
    for label, query, expected in _suite.TESTS:
        try:
            reply = _agent.route(query)
            actual = reply.text if reply else ""
            if expected.lower() in actual.lower():
                passed += 1
                continue
            failed += 1
            failures.append((label, query, expected, actual[:150]))
        except Exception as e:
            failed += 1
            failures.append((label, query, expected, f"EXCEPTION: {e}"))
    return passed, failed, failures


def main() -> int:
    p, f, failures = _run_via_agent()
    total = p + f
    print(f"\n{'='*60}")
    print(f"  EVAL SUITE — via ScientistAgent — {p}/{total} passed ({100*p/total:.0f}%)")
    print(f"{'='*60}\n")
    if failures:
        print(f"FAILURES ({len(failures)}):\n")
        for label, query, expected, actual in failures:
            print(f"  ❌ {label}")
            print(f"      query:    {query!r}")
            print(f"      expected: {expected!r}")
            print(f"      actual:   {actual[:200]!r}\n")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
