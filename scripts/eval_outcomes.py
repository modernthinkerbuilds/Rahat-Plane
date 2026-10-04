#!/usr/bin/env python3
"""scripts/eval_outcomes.py — replay an agent's golden set and score it
by outcomes (ADR-018 §2.10).

    RAHAT_TEST_MODE=1 .venv/bin/python scripts/eval_outcomes.py --agent bourdain --mode stub
    RAHAT_TEST_MODE=1 .venv/bin/python scripts/eval_outcomes.py --agent bourdain --mode replay --save
    RAHAT_TEST_MODE=1 RAHAT_FIXTURE_RECORD=1 .venv/bin/python scripts/eval_outcomes.py \\
        --agent bourdain --mode live --cassettes private/cassettes/bourdain --save      # records

Always under RAHAT_TEST_MODE=1: the run builds its own sandbox knowledge
base from the seed (never the live vault/rahat.db). `--mode live` calls
the model and spends money; with RAHAT_FIXTURE_RECORD=1 it writes the
cassettes that `--mode replay` later needs. Exit code 1 when any must
fails (the nightly job's auto-commit gate), 2 on a setup error.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

if os.environ.get("RAHAT_TEST_MODE") != "1":
    print("refusing to run without RAHAT_TEST_MODE=1 (the eval builds its own sandbox knowledge base)", file=sys.stderr)
    sys.exit(2)

if "live" in sys.argv:
    # The model key for --mode live has to be in the environment BEFORE core.io
    # is imported (it reads GEMINI_API_KEY at import). Test mode still sandboxes
    # the live DB; only the key comes from .env.
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO / ".env")
    except ImportError:
        pass

from core import eval_outcomes as ev  # noqa: E402
from core import manifest as cmanifest  # noqa: E402

DEFAULT_SEEDS = {"bourdain": "private/prds/bourdain/seed_nyc_boston.json"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="bourdain")
    ap.add_argument("--mode", choices=("stub", "replay", "live"), default="stub")
    ap.add_argument("--set", action="append", default=None, help="golden set path(s); default: the manifest's")
    ap.add_argument("--seed", default=None, help="the seed file for the sandbox knowledge base")
    ap.add_argument("--cassettes", default=None, help="cassette dir (default: private/cassettes/<agent>)")
    ap.add_argument("--case", action="append", default=None, help="run only these case ids")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--k", type=int, default=1, help="pass^k: every sample must pass")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--save", action="store_true", help="write the report to vault/eval_runs/")
    ap.add_argument("--runs-dir", default=None)
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--db", default=None, help="sandbox db path (default: a temp file)")
    args = ap.parse_args(argv)

    if args.mode == "live":
        from core import io as cio
        if cio.llm_client() is None:
            print("no GEMINI_API_KEY in the environment or .env; --mode live needs it", file=sys.stderr)
            return 2
        if os.environ.get("RAHAT_FIXTURE_RECORD") != "1":
            print("note: --mode live without RAHAT_FIXTURE_RECORD=1 spends money and records nothing", file=sys.stderr)
    manifest = cmanifest.load(args.agent)
    sets = args.set or manifest.golden_sets
    seed = args.seed or DEFAULT_SEEDS.get(args.agent)
    if not seed or not Path(seed).exists():
        print(f"no seed file for {args.agent} ({seed}); pass --seed", file=sys.stderr)
        return 2
    cassettes = args.cassettes or str(_REPO / "private" / "cassettes" / args.agent)
    os.environ.setdefault("RAHAT_CASSETTE_DIR", cassettes)
    if args.mode == "replay" and not Path(cassettes).exists():
        print(f"no cassettes at {cassettes}: record with --mode live and RAHAT_FIXTURE_RECORD=1 first", file=sys.stderr)
        return 2
    db_path = args.db or os.path.join(tempfile.gettempdir(), f"rahat_eval_{args.agent}_{os.getpid()}.db")
    cases = [c for s in sets for c in ev.load_golden(s)]
    if not cases:
        print("no cases", file=sys.stderr)
        return 2
    first_when = ev.when_of(cases[0])
    report_m = ev.prepare_db(seed, db_path=db_path, now=first_when)
    print(f"sandbox kb: {report_m.get('places')} places, {report_m.get('facts')} facts from {seed}")
    from importlib import import_module
    tenant = import_module(f"agents.{args.agent}.harness").tenant()
    rep = ev.run_set(tenant, cases, mode=args.mode, db_path=db_path, cassette_dir=cassettes, set_path=",".join(sets),
                     judge_enabled=not args.no_judge, only=set(args.case) if args.case else None, limit=args.limit, k=args.k,
                     progress=(lambda line: print(line, flush=True)) if args.verbose else None)
    print(rep.render(verbose=args.verbose))
    if args.save:
        path = ev.save_report(rep, args.runs_dir or ev.default_runs_dir())
        print(f"saved {path}")
    return 0 if rep.passed == rep.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
