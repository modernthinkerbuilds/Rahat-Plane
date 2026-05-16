#!/usr/bin/env python3
"""Record LLM cassettes for Fraser eval cases.

Usage:
    GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes
    GEMINI_API_KEY=… python -m scripts.record_fraser_cassettes --case fraser_001_hrv33_overhead_swap

Reads `tests/cassettes/fraser/inputs.json` and, for each case, calls
the real Gemini API once via `core.llm.generate` with
`RAHAT_FIXTURE_RECORD=1` so the response is written to disk in the
same shape as `core.llm._save_fixture` (the playback path). After
this script runs once, the test suite stays hermetic — pytest reads
the cassettes via `tests/cassette_helpers.py::replay_cassette`.

Why a script and not a pytest fixture: recording costs real money
and HITS the real wire. Pytest invocations are routine; cassette
recording should be deliberate (run once per system-prompt-version
bump per the ADR-005 doctrine).

Doctrine pins (Day-5 directive):
    • RAHAT_FIXTURE_RECORD=1 + LLM_FIXTURE_DIR both required.
    • Cassette files keyed by sha256(model:prompt)[:16] so a prompt
      change produces a new key — silent prompt drift surfaces as
      "cassette not found", not as a misleading XPASS.
    • Re-record on version bumps. Delete the cassette dir first to
      force full re-record (the directive: "the version constant
      change is the trigger to delete fixtures and re-record").
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INPUTS_PATH = ROOT / "tests" / "cassettes" / "fraser" / "inputs.json"
CASSETTE_DIR = ROOT / "tests" / "cassettes" / "fraser"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Run only this case_id.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be recorded; don't hit the API.")
    parser.add_argument("--force", action="store_true",
                        help="Re-record even if a cassette already exists.")
    args = parser.parse_args()

    if not INPUTS_PATH.exists():
        print(f"ERROR: missing {INPUTS_PATH}")
        return 2
    inputs = json.loads(INPUTS_PATH.read_text())
    cases = inputs.get("cases", [])
    if args.case:
        cases = [c for c in cases if c.get("case_id") == args.case]
        if not cases:
            print(f"ERROR: case_id {args.case!r} not found in inputs.json")
            return 2

    if not args.dry_run and not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY not set. Cassettes need real API "
              "access. Run with --dry-run to preview what would be "
              "recorded.")
        return 3

    # Force record mode in this script's process — env passes through
    # to core.llm.generate.
    os.environ["RAHAT_FIXTURE_RECORD"] = "1"
    os.environ["LLM_FIXTURE_DIR"] = str(CASSETTE_DIR)
    # RAHAT_TEST_MODE must be set for _save_fixture to even consider
    # writing (defense in depth — production never records).
    os.environ.setdefault("RAHAT_TEST_MODE", "1")

    from core import llm  # noqa: E402

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)

    recorded = 0
    skipped = 0
    for case in cases:
        case_id = case.get("case_id")
        prompt = case.get("prompt") or ""
        model = case.get("model")  # None → cio default
        if not case_id or not prompt:
            print(f"  WARN: skipping case with missing case_id/prompt: {case}")
            continue
        key = llm._fixture_key(prompt, model)
        target = CASSETTE_DIR / f"{key}.json"
        if target.exists() and not args.force:
            print(f"  skip   {case_id}  (cassette exists: {target.name})")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  WOULD record {case_id} → {target.name}")
            continue
        print(f"  record {case_id} → {target.name}")
        try:
            usage = llm.generate(
                actor="fraser-cassette-recorder",
                kind="fraser.cassette.record",
                prompt=prompt, model=model)
        except llm.BudgetExceeded as e:
            print(f"    BUDGET-EXCEEDED — actor={e.actor} "
                  f"spent=${e.spent_usd:.4f} of ${e.limit_usd:.4f}")
            return 4
        if usage.error:
            print(f"    ERROR: {usage.error}")
            continue
        # The generate() call already saved the cassette via
        # _save_fixture. Sanity check.
        if not target.exists():
            print(f"    WARN: cassette didn't land at {target}; check "
                  f"LLM_FIXTURE_DIR + RAHAT_FIXTURE_RECORD env.")
            continue
        recorded += 1
    print(f"\nDone. recorded={recorded} skipped={skipped} total={len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
