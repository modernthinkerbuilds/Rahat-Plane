#!/usr/bin/env python3
"""memory_consolidate.py — sleep-time-compute background worker.

Runs nightly (cron at 03:00 local). Performs the maintenance that
keeps the memory substrate dense rather than bloated:

    1. Summarize threads inactive >24h.
    2. Decay preference confidence for stale prefs.
    3. Archive entities past their valid_until.
    4. Garbage-collect very old events.
    5. Purge unused archival entries.

Uses Gemini Flash for summarization (~$0.001/run total at typical
volume). Idempotent — safe to re-run multiple times in a day.

Install via cron (Mac):
    crontab -e
    # add:
    0 3 * * * cd ~/developer/agency/rahat && /usr/bin/env python3 scripts/memory_consolidate.py >> vault/consolidate.log 2>&1

Or via launchd plist for better OS integration.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import memory as mem
from core.memory import archival
from core import io as cio


# ─────────────────────────── Thread summarization ───────────────────────────
def summarize_thread(thread: dict) -> str:
    """Generate a 1-2 sentence summary of a thread using Gemini Flash.
    Reads recent events tagged to the thread."""
    client = cio.llm_client()
    if not client:
        return thread.get("summary") or "(no LLM available for summary)"

    # Pull recent events for this agent that mention thread keywords.
    # Cheap heuristic — we don't have explicit thread<->event linkage
    # in the schema, so we pull all events from the thread's time
    # window and ask the model to summarize.
    started = thread.get("started_at")
    last = thread.get("last_active_at")
    events = mem.recent_events(
        thread["agent"],
        since_hours=72,  # full window for old threads
        limit=50)
    if not events:
        return thread.get("summary") or "(no events to summarize)"

    snippets = []
    for e in events[:30]:
        kind = e.get("kind", "")
        payload = e.get("payload") or {}
        if kind == "msg.in" and isinstance(payload, dict):
            t = payload.get("text") or payload.get("msg") or ""
            if t: snippets.append(f"User: {t[:200]}")
        elif kind == "msg.out" and isinstance(payload, dict):
            t = payload.get("text") or ""
            if t: snippets.append(f"Agent: {t[:200]}")

    if not snippets:
        return thread.get("summary") or "(no message content)"

    prompt = (
        f"Summarize this conversation thread in 1-2 sentences. "
        f"Focus on decisions made and any open questions. "
        f"Topic: {thread['topic']}\n\n"
        + "\n".join(snippets[-20:]))
    try:
        resp = client.models.generate_content(
            model=os.getenv("CONSOLIDATE_MODEL", "gemini-2.5-flash"),
            contents=prompt)
        return (getattr(resp, "text", "") or "").strip()[:500]
    except Exception as e:
        print(f"[consolidate] summarize failed: {e}")
        return thread.get("summary") or "(summary unavailable)"


def consolidate_threads(*, dry_run: bool = False,
                        inactive_hours: int = 24,
                        db_path: str | None = None) -> dict:
    """Walk all open threads, summarize the inactive ones, mark stale
    ones as resolved."""
    summarized = 0
    resolved = 0
    cutoff_iso = (datetime.now() - timedelta(hours=inactive_hours)).isoformat()

    con = mem._connect(db_path)
    try:
        cur = con.execute(
            "SELECT * FROM memory_threads "
            "WHERE status='open' AND last_active_at < ?",
            (cutoff_iso,))
        threads = []
        for row in cur.fetchall():
            d = {col[0]: v for col, v in zip(cur.description, row)}
            threads.append(d)
    finally:
        con.close()

    for t in threads:
        # Summarize if no summary or summary is stale.
        summary = summarize_thread(t)
        if dry_run:
            print(f"  would update thread {t['thread_id']} ({t['topic']}): {summary[:80]}")
        else:
            mem.update_thread(t["thread_id"], summary=summary, db_path=db_path)
        summarized += 1

        # Resolve threads inactive > 7 days.
        last_active = t.get("last_active_at")
        if last_active:
            try:
                la = datetime.fromisoformat(last_active.replace("Z", ""))
                if la < datetime.now() - timedelta(days=7):
                    if not dry_run:
                        mem.update_thread(t["thread_id"], status="resolved",
                                          db_path=db_path)
                    resolved += 1
            except Exception:
                pass

    return {"summarized": summarized, "resolved": resolved}


# ─────────────────────────── Preference decay ───────────────────────────
def decay_preferences(*, factor: float = 0.95,
                      older_than_days: int = 7,
                      db_path: str | None = None,
                      dry_run: bool = False) -> int:
    """Apply a multiplicative decay to preference confidence for any
    pref not reinforced in the last `older_than_days`."""
    if dry_run:
        # Dry-run: count without updating.
        con = mem._connect(db_path)
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM memory_preferences "
                "WHERE last_seen < datetime('now', ?)",
                (f"-{int(older_than_days)} days",))
            return cur.fetchone()[0]
        finally:
            con.close()
    return mem.decay_prefs(factor=factor, older_than_days=older_than_days,
                           db_path=db_path)


# ─────────────────────────── Entity archival ───────────────────────────
def archive_expired_entities(*, db_path: str | None = None,
                             dry_run: bool = False) -> int:
    """Mark entities past valid_until as expired (status='expired').
    Doesn't delete — keeps the audit trail."""
    con = mem._connect(db_path)
    try:
        if dry_run:
            cur = con.execute(
                "SELECT COUNT(*) FROM memory_entities "
                "WHERE status = 'active' "
                "  AND valid_until IS NOT NULL "
                "  AND valid_until < CURRENT_TIMESTAMP")
            return cur.fetchone()[0]
        cur = con.execute(
            "UPDATE memory_entities SET status='expired', "
            "       updated_at=CURRENT_TIMESTAMP "
            "WHERE status = 'active' "
            "  AND valid_until IS NOT NULL "
            "  AND valid_until < CURRENT_TIMESTAMP")
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


# ─────────────────────────── Event GC ───────────────────────────
def gc_old_events(*, older_than_days: int = 365,
                  db_path: str | None = None,
                  dry_run: bool = False) -> int:
    """Garbage-collect events older than ~1 year. We keep the recent
    history dense and rely on archival memory + thread summaries for
    the long tail."""
    cutoff = f"-{int(older_than_days)} days"
    con = mem._connect(db_path)
    try:
        if dry_run:
            cur = con.execute(
                "SELECT COUNT(*) FROM memory_events WHERE ts < datetime('now', ?)",
                (cutoff,))
            return cur.fetchone()[0]
        cur = con.execute(
            "DELETE FROM memory_events WHERE ts < datetime('now', ?)",
            (cutoff,))
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


# ─────────────────────────── Archival GC ───────────────────────────
def gc_unused_archival(*, older_than_days: int = 365,
                       db_path: str | None = None,
                       dry_run: bool = False) -> int:
    """Archival entries that are old AND have never been retrieved."""
    if dry_run:
        con = mem._connect(db_path)
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM memory_archival "
                "WHERE created_at < datetime('now', ?) "
                "  AND access_count = 0",
                (f"-{int(older_than_days)} days",))
            return cur.fetchone()[0]
        finally:
            con.close()
    return archival.archival_purge_unused(
        older_than_days=older_than_days, db_path=db_path)


# ─────────────────────────── Driver ───────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be done without writing")
    p.add_argument("--db", default=None, help="override DB path")
    p.add_argument("--skip-summaries", action="store_true",
                   help="skip thread summarization (saves ~$0.001 per stale thread)")
    args = p.parse_args(argv)

    db_path = args.db
    started = datetime.now()
    print(f"[consolidate] start: {started.isoformat()} dry_run={args.dry_run}")
    print(f"[consolidate] memory stats before: {mem.stats(db_path=db_path)}")
    print(f"[consolidate] archival count before: {archival.archival_count(db_path=db_path)}")

    if not args.skip_summaries:
        r = consolidate_threads(dry_run=args.dry_run, db_path=db_path)
        print(f"[consolidate] threads: summarized={r['summarized']}, "
              f"resolved={r['resolved']}")
    else:
        print("[consolidate] skipping thread summaries")

    n = decay_preferences(dry_run=args.dry_run, db_path=db_path)
    print(f"[consolidate] preferences decayed: {n}")

    n = archive_expired_entities(dry_run=args.dry_run, db_path=db_path)
    print(f"[consolidate] entities archived: {n}")

    n = gc_old_events(dry_run=args.dry_run, db_path=db_path)
    print(f"[consolidate] events gc'd: {n}")

    n = gc_unused_archival(dry_run=args.dry_run, db_path=db_path)
    print(f"[consolidate] archival gc'd: {n}")

    print(f"[consolidate] memory stats after: {mem.stats(db_path=db_path)}")
    print(f"[consolidate] elapsed: {(datetime.now() - started).total_seconds():.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
