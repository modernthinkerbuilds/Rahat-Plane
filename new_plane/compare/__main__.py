"""CLI for the comparison harness.

Usage::

    # Run a single prompt
    python -m new_plane.compare "what's my plan today"

    # Run several prompts (one arg per prompt)
    python -m new_plane.compare \\
      "what's my plan today" \\
      "when will I hit 196" \\
      "design me a workout for thursday" \\
      "where am I on pace"

    # Run prompts from a file (one per line)
    python -m new_plane.compare --from prompts.txt

    # Save markdown report (defaults to private/eval-runs/<ts>.md)
    python -m new_plane.compare --out report.md "..."

    # Print as JSON (machine-readable)
    python -m new_plane.compare --json "..."

The default destination is `private/eval-runs/` which is gitignored.
The 8-week gate evidence lives there.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from new_plane.compare.harness import (
    compare_many, render_report, save_report,
)

_DEFAULT_PROMPTS = [
    "what's my plan today",
    "where am I on pace",
    "when will I hit 196",
    "design me a workout for tomorrow",
    "should I take Saturday off",
    "compare today vs Friday",
]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m new_plane.compare",
        description="Side-by-side old-Miya vs new-Miya comparison harness.",
    )
    parser.add_argument(
        "prompts", nargs="*",
        help="prompts to compare (omit to use a default 6-prompt suite)",
    )
    parser.add_argument(
        "--from", dest="from_file",
        help="read prompts from a file, one per line",
    )
    parser.add_argument(
        "--out", default=None,
        help="output markdown path (default: private/eval-runs/<ts>.md)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print results as JSON instead of markdown",
    )
    parser.add_argument(
        "--chat-id", default="compare",
        help="chat_id to thread through to Fraser (default: compare)",
    )
    args = parser.parse_args(argv[1:])

    prompts: list[str] = []
    if args.from_file:
        p = Path(args.from_file).expanduser()
        prompts.extend(
            line.strip() for line in p.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    prompts.extend(args.prompts)
    if not prompts:
        prompts = _DEFAULT_PROMPTS

    print(f"Running {len(prompts)} prompt(s) through both planes…", file=sys.stderr)
    results = compare_many(prompts, chat_id=args.chat_id)

    if args.json:
        out = [
            {"prompt": r.prompt, "old": r.old, "new": r.new, "timings_ms": r.timings_ms}
            for r in results
        ]
        print(json.dumps(out, indent=2, default=str))
        return 0

    # Markdown path
    out_path = args.out
    if not out_path:
        ts = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = f"private/eval-runs/compare_{ts}.md"
    saved = save_report(results, out_path=out_path)
    print(f"Wrote {saved} ({len(results)} prompts)", file=sys.stderr)
    # Also dump report to stdout so user can pipe it
    print(render_report(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
