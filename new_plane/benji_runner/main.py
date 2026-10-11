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

logger = logging.getLogger("benji_runner")


def _configure_runtime() -> None:
    """Log file + .env — called from main(), NEVER at import. The
    import-time version wrote every pre-push test's fixture lines
    ('stranger@evil.com', fake digests) into the LIVE
    vault/benji_runner.log, which derailed launch-day debugging
    (2026-08-17 20:06). Tests import this module's functions; only the
    real entrypoint touches the real log and the real .env."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return
    configure(os.getenv("BENJI_LOG_PATH", "vault/benji_runner.log"),
              level=os.getenv("BENJI_LOG_LEVEL", "INFO"))
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
        if cap <= 0 or preview:
            candidates = []      # kits on request only (her preference)
        else:
            candidates = [
                r for r in store.queue_rows(only_undigested=True)
                if (r.get("score") or 0) >= dg.apply_threshold(prefs)
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
        stats: dict = {}
        subject, body, attachments, html = dg.build_morning(
            now=now, package_names=pkg_names or None, with_html=True,
            commit=not preview, stats=stats)
        attachments = attachments + pkg_files
    else:
        stats = {}
        result = dg.build_evening(now=now, with_html=True)
        if result is None:
            logger.info("evening delta: nothing ≥60 since morning — "
                        "staying silent")
            return 0
        subject, body, html = result
        attachments = []

    # T5: the health line. With no owner address it rides inside the
    # morning digest under "For the owner"; with one it is its own
    # plain-text email after the send (so a failed send counts too).
    owner = (os.getenv("BENJI_OWNER_EMAIL") or "").strip()
    problems: list[str] = []
    if which == "morning" and not preview and not dg.health_sent_today(now=now):
        problems = dg.health_problems(now=now)
        if problems and not owner:
            body += "\n" + dg.owner_section(problems)
            if html:
                from agents.benji.digest_html import owner_section_html
                cut = html.rfind("</div>")        # inside the outer block
                html = (html[:cut] + owner_section_html(problems) + html[cut:]
                        if cut >= 0 else html + owner_section_html(problems))

    if preview:
        # Reads only: nothing is marked digested, demoted or logged
        # (2026-10-07: a preview used to consume the 7:30 queue).
        print(f"SUBJECT: {subject}\n\n{body}")
        for name, content in attachments:
            print(f"\n--- attachment: {name} ({len(content)} bytes) ---")
        return 0

    recipient = (os.getenv("BENJI_DELIVERY_EMAIL") or "").strip()
    try:
        sent, reason = send_email(subject=subject, body=body, html=html,
                                  attachments=attachments, now=now)
    except Exception as e:                      # noqa: BLE001 — SMTP down
        sent, reason = False, f"{type(e).__name__}: {e}"
    if sent:
        # A digest an account sent to itself arrives already read, with no
        # alert: it does not count as having reached her, so the catch-up
        # still goes out once a real sender is set (2026-10-11).
        from new_plane.benji_runner.emailer import sending_account
        self_sent = sending_account(recipient)[0].strip().lower() == recipient.lower()
        store.log_digest(which, now=now,
                         meta={"subject": subject, "to": recipient,
                               "attachments": [n for n, _ in attachments],
                               "self_sent": self_sent, **stats})
        if problems and not owner:
            store.log_digest("health", now=now,
                             meta={"to": "digest", "problems": problems})
        logger.info("digest sent to %s: %s", recipient, subject)
        rc = 0
    else:
        logger.error("digest NOT sent to %s: %s", recipient, reason)
        store.record_failure(f"{which} digest", reason, now=now)
        rc = 1

    if which == "morning" and owner and not dg.health_sent_today(now=now):
        problems = dg.health_problems(now=now)       # includes a send that just failed
        if problems:
            _send_health_line(owner, problems, now=now)
    return rc


def _send_health_line(owner: str, problems: list[str], *, now) -> None:
    """One plain-text email to the owner, at most once a day."""
    from bridges.jobsearch import store
    from new_plane.benji_runner.emailer import send_email
    subject = f"Benji · health: {len(problems)} thing(s) need a look"
    body = "\n".join(["Benji needs a look:", "",
                       *[f"  • {p}" for p in problems], "",
                       "(sent at most once a day, only when something is "
                       "wrong — BENJI_OWNER_EMAIL in .env)"])
    try:
        sent, reason = send_email(subject=subject, body=body, now=now,
                                  to=owner)
    except Exception as e:                      # noqa: BLE001
        sent, reason = False, f"{type(e).__name__}: {e}"
    if sent:
        store.log_digest("health", now=now,
                         meta={"to": "owner", "problems": problems})
        logger.info("health line sent to %s: %d problem(s)", owner,
                    len(problems))
    else:
        logger.error("health line NOT sent to %s: %s", owner, reason)
        store.record_failure("health line", reason, now=now)


def cmd_kit(display_id: int, *, preview: bool = False) -> int:
    """Build + email one package on demand (the `kit N` action, CLI
    edition until S3's inbound loop ships). Email-only delivery — a
    package that can't be emailed is not written anywhere (PRD v1.2)."""
    from agents.benji.generation import generate_package
    from new_plane.benji_runner.emailer import send_email

    now = datetime.now()
    result = generate_package(display_id, now=now, on_request=True)
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
    if sent:
        from bridges.jobsearch import store
        store.log_digest("kit", now=now, meta={"job_id": display_id,
                                               "sender": "cli"})
        logger.info("kit [%s] sent to %s", display_id,
                    (os.getenv("BENJI_DELIVERY_EMAIL") or "").strip())
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
    _configure_runtime()
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as e:                      # noqa: BLE001
        # T5: a run that dies is recorded where the health check reads,
        # with its traceback in the log, and the process exits cleanly.
        logger.exception("benji run failed: %s: %s", type(e).__name__, e)
        try:
            from bridges.jobsearch import store
            store.record_failure(" ".join(argv or sys.argv[1:]) or "run",
                                 f"{type(e).__name__}: {e}",
                                 now=datetime.now())
        except Exception:                       # noqa: BLE001
            pass
        return 1


def _main(argv: list[str] | None = None) -> int:
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
