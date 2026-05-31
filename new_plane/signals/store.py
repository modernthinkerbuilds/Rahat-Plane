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
    p = os.environ.get("OPENCLAW_SIGNALS_DB", "").strip()
    if p:
        return Path(p)
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


def init_db() -> None:
    """Ensure the signals DB exists with the right schema. Idempotent."""
    with _LOCK:
        con = _connect()
        try:
            con.executescript(_SCHEMA)
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_signal(row: sqlite3.Row) -> Signal:
    return Signal(
        id=row["id"],
        agent=row["agent"],
        type=row["type"],
        payload=json.loads(row["payload_json"] or "{}"),
        ts=row["ts"],
        trace_id=row["trace_id"],
        consumed_by=json.loads(row["consumed_by_json"] or "[]"),
    )


# ─── Publish ──────────────────────────────────────────────────────────────
def publish(*, agent: str, type_: str, payload: dict[str, Any],
            trace_id: str, ts: str | None = None) -> int:
    """Append a signal. Returns the new signal_id.

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
                "INSERT INTO signals (agent, type, payload_json, ts, trace_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (agent, type_, payload_json, ts, trace_id),
            )
            con.commit()
            return int(cur.lastrowid or -1)
        finally:
            con.close()


# ─── Read ──────────────────────────────────────────────────────────────────
def recent(*, agent: str | None = None, type_: str | None = None,
           trace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent signals (newest first), filtered by any
    combination of agent / type / trace_id. Returns serialized dicts so
    HTTP callers can stream them straight to the wire.
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
