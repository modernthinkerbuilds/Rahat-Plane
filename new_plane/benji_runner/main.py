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

# Launchd starts this process with a bare environment — the BENJI_* keys
# live in .env. Never under test mode (the hermetic stack must not read
# a developer's real .env — the exact class core.io guards against).
if os.getenv("RAHAT_TEST_MODE") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:                              # noqa: BLE001
        pass


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

    if store.meta_get("digests_paused") == "1":
        logger.info("digests paused (she said `pause`) — skipping %s",
                    which)
        return 0
    store.wake_snoozed(now=now)      # snoozes that expired resurface

    if which == "morning":
        # S2: auto-build packages for the apply band before the digest
        # renders — capped by preferences, coverage floor enforced
        # inside generate_package (a stretch-low-match role refuses).
        from agents.benji.generation import generate_package
        from agents.benji.protocols import COVERAGE_FLOOR, load_preferences

        prefs, _ = load_preferences()
        cap = int(prefs.get("morning_package_cap", 5))
        candidates = [r for r in store.queue_rows(only_undigested=True)
                      if (r.get("score") or 0) >= 75
                      and (r.get("coverage") or 0) >= COVERAGE_FLOOR
                      and (r.get("jd_text") or "").strip()]
        candidates.sort(key=lambda r: -(r.get("score") or 0))
        pkg_files: list = []
        pkg_names: list[str] = []
        for r in candidates[:cap]:
            result = generate_package(r["id"], now=now)
            if result.get("ok"):
                pkg_files += result["files"]
                pkg_names.append(f"[{r['id']}] {r['org']}")
            else:
                logger.warning("package for [%s] not built: %s", r["id"],
                               result.get("refusal"))
        subject, body, attachments = dg.build_morning(
            now=now, package_names=pkg_names or None)
        attachments = attachments + pkg_files
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


def cmd_kit(display_id: int, *, preview: bool = False) -> int:
    """Build + email one package on demand (the `kit N` action, CLI
    edition until S3's inbound loop ships). Email-only delivery — a
    package that can't be emailed is not written anywhere (PRD v1.2)."""
    from agents.benji.generation import generate_package
    from new_plane.benji_runner.emailer import send_email

    now = datetime.now()
    result = generate_package(display_id, now=now)
    if not result.get("ok"):
        print(f"[{display_id}] no package: {result.get('refusal')}")
        return 1
    job = result["job"]
    if preview:
        print(result["review_md"])
        return 0
    head = (f"[{display_id}] {job.get('title')} — {job.get('org')}\n"
            f"Apply at: {job.get('canonical_url')}\n\n")
    sent, reason = send_email(
        subject=f"Benji · kit [{display_id}] {job.get('org')}",
        body=head + result["review_md"], attachments=result["files"],
        now=now)
    print(f"[{display_id}] {'sent' if sent else 'NOT sent'}: {reason}")
    return 0 if sent else 1


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
    ap.add_argument("--kit", type=int, metavar="ID",
                    help="build + email the package for one role")
    ap.add_argument("--inbox", action="store_true",
                    help="poll the mailbox once and act on her replies")
    args = ap.parse_args(argv)

    if args.inbox:
        from new_plane.benji_runner.inbox import poll_inbox
        result = poll_inbox()
        logger.info("inbox: %s", result)
        return 0

    if args.kit is not None:
        return cmd_kit(args.kit, preview=args.preview)
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
