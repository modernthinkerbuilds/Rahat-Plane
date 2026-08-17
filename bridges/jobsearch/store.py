"""Job inventory store — deduped, replay-safe, rejects retained.

One row per (normalized org, title, location) — Benji's dedup key per
the Filter Config ("Dedup on (org, title, location). Keep the ATS link
as canonical — it is the application destination."). Rejected postings
are RETAINED with `filter_result` + `reject_reason` (PRD J5, Tara
review #4): a false reject is invisible unless you keep the evidence,
and the Sunday digest samples 20 of them for calibration.

Liveness (PRD J7): a feed-source row absent from its source's latest
snapshot flips to status='closed' — the 4–6h poll IS the liveness
check; no extra requests.

db_path() — the hermeticity contract
------------------------------------
Order: explicit RAHAT_JOBSEARCH_DB → TEST-MODE SANDBOX → the live
vault path. Copied from bridges/events/store.py AFTER its 2026-08-12
fix (fe5ecda), not from genie.state._vault_dir(): the events pattern
has a tempdir fallback inside the sandbox branch, so a test that
forgot RAHAT_TEST_VAULT_DIR still cannot touch a live file. There is
deliberately NO fallback chain through other agents' DB env vars —
that chain is exactly how events reads leaked to the live vault DB.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta

# Terminal statuses never resurface in the queue (Scoring Rules v2
# automatic drop: "already applied to this role, or already seen and
# skipped").
TERMINAL_STATUSES = ("applied", "skipped")


def db_path() -> str:
    """Resolve the jobs inventory DB path (sandbox-first; see module doc)."""
    explicit = os.getenv("RAHAT_JOBSEARCH_DB")
    if explicit:
        return explicit
    if os.getenv("RAHAT_TEST_MODE") == "1":
        import tempfile
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR") or os.path.join(
            tempfile.gettempdir(), f"rahat_test_{os.getpid()}")
        os.makedirs(sandbox, exist_ok=True)
        return os.path.join(sandbox, "jobsearch_test.db")
    live = os.path.expanduser(
        "~/developer/agency/rahat/vault/benji/jobsearch.db")
    os.makedirs(os.path.dirname(live), exist_ok=True)
    return live


def _connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path())
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_key TEXT UNIQUE,
        org TEXT, title TEXT, location TEXT, work_mode TEXT,
        comp_range TEXT, comp_unlisted INTEGER DEFAULT 0,
        posted_date TEXT, canonical_url TEXT, jd_text TEXT,
        source TEXT, source_tier INTEGER,
        title_cluster TEXT, filter_result TEXT, reject_reason TEXT,
        coverage REAL, score INTEGER, score_breakdown TEXT,
        rationale TEXT, flags TEXT, stretch INTEGER DEFAULT 0,
        status TEXT DEFAULT 'new',
        first_seen TEXT, last_seen TEXT, closed_at TEXT,
        digested_at TEXT, status_note TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS source_state (
        source TEXT PRIMARY KEY,
        last_run TEXT, last_count INTEGER,
        consecutive_empty INTEGER DEFAULT 0,
        state TEXT DEFAULT 'ok', note TEXT,
        cold_started INTEGER DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS digest_log (
        kind TEXT, sent_at TEXT, meta TEXT)""")
    return con


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_key(org: str, title: str, location: str) -> str:
    blob = f"{_norm(org)}|{_norm(title)}|{_norm(location)}"
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def upsert_batch(rows: list[dict], *, now: datetime,
                 path: str | None = None) -> dict:
    """Idempotent write of one source's refresh. Returns counts.

    A row reappearing after 'closed' reopens as 'new' (flagged); a row
    in a terminal status (applied/skipped) NEVER resurfaces — the
    Scoring Rules automatic drop.
    """
    con = _connect(path)
    now_iso = _iso(now)
    added = updated = reopened = 0
    seen_keys: list[str] = []
    try:
        for r in rows:
            key = job_key(r.get("org", ""), r.get("title", ""),
                          r.get("location", ""))
            seen_keys.append(key)
            row = con.execute("SELECT id, status FROM jobs WHERE job_key=?",
                              (key,)).fetchone()
            if row is None:
                con.execute(
                    """INSERT INTO jobs (job_key, org, title, location,
                        work_mode, comp_range, comp_unlisted, posted_date,
                        canonical_url, jd_text, source, source_tier,
                        title_cluster, filter_result, reject_reason,
                        coverage, score, score_breakdown, rationale, flags,
                        stretch, status, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, r.get("org", ""), r.get("title", ""),
                     r.get("location", ""), r.get("work_mode", ""),
                     r.get("comp_range", ""),
                     1 if r.get("comp_unlisted") else 0,
                     r.get("posted_date"), r.get("canonical_url", ""),
                     r.get("jd_text", ""), r.get("source", ""),
                     int(r.get("source_tier") or 1),
                     r.get("title_cluster", ""),
                     r.get("filter_result", ""), r.get("reject_reason", ""),
                     r.get("coverage"), r.get("score"),
                     json.dumps(r.get("score_breakdown") or {}),
                     r.get("rationale", ""),
                     json.dumps(r.get("flags") or []),
                     1 if r.get("stretch") else 0,
                     r.get("status", "new"), now_iso, now_iso))
                added += 1
            else:
                if row["status"] in TERMINAL_STATUSES:
                    con.execute("UPDATE jobs SET last_seen=? WHERE job_key=?",
                                (now_iso, key))
                    continue
                new_status = row["status"]
                if row["status"] == "closed":
                    new_status, reopened = "new", reopened + 1
                con.execute(
                    """UPDATE jobs SET comp_range=?, comp_unlisted=?,
                        posted_date=COALESCE(?, posted_date),
                        canonical_url=?, jd_text=?, coverage=?, score=?,
                        score_breakdown=?, rationale=?, status=?,
                        last_seen=?, closed_at=NULL
                       WHERE job_key=?""",
                    (r.get("comp_range", ""),
                     1 if r.get("comp_unlisted") else 0,
                     r.get("posted_date"), r.get("canonical_url", ""),
                     r.get("jd_text", ""), r.get("coverage"),
                     r.get("score"),
                     json.dumps(r.get("score_breakdown") or {}),
                     r.get("rationale", ""), new_status, now_iso, key))
                updated += 1
        con.commit()
    finally:
        con.close()
    return {"added": added, "updated": updated, "reopened": reopened,
            "seen_keys": seen_keys}


def mark_missing_closed(source: str, seen_keys: list[str], *,
                        now: datetime, path: str | None = None) -> int:
    """Liveness via snapshot-absence (PRD J7): open rows of `source` not
    in this refresh flip to 'closed'. Terminal rows are left alone."""
    con = _connect(path)
    try:
        qmarks = ",".join("?" for _ in seen_keys) or "''"
        cur = con.execute(
            f"""UPDATE jobs SET status='closed', closed_at=?
                WHERE source=? AND status NOT IN ('closed','applied','skipped')
                AND job_key NOT IN ({qmarks})""",
            [_iso(now), source, *seen_keys])
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def record_source_run(source: str, count: int, *, now: datetime,
                      state: str = "ok", note: str = "",
                      path: str | None = None) -> dict:
    """Update the per-source ledger. 3 consecutive empties → 'dead-feed?'
    (the Filter Config rule: raise a flag, not fail silently)."""
    con = _connect(path)
    try:
        row = con.execute("SELECT consecutive_empty, cold_started FROM "
                          "source_state WHERE source=?", (source,)).fetchone()
        empty = (row["consecutive_empty"] if row else 0)
        cold_started = (row["cold_started"] if row else 0)
        empty = empty + 1 if (count == 0 and state == "ok") else 0
        if empty >= 3:
            state, note = "dead-feed?", note or "empty 3 consecutive runs"
        con.execute(
            """INSERT INTO source_state
                 (source, last_run, last_count, consecutive_empty, state,
                  note, cold_started)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET last_run=excluded.last_run,
                 last_count=excluded.last_count,
                 consecutive_empty=excluded.consecutive_empty,
                 state=excluded.state, note=excluded.note""",
            (source, _iso(now), count, empty, state, note, cold_started))
        con.commit()
        return {"source": source, "state": state, "consecutive_empty": empty}
    finally:
        con.close()


def source_cold_started(source: str, path: str | None = None) -> bool:
    con = _connect(path)
    try:
        row = con.execute("SELECT cold_started FROM source_state WHERE "
                          "source=?", (source,)).fetchone()
        return bool(row and row["cold_started"])
    finally:
        con.close()


def mark_cold_started(source: str, *, now: datetime,
                      path: str | None = None) -> None:
    con = _connect(path)
    try:
        con.execute(
            """INSERT INTO source_state (source, last_run, cold_started)
               VALUES (?,?,1)
               ON CONFLICT(source) DO UPDATE SET cold_started=1""",
            (source, _iso(now)))
        con.commit()
    finally:
        con.close()


def source_ledger(path: str | None = None) -> list[dict]:
    con = _connect(path)
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM source_state ORDER BY source").fetchall()]
    finally:
        con.close()


def queue_rows(*, statuses: tuple[str, ...] = ("new",),
               only_undigested: bool = False,
               path: str | None = None) -> list[dict]:
    con = _connect(path)
    try:
        q = (f"SELECT * FROM jobs WHERE status IN "
             f"({','.join('?' for _ in statuses)}) "
             f"AND filter_result IN ('accept','flag')")
        if only_undigested:
            q += " AND digested_at IS NULL"
        return [dict(r) for r in con.execute(q, list(statuses)).fetchall()]
    finally:
        con.close()


def mark_digested(ids: list[int], *, now: datetime,
                  path: str | None = None) -> None:
    if not ids:
        return
    con = _connect(path)
    try:
        con.execute(
            f"UPDATE jobs SET digested_at=? WHERE id IN "
            f"({','.join('?' for _ in ids)})", [_iso(now), *ids])
        con.commit()
    finally:
        con.close()


def set_status(display_id: int, status: str, *, note: str = "",
               now: datetime, path: str | None = None) -> bool:
    con = _connect(path)
    try:
        cur = con.execute(
            "UPDATE jobs SET status=?, status_note=?, last_seen=? WHERE id=?",
            (status, note, _iso(now), display_id))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def demote_to_backlog(ids: list[int], *, now: datetime,
                      path: str | None = None) -> None:
    """Cold-start overflow (PRD J1 / Tara #1): everything past the first
    digest's cap of 30 waits in 'backlog' — visible in the emailed
    initial_backlog.md, absent from later delta digests."""
    if not ids:
        return
    con = _connect(path)
    try:
        con.execute(
            f"UPDATE jobs SET status='backlog', digested_at=? WHERE id IN "
            f"({','.join('?' for _ in ids)})", [_iso(now), *ids])
        con.commit()
    finally:
        con.close()


def sample_rejects(*, seed: str, n: int = 20, days: int = 7,
                   now: datetime, path: str | None = None) -> list[dict]:
    """The Sunday rejects sample (Tara #4): `n` random drops from the
    last `days` days, seeded by `seed` so the pick is deterministic and
    the behavior is testable."""
    con = _connect(path)
    try:
        cutoff = _iso(now - timedelta(days=days))
        rows = [dict(r) for r in con.execute(
            "SELECT id, org, title, location, reject_reason FROM jobs "
            "WHERE filter_result='reject' AND last_seen >= ? ORDER BY id",
            (cutoff,)).fetchall()]
    finally:
        con.close()
    rng = random.Random(seed)
    return rng.sample(rows, min(n, len(rows)))


def purge_old_rejects(*, now: datetime, days: int = 90,
                      path: str | None = None) -> int:
    con = _connect(path)
    try:
        cur = con.execute(
            "DELETE FROM jobs WHERE filter_result='reject' AND last_seen < ?",
            (_iso(now - timedelta(days=days)),))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def log_digest(kind: str, *, now: datetime, meta: dict | None = None,
               path: str | None = None) -> None:
    con = _connect(path)
    try:
        con.execute("INSERT INTO digest_log (kind, sent_at, meta) "
                    "VALUES (?,?,?)",
                    (kind, _iso(now), json.dumps(meta or {})))
        con.commit()
    finally:
        con.close()


def last_digest(kind: str, path: str | None = None) -> str | None:
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT sent_at FROM digest_log WHERE kind=? "
            "ORDER BY sent_at DESC LIMIT 1", (kind,)).fetchone()
        return row["sent_at"] if row else None
    finally:
        con.close()
