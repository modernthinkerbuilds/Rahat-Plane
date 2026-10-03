"""core.state_scopes — five scopes for every fact an agent remembers
(ADR-018 §2.7, platform PR-S).

    turn      this ask only
    session   the conversation: the last answer, an in-flight ask, a tightened gate
    day       overrides and exclusions until midnight local
    trip      a leg's stay, schedule, plan and saves
    standing  profiles, household rules, regulars

Every write names its scope and gets an expiry from it; reads filter by
`now`. A today-only fact never becomes standing here: promotion is a
separate, explicit call that records the verdict that justified it
(the Charter gates it in the controller). Rows live in the same SQLite
file as the other platform tables, so `RAHAT_TEST_MODE=1` sandboxes
them, and they replace the lockless JSON session store.

    from core import state_scopes as ss
    ss.put("bourdain", chat, "override", {"who": ["spouse"], "rule": "no egg"},
           scope="day", now=now, source_trace=tid)
    ss.get_all("bourdain", chat, "override", now=now)   # only the ones in force
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import io as cio

SCOPES = ("turn", "session", "day", "trip", "standing")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS state_scoped (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    owner TEXT NOT NULL,            -- chat id, member id or leg id
    key TEXT NOT NULL,              -- override | exclusion | last_answer | in_flight | location | party | speaker | rule | ...
    scope TEXT NOT NULL,
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,                -- NULL = never (standing)
    source_trace TEXT,
    promoted_from INTEGER,          -- row_id of the day/trip fact this was promoted from
    withdrawn_at TEXT               -- set when explicitly withdrawn ("egg is back")
);
CREATE INDEX IF NOT EXISTS state_scoped_lookup ON state_scoped(agent, owner, key, scope);
"""


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    con = cio.db(db_path)
    con.executescript(_SCHEMA)
    return con


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def expiry_for(scope: str, now: datetime, *, session_hours: float = 12.0,
               trip_end: datetime | None = None) -> str | None:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if scope == "turn":
        return _fmt(now + timedelta(minutes=5))
    if scope == "session":
        return _fmt(now + timedelta(hours=session_hours))
    if scope == "day":
        return _fmt(now.replace(hour=23, minute=59, second=59, microsecond=0))
    if scope == "trip":
        return _fmt(trip_end) if trip_end else None
    return None


def put(agent: str, owner: str, key: str, value: Any, *, scope: str, now: datetime,
        source_trace: str | None = None, replace: bool = False,
        trip_end: datetime | None = None, db_path: str | Path | None = None) -> int:
    """Write one fact in a scope. `replace=True` withdraws earlier live
    rows with the same key and scope first (the last answer, a location
    override); the default appends (overrides and exclusions stack)."""
    con = _connect(db_path)
    try:
        if replace:
            con.execute("UPDATE state_scoped SET withdrawn_at = ? WHERE agent = ? AND owner = ? "
                        "AND key = ? AND scope = ? AND withdrawn_at IS NULL",
                        (_fmt(now), agent, owner, key, scope))
        cur = con.execute(
            "INSERT INTO state_scoped (agent, owner, key, scope, value_json, created_at, expires_at, "
            "source_trace) VALUES (?,?,?,?,?,?,?,?)",
            (agent, owner, key, scope, json.dumps(value, ensure_ascii=False, default=str),
             _fmt(now), expiry_for(scope, now, trip_end=trip_end), source_trace))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get_all(agent: str, owner: str, key: str, *, now: datetime,
            scope: str | None = None, db_path: str | Path | None = None) -> list[dict]:
    """Every live row for (agent, owner, key): not withdrawn, not expired."""
    con = _connect(db_path)
    try:
        sql = ("SELECT row_id, scope, value_json, created_at, expires_at, source_trace, promoted_from "
               "FROM state_scoped WHERE agent = ? AND owner = ? AND key = ? AND withdrawn_at IS NULL "
               "AND (expires_at IS NULL OR expires_at >= ?)")
        args: list = [agent, owner, key, _fmt(now)]
        if scope:
            sql += " AND scope = ?"; args.append(scope)
        rows = con.execute(sql + " ORDER BY row_id", args).fetchall()
    finally:
        con.close()
    return [{"row_id": r[0], "scope": r[1], "value": json.loads(r[2]), "created_at": r[3],
             "expires_at": r[4], "source_trace": r[5], "promoted_from": r[6]} for r in rows]


def get(agent: str, owner: str, key: str, *, now: datetime, scope: str | None = None,
        db_path: str | Path | None = None) -> Any:
    """The newest live value, or None."""
    rows = get_all(agent, owner, key, now=now, scope=scope, db_path=db_path)
    return rows[-1]["value"] if rows else None


def withdraw(row_id: int, *, now: datetime, db_path: str | Path | None = None) -> bool:
    con = _connect(db_path)
    try:
        cur = con.execute("UPDATE state_scoped SET withdrawn_at = ? WHERE row_id = ? AND withdrawn_at IS NULL",
                          (_fmt(now), row_id))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def promote(row_id: int, *, to_scope: str, now: datetime, justified_by: str,
            db_path: str | Path | None = None) -> int:
    """Copy a day or trip fact into a wider scope, recording the verdict
    that justified it. The caller has already cleared the Charter and
    shown the confirm card; this function only records."""
    if to_scope not in ("trip", "standing"):
        raise ValueError("promotion targets are trip or standing")
    con = _connect(db_path)
    try:
        r = con.execute("SELECT agent, owner, key, scope, value_json FROM state_scoped WHERE row_id = ?",
                        (row_id,)).fetchone()
        if not r:
            raise KeyError(row_id)
        agent, owner, key, scope, value_json = r
        if SCOPES.index(to_scope) <= SCOPES.index(scope):
            raise ValueError(f"cannot promote from {scope} to {to_scope}")
        cur = con.execute(
            "INSERT INTO state_scoped (agent, owner, key, scope, value_json, created_at, expires_at, "
            "source_trace, promoted_from) VALUES (?,?,?,?,?,?,?,?,?)",
            (agent, owner, key, to_scope, value_json, _fmt(now), expiry_for(to_scope, now),
             justified_by, row_id))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def sweep(*, now: datetime, db_path: str | Path | None = None) -> int:
    """Mark expired rows withdrawn (housekeeping; reads already filter)."""
    con = _connect(db_path)
    try:
        cur = con.execute("UPDATE state_scoped SET withdrawn_at = ? WHERE withdrawn_at IS NULL "
                          "AND expires_at IS NOT NULL AND expires_at < ?", (_fmt(now), _fmt(now)))
        con.commit()
        return cur.rowcount
    finally:
        con.close()
