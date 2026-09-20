#!/usr/bin/env python3
"""Load a Bourdain seed file into the vault tables (idempotent).

    .venv/bin/python scripts/bourdain_seed.py private/prds/bourdain/seed_nyc_boston.json

Re-running updates places by id and never duplicates a save. Honours
RAHAT_TEST_MODE=1 (writes go to the per-process sandbox, so a dry run
against the live seed never touches vault/rahat.db):

    RAHAT_TEST_MODE=1 .venv/bin/python scripts/bourdain_seed.py <file> --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    path = Path(args[0])
    if not path.exists():
        print(f"seed not found: {path}")
        return 1
    import os
    if "--dry-run" in argv and os.getenv("RAHAT_TEST_MODE") != "1":
        print("--dry-run needs RAHAT_TEST_MODE=1 (sandboxed DB)")
        return 2
    from agents.bourdain import seed, state
    res = seed.load_seed(path)
    print(f"loaded {res['places']} places, {res['saves']} of them your own "
          f"saves, {res['stays']} stays → {state.db_path()} and "
          f"{state.store_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
