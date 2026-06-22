"""SQLite-backed signal store — isolated from old-plane decisions table.

Path: ``$OPENCLAW_SIGNALS_DB`` if set, else ``~/.rahat/new_plane_signals.db``.
Tests override via ``set_db_path(...)``.

Schema (idempotent — ``CREATE TABLE IF NOT EXISTS``)::

    CREATE TABLE signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        agent        TEXT    NOT NULL,
        type         TEXT    NOT NULL,
        payload_json TEXT    NOT NULL DEFAULT '{}',
        ts           TEXT    NOT NULL,                  -- ISO 8601 UTC
        trace_id     TEXT    NOT NULL,
        consumed_by_json TEXT NOT NULL DEFAULT '[]'    -- JSON array of agent names
    );
    CREATE INDEX signals_agent_ts ON signals(agent, ts);
    CREATE INDEX signals_trace    ON signals(trace_id);

This file is intentionally small and dependency-free (stdlib only). Treat
it as a contract surface — see ``specs/ARCHITECT_THREADS_2026-05-30.md``
for the rules. Schema changes require coordination with the new-plane
architect.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None
_LOCK = threading.Lock()


# ─── Path resolution ───────────────────────────────────────────────────────
def _default_path() -> Path:
    # Explicit override always wins (tests set this to a tmp path).
    p = os.environ.get("OPENCLAW_SIGNALS_DB", "").strip()
    if p:
        return Path(p)
    # Test-mode sandbox guard (mirrors core.io): when RAHAT_TEST_MODE=1 and no
    # explicit path is set, never touch the real signals DB — use a pid-scoped
    # temp file. Closes the same class of risk as the 2026-05-08 live-DB
    # corruption for paths that forget to call set_db_path().
    if os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes"):
        import tempfile
        return Path(tempfile.gettempdir()) / f"rahat_signals_test_{os.getpid()}.db"
    return Path.home() / ".rahat" / "new_plane_signals.db"


def set_db_path(path: str | Path) -> None:
    """Override the DB path (used by tests). Resets the cached path; the
    next call to ``init_db()`` or any public function will use the new path."""
    global _DB_PATH
    _DB_PATH = Path(path)


def _path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = _default_path()
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    return con


# ─── Schema ────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent            TEXT    NOT NULL,
    type             TEXT    NOT NULL,
    payload_json     TEXT    NOT NULL DEFAULT '{}',
    ts               TEXT    NOT NULL,
    trace_id         TEXT    NOT NULL,
    consumed_by_json TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS signals_agent_ts ON signals(agent, ts);
CREATE INDEX IF NOT EXISTS signals_trace    ON signals(trace_id);
"""


# PF-2026-06-10-006 — additive migration to scope signals by chat_id.
# Nullable so legacy rows ('chat_id IS NULL') are treated as global
# (the single-user / single-chat era). New publishes set it explicitly.
_MIGRATIONS = [
    # name, idempotent-DDL
    (
        "add_chat_id_to_signals",
        """
        ALTER TABLE signals ADD COLUMN chat_id TEXT;
        CREATE INDEX IF NOT EXISTS signals_chat_ts ON signals(chat_id, ts);
        """,
    ),
]


def _apply_migrations(con: sqlite3.Connection) -> None:
    """Idempotent best-effort column additions. SQLite's ALTER TABLE ADD
    COLUMN errors if the column already exists, so each step is wrapped
    in try/except — succeed-or-already-applied is the only success."""
    for name, ddl in _MIGRATIONS:
        try:
            con.executescript(ddl)
            con.commit()
        except sqlite3.OperationalError:
            # Column already present — that's the desired terminal state.
            pass


def init_db() -> None:
    """Ensure the signals DB exists with the right schema. Idempotent."""
    with _LOCK:
        con = _connect()
        try:
            con.executescript(_SCHEMA)
            _apply_migrations(con)
            con.commit()
        finally:
            con.close()


# ─── Public dataclass ─────────────────────────────────────────────────────
@dataclass
class Signal:
    id: int
    agent: str
    type: str
    payload: dict[str, Any]
    ts: str
    trace_id: str
    consumed_by: list[str]
    chat_id: str | None = None  # PF-006

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_signal(row: sqlite3.Row) -> Signal:
    # `chat_id` is a post-launch additive column; pre-migration rows
    # return None via the row mapping. Defensively pull with .get on a
    # dict view since sqlite3.Row doesn't support .get directly.
    try:
        chat_id = row["chat_id"]
    except (IndexError, KeyError):
        chat_id = None
    return Signal(
        id=row["id"],
        agent=row["agent"],
        type=row["type"],
        payload=json.loads(row["payload_json"] or "{}"),
        ts=row["ts"],
        trace_id=row["trace_id"],
        consumed_by=json.loads(row["consumed_by_json"] or "[]"),
        chat_id=chat_id,
    )


# ─── Publish ──────────────────────────────────────────────────────────────
def publish(*, agent: str, type_: str, payload: dict[str, Any],
            trace_id: str, chat_id: str | None = None,
            ts: str | None = None) -> int:
    """Append a signal. Returns the new signal_id.

    `chat_id` (PF-006) scopes signals per chat for concurrent-chat
    safety. Optional — pre-migration callers continue to publish global
    signals (`chat_id IS NULL`).
    `ts` defaults to UTC-now in ISO 8601 with microsecond precision.
    """
    if not agent or not type_:
        raise ValueError("agent and type_ are required")
    ts = ts or (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z")
    payload_json = json.dumps(payload, default=str)
    init_db()
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO signals (agent, type, payload_json, ts, trace_id, chat_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent, type_, payload_json, ts, trace_id, chat_id),
            )
            con.commit()
            return int(cur.lastrowid or -1)
        finally:
            con.close()


# ─── Read ──────────────────────────────────────────────────────────────────
def recent(*, agent: str | None = None, type_: str | None = None,
           trace_id: str | None = None, chat_id: str | None = None,
           limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent signals (newest first), filtered by any
    combination of agent / type / trace_id / chat_id. Returns serialized
    dicts so HTTP callers can stream them straight to the wire.

    PF-006: when `chat_id` is supplied, only signals from that chat OR
    legacy chat-agnostic signals (`chat_id IS NULL`) are returned — so
    new code is scoped while old global signals remain visible until
    they age out.
    """
    init_db()
    where: list[str] = []
    args: list[Any] = []
    if agent:
        where.append("agent = ?")
        args.append(agent)
    if type_:
        where.append("type = ?")
        args.append(type_)
    if trace_id:
        where.append("trace_id = ?")
        args.append(trace_id)
    if chat_id is not None:
        # Match this chat exactly OR legacy global rows (NULL chat_id).
        where.append("(chat_id = ? OR chat_id IS NULL)")
        args.append(chat_id)
    sql = "SELECT * FROM signals"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
    return [_row_to_signal(r).to_dict() for r in rows]


# ─── Consumption (the load-bearing half) ──────────────────────────────────
def mark_consumed(signal_id: int, consumer_agent: str) -> bool:
    """Record that ``consumer_agent`` *used* this signal in a decision.

    Per the PM thesis v1.1 contract: cross-pollination only counts when
    a reader folds a signal into a decision. The contract test
    (eventually) asserts that every published signal has at least one
    consumer; signals that are published but never consumed are the
    failure mode.

    Returns True if the consumer was newly added, False if it was already
    in the consumed_by list, raises KeyError if the signal_id is unknown.
    """
    if not consumer_agent:
        raise ValueError("consumer_agent is required")
    init_db()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT consumed_by_json FROM signals WHERE id = ?",
                (signal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"signal_id {signal_id} not found")
            consumed = json.loads(row["consumed_by_json"] or "[]")
            if consumer_agent in consumed:
                return False
            consumed.append(consumer_agent)
            con.execute(
                "UPDATE signals SET consumed_by_json = ? WHERE id = ?",
                (json.dumps(consumed), signal_id),
            )
            con.commit()
            return True
        finally:
            con.close()


# ─── Diagnostics ───────────────────────────────────────────────────────────
def unconsumed_count(*, agent: str | None = None) -> int:
    """How many published signals have ``consumed_by = []`` — i.e., never
    used in a downstream decision. The cross-pollination health gauge."""
    init_db()
    sql = "SELECT COUNT(*) AS n FROM signals WHERE consumed_by_json IN ('[]', '')"
    args: list[Any] = []
    if agent:
        sql += " AND agent = ?"
        args.append(agent)
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(sql, args).fetchone()
        finally:
            con.close()
    return int(row["n"]) if row else 0
