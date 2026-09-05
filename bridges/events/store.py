"""Events inventory store — deduped, replay-safe, freshness-aware.

Table `events_inventory` in the vitals DB (or RAHAT_EVENTS_DB override):
one row per (normalized title, date, city) — the PRD's blocking key —
so the same event via Funcheap AND the library feed merges instead of
duplicating.

Freshness (PRD §6.3 'silent cancellations'): every refresh stamps
last_seen per source; a FUTURE event unseen across the source's recent
PRODUCTIVE refreshes flips to status='suspect' and is excluded from
default queries — "seen before, gone on re-crawl → mark suspect".

Suspect rule rework (2026-08-24, owner: "genie isn't picking up events
from sites like linden, home depot, local libraries"): the original
rule — unseen across the last TWO refreshes, any refreshes — silently
erased the future calendar. Live evidence: 1,123 suspect rows vs 571
active; SJPL's real upcoming storytimes ALL suspect; linden-tree,
mv-city, broadway-sj with ZERO active future events. Two causes, two
fixes:
  * An extractor failure is not a cancellation. A refresh that fetched
    ZERO events (LLM outage, page 404, feed hiccup) proves nothing
    about any event — it no longer counts as evidence. Only
    PRODUCTIVE refreshes (fetched > 0) advance the suspect clock.
  * Grounded-search recall FLAPS: each refresh surfaces a different
    subset, so a real event routinely skips two refreshes. Search-kind
    sources now need FOUR consecutive productive misses (~1.3 days at
    3 refreshes/day) before suspicion; deterministic kinds (ical,
    page) keep the tight window of two — when the whole feed is
    in-context, absence twice really does mean gone.
Re-seen events still resurrect to 'active' on the spot (unchanged).
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import unicodedata
from datetime import datetime

# Consecutive PRODUCTIVE refreshes an event must miss before 'suspect'.
_SUSPECT_MISSES = {"ical": 2, "page": 2, "search": 4}


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
    # 2026-08-24: the suspect clock needs to know whether a refresh was
    # PRODUCTIVE. Legacy rows keep NULL and count as productive so the
    # live DB's history doesn't reset the clock on upgrade.
    try:
        con.execute("ALTER TABLE events_refresh_log "
                    "ADD COLUMN fetched INTEGER")
    except sqlite3.OperationalError:
        pass                                 # column already exists
    # Curation (2026-09-03): one row per recurring series — how popular
    # / well-reviewed it is online, looked up ONCE and reused.
    con.execute("""CREATE TABLE IF NOT EXISTS events_popularity (
        series_key TEXT PRIMARY KEY,
        score INTEGER, evidence TEXT, audience TEXT,
        checked_at TEXT)""")
    return con


def _norm(s: str) -> str:
    # NFKD accent-fold first (2026-08-24): "San José" and "San Jose"
    # were hashing to different event keys, duplicating every SJPL row
    # that arrived spelled both ways.
    s = unicodedata.normalize("NFKD", s or "").encode(
        "ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_SERIES_NOISE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b|"
    r"\b\d{1,4}(?:st|nd|rd|th)?\b|\b20\d\d\b|\bweek\s*\d+\b|"
    r"\b(?:part|session|day)\s+\d+\b", re.I)


def series_key(title: str, venue: str = "") -> str:
    """Identity of a RECURRING SERIES: the title with dates / weekdays /
    numbers stripped, plus the venue. "Music on Castro — Aug 26" and
    "Music on Castro — Sep 2" collapse to one series; "Storytime with
    <author>" (one-off) stays its own key. Curation (2026-09-03) keys
    recurrence detection and the popularity cache on this."""
    t = _SERIES_NOISE.sub(" ", title or "")
    return f"{_norm(t)}|{_norm(venue)}"


def event_key(title: str, start_ts: str, city: str) -> str:
    blob = f"{_norm(title)}|{(start_ts or '')[:10]}|{_norm(city)}"
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def upsert_events(events: list[dict], source_id: str, *,
                  now: datetime | None = None,
                  path: str | None = None,
                  source_kind: str = "search") -> dict:
    """Idempotent write of one source's refresh. Returns counts.
    `source_kind` sets the suspect window (see _SUSPECT_MISSES)."""
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
                    "refreshed_at, fetched) VALUES (?, ?, ?)",
                    (source_id, now_iso, len(events)))
        # Silent-cancellation heuristic (reworked 2026-08-24, see module
        # docstring): a future event from THIS source unseen across its
        # last N PRODUCTIVE refreshes → suspect. Zero-yield refreshes
        # are extractor failures, not evidence; NULL = legacy row,
        # counted productive.
        n_miss = _SUSPECT_MISSES.get(source_kind, 4)
        refreshes = [r[0] for r in con.execute(
            "SELECT refreshed_at FROM events_refresh_log WHERE "
            "source_id = ? AND (fetched IS NULL OR fetched > 0) "
            "ORDER BY refreshed_at DESC LIMIT ?",
            (source_id, n_miss)).fetchall()]
        if len(refreshes) == n_miss:
            con.execute(
                "UPDATE events_inventory SET status = 'suspect' WHERE "
                "source_id = ? AND status = 'active' AND start_ts > ? "
                "AND last_seen < ?",
                (source_id, now_iso, refreshes[-1]))
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



# ─────────────────────────── curation reads/writes ───────────────────
def recurrence_counts(path: str | None = None) -> dict[str, int]:
    """series_key → number of DISTINCT dates the series has appeared on,
    across the whole inventory (past rows included, any status). ≥2 =
    a recurring series with a track record worth looking up."""
    con = _connect(path)
    try:
        rows = con.execute("SELECT title, venue, date(start_ts) "
                           "FROM events_inventory").fetchall()
    finally:
        con.close()
    dates: dict[str, set[str]] = {}
    for title, venue, d in rows:
        if not d:
            continue
        dates.setdefault(series_key(title or "", venue or ""), set()).add(d)
    return {k: len(v) for k, v in dates.items()}


def get_popularity(keys: list[str],
                   path: str | None = None) -> dict[str, dict]:
    if not keys:
        return {}
    con = _connect(path)
    try:
        out: dict[str, dict] = {}
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            q = ("SELECT series_key, score, evidence, audience, checked_at "
                 "FROM events_popularity WHERE series_key IN (" +
                 ",".join("?" * len(chunk)) + ")")
            for k, sc, ev, au, at in con.execute(q, chunk):
                out[k] = {"score": sc, "evidence": ev or "",
                          "audience": au or "", "checked_at": at or ""}
        return out
    finally:
        con.close()


def set_popularity(key: str, score: int | None, evidence: str,
                   audience: str, *, now: datetime | None = None,
                   path: str | None = None) -> None:
    now_iso = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    con = _connect(path)
    try:
        con.execute("INSERT OR REPLACE INTO events_popularity VALUES "
                    "(?,?,?,?,?)",
                    (key, score, (evidence or "")[:200],
                     (audience or "")[:120], now_iso))
        con.commit()
    finally:
        con.close()
