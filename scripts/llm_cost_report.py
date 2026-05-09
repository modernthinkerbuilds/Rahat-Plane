#!/usr/bin/env python3
"""llm_cost_report.py — daily cost CLI over the decisions ledger.

Reads the `decisions` table written by `core.decisions.span` and prints a
breakdown by actor, model, and day. Lets us answer:

    "What did the Scientist spend yesterday?"
    "Are we on track for $16/mo at the 20-agent target?"
    "Which intent burned the most tokens last week?"

Usage:
    python3 scripts/llm_cost_report.py                       # last 7 days
    python3 scripts/llm_cost_report.py --since 24h
    python3 scripts/llm_cost_report.py --since 30d --by actor
    python3 scripts/llm_cost_report.py --since 7d --by model
    python3 scripts/llm_cost_report.py --since 7d --raw      # one row per call

The CLI runs against `vault/rahat.db` by default; override with
`--db /path/to/other.db` for offline replay or test fixtures.

Why this is a CLI and not a dashboard yet:
    A flat-file SQL query answers 95% of the question; building a live
    dashboard before we have weeks of real data would be premature. When
    the bill exceeds $5/mo we'll graduate this to an artifact (see
    LLM-COST-OPTIMIZATION.md P2.3).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Repo-root on path for `core.cost`
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.cost import fmt_usd  # noqa: E402


_DEFAULT_DB = _REPO / "vault" / "rahat.db"


def _parse_since(since: str) -> str:
    """'7d' → '-7 days' (SQLite-friendly); '24h' → '-24 hours'; etc."""
    s = since.strip().lower()
    if s.endswith("d"):
        return f"-{int(s[:-1])} days"
    if s.endswith("h"):
        return f"-{int(s[:-1])} hours"
    if s.endswith("m"):
        return f"-{int(s[:-1])} minutes"
    raise SystemExit(f"unrecognized --since '{since}' (use e.g. 24h, 7d, 30d)")


def _open(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")
    return sqlite3.connect(str(db_path))


def _has_cost_data(con: sqlite3.Connection, since: str) -> bool:
    n = con.execute(
        "SELECT COUNT(*) FROM decisions "
        "WHERE ts >= datetime('now', ?) AND cost_usd IS NOT NULL",
        (since,)).fetchone()[0]
    return n > 0


def report_summary(con: sqlite3.Connection, since: str) -> None:
    rows = con.execute("""
        SELECT
            COUNT(*)                      AS calls,
            COALESCE(SUM(tokens_in), 0)   AS toks_in,
            COALESCE(SUM(tokens_out), 0)  AS toks_out,
            COALESCE(SUM(cost_usd), 0)    AS cost
        FROM decisions
        WHERE ts >= datetime('now', ?) AND cost_usd IS NOT NULL
    """, (since,)).fetchone()
    calls, toks_in, toks_out, cost = rows
    print(f"\n=== LLM cost summary (since {since}) ===")
    print(f"  calls        : {calls:,}")
    print(f"  tokens in    : {toks_in:,}")
    print(f"  tokens out   : {toks_out:,}")
    print(f"  total spend  : {fmt_usd(cost)}")
    if calls:
        print(f"  avg/call     : {fmt_usd(cost / calls)}")


def report_by(con: sqlite3.Connection, since: str, dim: str) -> None:
    if dim == "actor":
        col, label = "actor", "actor"
    elif dim == "model":
        col, label = "json_extract(input_json,'$.model')", "model"
    elif dim == "op":
        col, label = "op", "op"
    elif dim == "day":
        col, label = "substr(ts, 1, 10)", "day"
    else:
        raise SystemExit(f"unknown --by '{dim}' (use actor, model, op, day)")

    rows = con.execute(f"""
        SELECT
            {col}                        AS k,
            COUNT(*)                     AS calls,
            COALESCE(SUM(tokens_in), 0)  AS toks_in,
            COALESCE(SUM(tokens_out), 0) AS toks_out,
            COALESCE(SUM(cost_usd), 0)   AS cost
        FROM decisions
        WHERE ts >= datetime('now', ?) AND cost_usd IS NOT NULL
        GROUP BY {col}
        ORDER BY cost DESC
    """, (since,)).fetchall()
    print(f"\n=== by {label} (since {since}) ===")
    print(f"  {'key':<32} {'calls':>7} {'tok_in':>10} {'tok_out':>10} {'cost':>10}")
    for k, calls, t_in, t_out, c in rows:
        print(f"  {str(k or '-'):<32} {calls:>7,} {t_in:>10,} {t_out:>10,} {fmt_usd(c):>10}")


def report_raw(con: sqlite3.Connection, since: str, limit: int) -> None:
    print(f"\n=== last {limit} LLM calls (since {since}) ===")
    rows = con.execute("""
        SELECT ts, actor, op, tokens_in, tokens_out, cost_usd, latency_ms
        FROM decisions
        WHERE ts >= datetime('now', ?) AND cost_usd IS NOT NULL
        ORDER BY ts DESC
        LIMIT ?
    """, (since, limit)).fetchall()
    for ts, actor, op, t_in, t_out, c, lat in rows:
        print(f"  {ts:<19}  {actor:<14} {op:<28} "
              f"{t_in or 0:>6} → {t_out or 0:>5}  "
              f"{fmt_usd(c or 0):>9}  {lat or 0:>5}ms")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(_DEFAULT_DB),
                    help=f"path to rahat.db (default: {_DEFAULT_DB})")
    ap.add_argument("--since", default="7d",
                    help="window — 24h, 7d, 30d (default: 7d)")
    ap.add_argument("--by", default=None, choices=["actor", "model", "op", "day"],
                    help="break down by actor / model / op / day")
    ap.add_argument("--raw", action="store_true",
                    help="print one row per LLM call")
    ap.add_argument("--limit", type=int, default=30,
                    help="how many rows to show with --raw (default: 30)")
    args = ap.parse_args(argv)

    since = _parse_since(args.since)
    con = _open(Path(args.db))
    try:
        if not _has_cost_data(con, since):
            print(f"No cost-tagged decisions in the last {args.since}. "
                  "Either nothing ran or the telemetry isn't wired yet.")
            return 0
        report_summary(con, since)
        if args.by:
            report_by(con, since, args.by)
        if args.raw:
            report_raw(con, since, args.limit)
        # If neither --by nor --raw, fall back to a useful default
        # breakdown so a bare `llm_cost_report.py` does the right thing.
        if not args.by and not args.raw:
            report_by(con, since, "actor")
            report_by(con, since, "day")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
