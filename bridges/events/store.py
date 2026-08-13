"""Events inventory store — deduped, replay-safe, freshness-aware.

Table `events_inventory` in the vitals DB (or RAHAT_EVENTS_DB override):
one row per (normalized title, date, city) — the PRD's blocking key —
so the same event via Funcheap AND the library feed merges instead of
duplicating.

Freshness (PRD §6.3 'silent cancellations'): every refresh stamps
last_seen per source; a FUTURE event whose source has since refreshed
twice without mentioning it flips to status='suspect' and is excluded
from default queries — "seen before, gone on re-crawl → mark suspect".
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime


def db_path() -> str:
    """Resolve the inventory DB path.

    Order: explicit RAHAT_EVENTS_DB → TEST-MODE SANDBOX → RAHAT_VITALS_DB
    → the live vault DB.

    The test-mode branch is load-bearing (caught live 2026-08-12): the
    pre-push gate failed on the owner's Mac because an OLD test called
    handle_whats_on, which now reads the inventory — and with no
    explicit override this fell through to the LIVE vault/rahat.db,
    where that morning's refresh had planted real Santa Cruz rows. A
    test reading (or worse, WRITING) the live DB is the 2026-05-08
    corruption incident class; under RAHAT_TEST_MODE=1 every path must
    resolve inside the sandbox, mirroring genie.state._vault_dir()."""
    explicit = os.getenv("RAHAT_EVENTS_DB")
    if explicit:
        return explicit
    if os.getenv("RAHAT_TEST_MODE") == "1":
        import tempfile
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR") or os.path.join(
            tempfile.gettempdir(), f"rahat_test_{os.getpid()}")
        os.makedirs(sandbox, exist_ok=True)
        return os.path.join(sandbox, "events_test.db")
    return os.getenv("RAHAT_VITALS_DB",
                     os.path.expanduser(
                         "~/developer/agency/rahat/vault/rahat.db"))


def _connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path())
    con.execute("""CREATE TABLE IF NOT EXISTS events_inventory (
        event_id TEXT PRIMARY KEY,
        title TEXT, start_ts TEXT, end_ts TEXT,
        venue TEXT, city TEXT, url TEXT,
        source_id TEXT, categories TEXT,
        status TEXT DEFAULT 'active',
        first_seen TEXT, last_seen TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS events_refresh_log (
        source_id TEXT, refreshed_at TEXT)""")
    return con


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def event_key(title: str, start_ts: str, city: str) -> str:
    blob = f"{_norm(title)}|{(start_ts or '')[:10]}|{_norm(city)}"
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def upsert_events(events: list[dict], source_id: str, *,
                  now: datetime | None = None,
                  path: str | None = None) -> dict:
    """Idempotent write of one source's refresh. Returns counts."""
    now_iso = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    con = _connect(path)
    added = updated = 0
    try:
        for e in events:
            title = str(e.get("title") or "").strip()[:160]
            start = str(e.get("start_ts") or "").strip()[:19]
            if not title or len(start) < 10:
                continue
            key = event_key(title, start, str(e.get("city") or ""))
            row = con.execute("SELECT event_id FROM events_inventory "
                              "WHERE event_id = ?", (key,)).fetchone()
            if row:
                con.execute(
                    "UPDATE events_inventory SET last_seen = ?, "
                    "status = 'active', end_ts = COALESCE(NULLIF(?, ''), "
                    "end_ts), url = COALESCE(NULLIF(?, ''), url) "
                    "WHERE event_id = ?",
                    (now_iso, str(e.get("end_ts") or "")[:19],
                     str(e.get("url") or "")[:300], key))
                updated += 1
            else:
                con.execute(
                    "INSERT INTO events_inventory (event_id, title, "
                    "start_ts, end_ts, venue, city, url, source_id, "
                    "categories, status, first_seen, last_seen) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, title, start, str(e.get("end_ts") or "")[:19],
                     str(e.get("venue") or "")[:120],
                     str(e.get("city") or "")[:60],
                     str(e.get("url") or "")[:300], source_id,
                     ",".join(e.get("categories") or [])[:120],
                     "active", now_iso, now_iso))
                added += 1
        con.execute("INSERT INTO events_refresh_log (source_id, "
                    "refreshed_at) VALUES (?, ?)", (source_id, now_iso))
        # Silent-cancellation heuristic: future events from THIS source
        # not seen across its last two refreshes → suspect.
        refreshes = [r[0] for r in con.execute(
            "SELECT refreshed_at FROM events_refresh_log WHERE "
            "source_id = ? ORDER BY refreshed_at DESC LIMIT 2",
            (source_id,)).fetchall()]
        if len(refreshes) == 2:
            con.execute(
                "UPDATE events_inventory SET status = 'suspect' WHERE "
                "source_id = ? AND status = 'active' AND start_ts > ? "
                "AND last_seen < ?",
                (source_id, now_iso, refreshes[1]))
        con.commit()
    finally:
        con.close()
    return {"added": added, "updated": updated}


def query_window(start_date: str, end_date: str, *,
                 city_like: str | None = None,
                 categories: list[str] | None = None,
                 include_suspect: bool = False,
                 limit: int = 60,
                 path: str | None = None) -> list[dict]:
    """Inventory for a date window (ISO dates, inclusive)."""
    con = _connect(path)
    try:
        sql = ("SELECT title, start_ts, end_ts, venue, city, url, "
               "source_id, categories, status FROM events_inventory "
               "WHERE date(start_ts) >= ? AND date(start_ts) <= ?")
        args: list = [start_date, end_date]
        if not include_suspect:
            sql += " AND status = 'active'"
        if city_like:
            sql += " AND (city LIKE ? OR city = 'Bay Area')"
            args.append(f"%{city_like}%")
        if categories:
            sql += (" AND (" + " OR ".join("categories LIKE ?"
                                           for _ in categories) + ")")
            args += [f"%{c}%" for c in categories]
        sql += " ORDER BY start_ts LIMIT ?"
        args.append(limit)
        cols = ("title", "start_ts", "end_ts", "venue", "city", "url",
                "source_id", "categories", "status")
        return [dict(zip(cols, r)) for r in con.execute(sql, args)]
    finally:
        con.close()


def inventory_stats(path: str | None = None) -> list[tuple]:
    """(source_id, active_count, latest_refresh) — the honest yield view."""
    con = _connect(path)
    try:
        return con.execute(
            "SELECT source_id, SUM(status = 'active'), MAX(last_seen) "
            "FROM events_inventory GROUP BY source_id "
            "ORDER BY 2 DESC").fetchall()
    finally:
        con.close()
