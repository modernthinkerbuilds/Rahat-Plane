"""core.episodes — episodic memory primitives.

An *episode* is a bounded slice of the user's life that one or more
agents care about. Examples:

    - Scientist:   weight cycle ("196 lb → 185 lb")
    - Coach:       training block ("PRVN squat block, 4 weeks")
    - Curriculum:  developmental phase ("toddler week 14: motor milestone")
    - Concierge:   trip ("Japan, Jan 2026")
    - Foodie:      cleanse ("no red meat, 30 days")

Without this primitive, each of those agents will create its own
phases / cycles / blocks table, and at agent #15 we'll have eight
near-duplicates and no unified "what was happening on March 14."

API is deliberately tiny — open / close / note / list. The CLI is
deferred (Next phase) because the consumers are agents, not humans.

Tables:
    episodes
        id, kind, subject, started_at, ended_at, entities_json, status
    episode_notes
        id, episode_id, ts, actor, text, payload_json
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from core import io as cio


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT 'self',
    started_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at      DATETIME,
    status        TEXT DEFAULT 'open',
    entities_json TEXT,
    UNIQUE(kind, subject, started_at)
);
CREATE INDEX IF NOT EXISTS episodes_kind_status ON episodes(kind, status);
CREATE INDEX IF NOT EXISTS episodes_subject ON episodes(subject);

CREATE TABLE IF NOT EXISTS episode_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id   INTEGER NOT NULL,
    ts           DATETIME DEFAULT CURRENT_TIMESTAMP,
    actor        TEXT NOT NULL,
    text         TEXT,
    payload_json TEXT,
    FOREIGN KEY(episode_id) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS notes_episode_ts ON episode_notes(episode_id, ts DESC);
"""


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


# ─────────────────────────── Open / close ───────────────────────────
def open(kind: str,
         *,
         subject: str = "self",
         entities: dict | None = None,
         started_at: datetime | None = None,
         db_path: str | None = None) -> int:
    """Open a new episode. Returns the episode id.

    If an episode with the same (kind, subject) is already `open`,
    returns its existing id rather than creating a duplicate. To force a
    new one, close the prior episode first.
    """
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        existing = con.execute(
            "SELECT id FROM episodes WHERE kind=? AND subject=? AND status='open' "
            "ORDER BY started_at DESC LIMIT 1",
            (kind, subject)).fetchone()
        if existing:
            return existing[0]
        cur = con.execute(
            "INSERT INTO episodes (kind, subject, started_at, entities_json) "
            "VALUES (?, ?, ?, ?)",
            (kind, subject,
             (started_at or datetime.utcnow()).isoformat(timespec="seconds"),
             json.dumps(entities or {})))
        con.commit()
        return cur.lastrowid or -1
    finally:
        con.close()


def close(episode_id: int,
          *,
          ended_at: datetime | None = None,
          status: str = "closed",
          db_path: str | None = None) -> None:
    """Mark an episode as ended. status defaults to 'closed' but agents
    may use 'abandoned' or domain-specific values like 'achieved'.
    """
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        con.execute(
            "UPDATE episodes SET ended_at=?, status=? WHERE id=?",
            ((ended_at or datetime.utcnow()).isoformat(timespec="seconds"),
             status, episode_id))
        con.commit()
    finally:
        con.close()


# ─────────────────────────── Notes ───────────────────────────
def note(episode_id: int,
         *,
         actor: str,
         text: str = "",
         payload: dict | None = None,
         ts: datetime | None = None,
         db_path: str | None = None) -> int:
    """Append a note to an episode. Returns the note id.

    `text` is for human-readable context; `payload` is for structured
    data agents may reason over later (e.g. {"weight_lbs": 192.4}).
    """
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        cur = con.execute(
            "INSERT INTO episode_notes (episode_id, ts, actor, text, payload_json) "
            "VALUES (?,?,?,?,?)",
            (episode_id,
             (ts or datetime.utcnow()).isoformat(timespec="seconds"),
             actor, text or None,
             json.dumps(payload) if payload is not None else None))
        con.commit()
        return cur.lastrowid or -1
    finally:
        con.close()


# ─────────────────────────── Lookup ───────────────────────────
def get(episode_id: int, *, db_path: str | None = None) -> dict | None:
    """Fetch one episode + its notes. Returns None if not found."""
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT id, kind, subject, started_at, ended_at, status, entities_json "
            "FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not row:
            return None
        notes = con.execute(
            "SELECT id, ts, actor, text, payload_json FROM episode_notes "
            "WHERE episode_id=? ORDER BY ts ASC, id ASC",
            (episode_id,)).fetchall()
        return {
            "id": row[0], "kind": row[1], "subject": row[2],
            "started_at": row[3], "ended_at": row[4], "status": row[5],
            "entities": json.loads(row[6] or "{}"),
            "notes": [
                {"id": n[0], "ts": n[1], "actor": n[2],
                 "text": n[3],
                 "payload": json.loads(n[4]) if n[4] else None}
                for n in notes
            ],
        }
    finally:
        con.close()


def list_open(kind: str | None = None, *, subject: str = "self",
              db_path: str | None = None) -> list[dict]:
    """Return open episodes, optionally filtered by kind."""
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        if kind:
            rows = con.execute(
                "SELECT id, kind, subject, started_at, entities_json "
                "FROM episodes WHERE status='open' AND kind=? AND subject=? "
                "ORDER BY started_at DESC", (kind, subject)).fetchall()
        else:
            rows = con.execute(
                "SELECT id, kind, subject, started_at, entities_json "
                "FROM episodes WHERE status='open' AND subject=? "
                "ORDER BY started_at DESC", (subject,)).fetchall()
        return [
            {"id": r[0], "kind": r[1], "subject": r[2],
             "started_at": r[3],
             "entities": json.loads(r[4] or "{}")}
            for r in rows
        ]
    finally:
        con.close()


def find(kind: str, *, subject: str = "self",
         active_at: datetime | None = None,
         db_path: str | None = None) -> dict | None:
    """Return the episode of `kind` for `subject` that was active at
    `active_at` (default: now). Useful for "what training block was I
    in on March 14?" lookups.
    """
    when = (active_at or datetime.utcnow()).isoformat(timespec="seconds")
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT id FROM episodes WHERE kind=? AND subject=? "
            "AND started_at <= ? AND (ended_at IS NULL OR ended_at >= ?) "
            "ORDER BY started_at DESC LIMIT 1",
            (kind, subject, when, when)).fetchone()
        if not row:
            return None
        return get(row[0], db_path=db_path)
    finally:
        con.close()
