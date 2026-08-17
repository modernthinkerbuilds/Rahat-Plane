"""Benji runner entrypoint — launchd calls this; so can a human.

    python -m new_plane.benji_runner.main --ingest
    python -m new_plane.benji_runner.main --digest        # picks by hour
    python -m new_plane.benji_runner.main --digest morning
    python -m new_plane.benji_runner.main --digest evening
    python -m new_plane.benji_runner.main --status
    python -m new_plane.benji_runner.main --preview       # print, no email
    python -m new_plane.benji_runner.main --mark 87 applied
                       # interim status CLI until the S3 inbound email
                       # loop ships ("applied" auto-drops the role)

Scheduling (installer: scripts/install_benji.sh, owner-run):
    com.rahat.benji.ingest — 06:00 / 10:00 / 14:00 / 18:00 / 22:00
    com.rahat.benji.digest — 07:30 (morning) / 18:05 (evening delta)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from new_plane.log_setup import configure

configure(os.getenv("BENJI_LOG_PATH", "vault/benji_runner.log"),
          level=os.getenv("BENJI_LOG_LEVEL", "INFO"))
logger = logging.getLogger("benji_runner")


def cmd_ingest() -> int:
    from agents.benji.pipeline import run_cycle
    from bridges.jobsearch.fetchers import requests_http

    summary = run_cycle(http=requests_http())
    print(f"{'source':32} {'state':14} {'count':>5}")
    for row in summary:
        print(f"{row['source'][:32]:32} {row['state']:14} "
              f"{row.get('count', 0):>5}  "
              f"+{row.get('added', 0)} new" if row.get("state") == "ok"
              else f"{row['source'][:32]:32} {row['state']:14} "
                   f"{row.get('note', '')[:40]}")
    bad = [r for r in summary if r["state"] not in ("ok", "warning")]
    return 1 if len(bad) == len(summary) and summary else 0


def cmd_digest(which: str | None, *, preview: bool = False) -> int:
    from agents.benji import digest as dg
    from bridges.jobsearch import store
    from new_plane.benji_runner.emailer import send_email

    now = datetime.now()
    if which is None:
        which = "morning" if now.hour < 12 else "evening"

    if which == "morning":
        subject, body, attachments = dg.build_morning(now=now)
    else:
        result = dg.build_evening(now=now)
        if result is None:
            logger.info("evening delta: nothing ≥60 since morning — "
                        "staying silent")
            return 0
        subject, body = result
        attachments = []

    if preview:
        print(f"SUBJECT: {subject}\n\n{body}")
        for name, content in attachments:
            print(f"\n--- attachment: {name} ({len(content)} bytes) ---")
        return 0

    sent, reason = send_email(subject=subject, body=body,
                              attachments=attachments, now=now)
    if sent:
        store.log_digest(which, now=now,
                         meta={"subject": subject,
                               "attachments": [n for n, _ in attachments]})
        logger.info("digest sent: %s", subject)
        return 0
    logger.error("digest NOT sent: %s", reason)
    return 1


def cmd_mark(display_id: int, status: str, note: str) -> int:
    from agents.benji.state import gated_set_status

    ok, reason = gated_set_status(display_id, status, note=note,
                                  now=datetime.now())
    print(f"[{display_id}] → {status}: {reason}")
    return 0 if ok else 1


def cmd_status() -> int:
    from bridges.jobsearch import store

    for s in store.source_ledger():
        print(f"{s['source']:32} {s['state']:12} {s['last_count']:>5} "
              f"@ {s['last_run']} {s.get('note') or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="benji_runner")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--digest", nargs="?", const="auto",
                    choices=["auto", "morning", "evening"])
    ap.add_argument("--preview", action="store_true",
                    help="render the digest to stdout, send nothing")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--mark", nargs="+", metavar=("ID", "STATUS"),
                    help="--mark 87 applied [note…]")
    args = ap.parse_args(argv)

    if args.mark:
        if len(args.mark) < 2:
            ap.error("--mark needs: ID STATUS [note…]")
        return cmd_mark(int(args.mark[0]), args.mark[1],
                        " ".join(args.mark[2:]))
    if args.ingest:
        return cmd_ingest()
    if args.digest:
        which = None if args.digest == "auto" else args.digest
        return cmd_digest(which, preview=args.preview)
    if args.preview:
        return cmd_digest("morning", preview=True)
    if args.status:
        return cmd_status()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
