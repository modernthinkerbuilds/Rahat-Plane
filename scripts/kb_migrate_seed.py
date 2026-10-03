#!/usr/bin/env python3
"""Load a seed file into the knowledge base and print the accounting
report (ADR-018 §2.6; Bourdain BR-K6).

    .venv/bin/python scripts/kb_migrate_seed.py private/prds/bourdain/seed_nyc_boston.json

The one intended live write of Phase 1a. Every seed field and meta
block lands somewhere and the report says where; the gate is
"Unaccounted: none". Run it again and facts are appended as a second
observation dated the same, which is visible and harmless. Under
RAHAT_TEST_MODE=1 it writes to the per-process sandbox instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.kb import migrate  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    report = migrate.migrate_seed(argv[1])
    print(migrate.render_report(report))
    if report["unaccounted"]:
        print("\nGATE FAILED: unaccounted meta blocks", report["unaccounted"])
        return 1
    print("\nGATE PASSED: every field accounted for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
