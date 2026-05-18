"""Mine real user phrasings from the production decisions ledger.

Connects to a Rahat DB, pulls every miya.route input from the last N
days, dedupes on lowercase-token overlap, and writes a JSON corpus
to tests/adversarial/corpus.json. The corpus drives the adversarial
phrasings layer — assertions are run against the REAL phrasings the
user has typed, not invented ones.

Usage:
    python scripts/mine_phrasings.py
        # mines from $RAHAT_DB_PATH or vault/rahat.db, last 30 days
    python scripts/mine_phrasings.py --db ~/path/to/rahat.db --since-days 60
    python scripts/mine_phrasings.py --output /tmp/corpus.json

The output is a JSON array of entries shaped like:
    {
      "text": "what is the WOD",
      "winner": "kobe",                # what Miya routed to
      "strategy": "regex+llm",         # how it routed
      "first_seen": "2026-05-15",      # first timestamp this phrasing appeared
      "occurrences": 4,                # how many times the user typed it
      "expected_agent": null           # user hand-labels later for the test
    }

The `expected_agent` field starts null — a human (or LLM-assisted
labeller) tags the first 20-50 entries with what they SHOULD have
routed to. The adversarial test then asserts each phrasing actually
routes to its expected agent and produces a non-empty, non-stub reply.

Rerun this script periodically (weekly) to refresh the corpus as the
user's vocabulary expands. New entries land with expected_agent=null
so the labelling backlog is visible.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def normalize(text: str) -> str:
    """Normalize for dedup: lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9\s/]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def query_messages(db_path: Path, since_days: int) -> list[dict]:
    """Pull miya.route inputs from the decisions ledger.

    Per the user's spec (2026-05-17 context drop):
        SELECT
          json_extract(input_json, '$.msg') AS user_msg,
          json_extract(output_json, '$.strategy') AS strategy,
          json_extract(output_json, '$.winner') AS winner
        FROM decisions
        WHERE op = 'miya.route' AND user_msg IS NOT NULL
    """
    if not db_path.exists():
        print(f"  ✗ DB not found: {db_path}", file=sys.stderr)
        return []
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        print(f"  ✗ couldn't open {db_path}: {e}", file=sys.stderr)
        return []

    try:
        # The decisions schema has evolved — fall back to plain columns if
        # JSON_extract on a TEXT field fails.
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime(
            "%Y-%m-%d %H:%M:%S")
        rows = con.execute(
            """
            SELECT
              json_extract(input_json, '$.msg')         AS user_msg,
              json_extract(output_json, '$.strategy')   AS strategy,
              json_extract(output_json, '$.winner')     AS winner,
              ts
            FROM decisions
            WHERE op = 'miya.route'
              AND json_extract(input_json, '$.msg') IS NOT NULL
              AND ts >= ?
            ORDER BY ts ASC
            """,
            (cutoff,)
        ).fetchall()
    except sqlite3.Error as e:
        print(f"  ✗ query failed: {e}", file=sys.stderr)
        return []
    finally:
        con.close()

    return [
        {"text": r[0], "strategy": r[1], "winner": r[2], "ts": r[3]}
        for r in rows
        if r[0] and len((r[0] or "").strip()) > 0
    ]


def dedupe_corpus(rows: list[dict]) -> list[dict]:
    """Collapse exact + near-duplicate phrasings.

    Strategy: normalize() → use as dict key. Keep first-seen ts and
    count occurrences. Track the most common (winner, strategy) per
    phrasing as the production observation.
    """
    grouped: dict[str, dict] = {}
    for row in rows:
        key = normalize(row["text"])
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {
                "text": row["text"].strip(),
                "_winners": Counter(),
                "_strategies": Counter(),
                "first_seen": row["ts"],
                "last_seen": row["ts"],
                "occurrences": 0,
            }
        bucket = grouped[key]
        bucket["_winners"][row.get("winner") or "?"] += 1
        bucket["_strategies"][row.get("strategy") or "?"] += 1
        bucket["last_seen"] = row["ts"]
        bucket["occurrences"] += 1
        # Prefer the longer / more punctuated original as the canonical text.
        if len(row["text"]) > len(bucket["text"]):
            bucket["text"] = row["text"].strip()

    out = []
    for key, b in grouped.items():
        out.append({
            "text": b["text"],
            "winner": b["_winners"].most_common(1)[0][0],
            "strategy": b["_strategies"].most_common(1)[0][0],
            "first_seen": b["first_seen"][:10] if b["first_seen"] else None,
            "last_seen":  b["last_seen"][:10] if b["last_seen"] else None,
            "occurrences": b["occurrences"],
            "expected_agent": None,   # human-labeled later
            "intent": None,           # human-labeled later
        })
    # Sort by occurrences DESC then text ASC so the most-typed
    # phrasings get hand-labeled first.
    out.sort(key=lambda e: (-e["occurrences"], e["text"]))
    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db",
                   default=os.environ.get("RAHAT_DB_PATH",
                                          "vault/rahat.db"),
                   help="SQLite DB to read (default: vault/rahat.db)")
    p.add_argument("--since-days", type=int, default=30,
                   help="how far back to look (default: 30)")
    p.add_argument("--output",
                   default="tests/adversarial/corpus.json",
                   help="output JSON path")
    p.add_argument("--limit", type=int, default=500,
                   help="cap on number of unique phrasings emitted")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    db_path = Path(args.db).expanduser()
    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.quiet:
        print(f"  mining phrasings from {db_path} (last {args.since_days}d)")

    rows = query_messages(db_path, args.since_days)
    if not rows:
        if not args.quiet:
            print(f"  ⊘ no rows found — DB empty or path wrong")
        return 1

    corpus = dedupe_corpus(rows)[:args.limit]

    # Preserve any existing hand-labels on output: merge in expected_agent
    # / intent from the existing corpus.json if present.
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            by_text = {normalize(e["text"]): e for e in existing}
            for entry in corpus:
                old = by_text.get(normalize(entry["text"]))
                if old:
                    entry["expected_agent"] = old.get("expected_agent")
                    entry["intent"] = old.get("intent")
        except (json.JSONDecodeError, OSError):
            pass

    out_path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False))

    if not args.quiet:
        unlabeled = sum(1 for e in corpus if e["expected_agent"] is None)
        print(f"  ✓ wrote {len(corpus)} phrasings → {out_path}")
        print(f"    {len(rows)} raw msgs → {len(corpus)} unique after dedup")
        if unlabeled:
            print(f"    ⚠ {unlabeled} phrasings need expected_agent label")
            print(f"      hand-label the top 20-50 to seed the suite")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
