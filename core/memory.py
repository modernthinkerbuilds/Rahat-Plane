"""core.memory — universal memory substrate for the Rahat agent mesh.

Five primitives, agent-agnostic, all in SQLite:

    events         — append-only firehose of meaningful occurrences
                     (messages, tool calls, decisions, vital readings).
                     Time-stamped, agent-scoped, optionally entity-tagged.
    entities       — first-class objects with lifecycle. Each agent
                     defines its own types via JSON payload. Status ∈
                     active / superseded / expired / archived.
    threads        — conversation threads scoped per (agent, topic).
                     Holds summary, open questions, decisions.
    preferences    — sticky k/v per agent with confidence decay.
    relationships  — entity-to-entity links, can cross agents.

Plus a sixth (in `core/archival.py`) for long-term semantic memory.

Design notes (see specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md §7):
    - The substrate is intentionally agent-agnostic. The Scientist's
      "active goal" is just `entity(type='goal', agent='scientist')`.
    - Default queries are agent-scoped. Cross-agent reads go through
      Miya as a permission/visibility broker.
    - Auto-migrates on first import — same pattern as `core/decisions.py`.
    - Pure-Python DAL, no ORM. SQLite parameterized queries throughout.
    - Every memory write also lands in the events stream for observability.

The DAL functions return plain dicts (or lists of dicts) — no custom
classes — so call sites can JSON-serialize freely.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from core import io as cio


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            DATETIME DEFAULT CURRENT_TIMESTAMP,
    agent         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload       TEXT,
    actor         TEXT,
    related_ids   TEXT,
    trace_id      TEXT
);
CREATE INDEX IF NOT EXISTS memory_events_agent_ts ON memory_events(agent, ts DESC);
CREATE INDEX IF NOT EXISTS memory_events_kind_ts  ON memory_events(kind, ts DESC);
CREATE INDEX IF NOT EXISTS memory_events_trace    ON memory_events(trace_id);

CREATE TABLE IF NOT EXISTS memory_entities (
    entity_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    type           TEXT NOT NULL,
    payload        TEXT NOT NULL,
    status         TEXT DEFAULT 'active',
    valid_from     DATETIME DEFAULT CURRENT_TIMESTAMP,
    valid_until    DATETIME,
    superseded_by  INTEGER,
    rationale      TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS memory_entities_agent_type ON memory_entities(agent, type, status);
CREATE INDEX IF NOT EXISTS memory_entities_active     ON memory_entities(agent, status, valid_until);

CREATE TABLE IF NOT EXISTS memory_threads (
    thread_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    topic          TEXT NOT NULL,
    started_at     DATETIME NOT NULL,
    last_active_at DATETIME NOT NULL,
    summary        TEXT,
    open_questions TEXT,
    decisions      TEXT,
    status         TEXT DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS memory_threads_agent ON memory_threads(agent, status, last_active_at DESC);

CREATE TABLE IF NOT EXISTS memory_preferences (
    agent          TEXT NOT NULL,
    key            TEXT NOT NULL,
    value          TEXT NOT NULL,
    confidence     REAL DEFAULT 1.0,
    learned_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent, key)
);

CREATE TABLE IF NOT EXISTS memory_relationships (
    rel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a      INTEGER NOT NULL,
    entity_b      INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    metadata      TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS memory_relationships_a ON memory_relationships(entity_a);
CREATE INDEX IF NOT EXISTS memory_relationships_b ON memory_relationships(entity_b);
"""


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection. Auto-migrates on first call. Caller closes."""
    con = cio.db(db_path) if db_path else cio.db()
    _ensure_schema(con)
    return con


def _row_to_dict(cur: sqlite3.Cursor, row: tuple) -> dict:
    return {d[0]: v for d, v in zip(cur.description, row)}


def _parse_payload(s: str | None) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


# ─────────────────────────── Events ───────────────────────────
def add_event(agent: str, kind: str,
              *,
              payload: Any = None,
              actor: str | None = None,
              entity_ids: list[int] | None = None,
              trace_id: str | None = None,
              db_path: str | None = None) -> int:
    """Append an event. Returns the new event_id.

    `kind` is agent-defined: 'msg.in', 'msg.out', 'tool.call',
    'commitment.made', 'vital.hrv', 'plan.committed', etc.

    Failures are swallowed and printed (same policy as decisions.log) —
    observability must never crash the runtime.
    """
    try:
        con = _connect(db_path)
        try:
            cur = con.execute(
                "INSERT INTO memory_events "
                "(agent, kind, payload, actor, related_ids, trace_id) "
                "VALUES (?,?,?,?,?,?)",
                (agent, kind,
                 json.dumps(payload, default=str) if payload is not None else None,
                 actor,
                 json.dumps(entity_ids) if entity_ids else None,
                 trace_id))
            con.commit()
            return cur.lastrowid or -1
        finally:
            con.close()
    except Exception as e:
        print(f"[memory.add_event] failed: {e}")
        return -1


def recent_events(agent: str,
                  *,
                  since_minutes: int | None = None,
                  since_hours: int | None = None,
                  kinds: list[str] | None = None,
                  limit: int = 50,
                  db_path: str | None = None) -> list[dict]:
    """Read recent events for an agent, optionally filtered by kind."""
    where = ["agent = ?"]
    params: list[Any] = [agent]
    if since_minutes is not None:
        where.append("ts >= datetime('now', ?)")
        params.append(f"-{int(since_minutes)} minutes")
    elif since_hours is not None:
        where.append("ts >= datetime('now', ?)")
        params.append(f"-{int(since_hours)} hours")
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        where.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    sql = (f"SELECT * FROM memory_events WHERE {' AND '.join(where)} "
           f"ORDER BY event_id DESC LIMIT ?")
    params.append(int(limit))
    con = _connect(db_path)
    try:
        cur = con.execute(sql, params)
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        for r in rows:
            r["payload"] = _parse_payload(r.get("payload"))
            r["related_ids"] = _parse_payload(r.get("related_ids")) or []
        return rows
    finally:
        con.close()


# ─────────────────────────── Entities ───────────────────────────
def put_entity(agent: str, type: str,
               payload: Any,
               *,
               valid_from: datetime | None = None,
               valid_until: datetime | None = None,
               rationale: str | None = None,
               status: str = "active",
               supersede_existing: bool = True,
               db_path: str | None = None) -> int:
    """Insert a new entity. Returns entity_id.

    `supersede_existing=True` (default) marks any other active entity
    with the same (agent, type) as superseded — useful for "current
    goal" / "current plan" patterns where only one is active at a time.
    Pass False for entities where multiple active is OK (e.g. a list of
    commitments).
    """
    payload_json = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
    con = _connect(db_path)
    try:
        if supersede_existing:
            # Mark prior actives as superseded.
            con.execute(
                "UPDATE memory_entities SET status='superseded', "
                "       updated_at=CURRENT_TIMESTAMP "
                "WHERE agent=? AND type=? AND status='active'",
                (agent, type))
        cur = con.execute(
            "INSERT INTO memory_entities "
            "(agent, type, payload, status, valid_from, valid_until, "
            " rationale) VALUES (?,?,?,?,?,?,?)",
            (agent, type, payload_json, status,
             valid_from.isoformat() if valid_from else None,
             valid_until.isoformat() if valid_until else None,
             rationale))
        new_id = cur.lastrowid or -1
        con.commit()
    finally:
        con.close()
    add_event(agent, f"entity.{type}.put",
              payload={"entity_id": new_id, "rationale": rationale},
              db_path=db_path)
    return new_id


def list_entities(agent: str,
                  *,
                  type: str | None = None,
                  status: str | None = "active",
                  include_expired: bool = False,
                  limit: int = 50,
                  db_path: str | None = None) -> list[dict]:
    """List entities for an agent. By default returns only active,
    non-expired entities."""
    where = ["agent = ?"]
    params: list[Any] = [agent]
    if type:
        where.append("type = ?")
        params.append(type)
    if status:
        where.append("status = ?")
        params.append(status)
    if not include_expired:
        where.append("(valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)")
    sql = (f"SELECT * FROM memory_entities WHERE {' AND '.join(where)} "
           f"ORDER BY entity_id DESC LIMIT ?")
    params.append(int(limit))
    con = _connect(db_path)
    try:
        cur = con.execute(sql, params)
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        for r in rows:
            r["payload"] = _parse_payload(r.get("payload"))
        return rows
    finally:
        con.close()


def get_entity(entity_id: int,
               *,
               db_path: str | None = None) -> dict | None:
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT * FROM memory_entities WHERE entity_id = ?",
            (entity_id,))
        row = cur.fetchone()
        if not row:
            return None
        out = _row_to_dict(cur, row)
        out["payload"] = _parse_payload(out.get("payload"))
        return out
    finally:
        con.close()


def update_entity(entity_id: int,
                  *,
                  payload: Any = None,
                  status: str | None = None,
                  valid_until: datetime | None = None,
                  rationale: str | None = None,
                  db_path: str | None = None) -> None:
    """Update fields on an existing entity. None values are ignored."""
    sets: list[str] = ["updated_at=CURRENT_TIMESTAMP"]
    params: list[Any] = []
    if payload is not None:
        sets.append("payload = ?")
        params.append(json.dumps(payload, default=str)
                      if not isinstance(payload, str) else payload)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if valid_until is not None:
        sets.append("valid_until = ?")
        params.append(valid_until.isoformat())
    if rationale is not None:
        sets.append("rationale = ?")
        params.append(rationale)
    params.append(entity_id)
    con = _connect(db_path)
    try:
        con.execute(f"UPDATE memory_entities SET {', '.join(sets)} "
                    f"WHERE entity_id = ?", params)
        con.commit()
    finally:
        con.close()


def supersede_entity(entity_id: int,
                     *,
                     reason: str | None = None,
                     db_path: str | None = None) -> None:
    """Mark an entity as superseded — explicit retirement, distinct
    from automatic supersession by put_entity()."""
    update_entity(entity_id, status="superseded",
                  rationale=reason, db_path=db_path)


def cross_agent_list(type: str | None = None,
                     status: str | None = "active",
                     limit: int = 50,
                     db_path: str | None = None) -> list[dict]:
    """List entities across ALL agents — used by Miya as a broker for
    cross-agent reasoning. Default callers should NOT use this; prefer
    list_entities(agent=...) so visibility is explicit."""
    where = []
    params: list[Any] = []
    if type:
        where.append("type = ?")
        params.append(type)
    if status:
        where.append("status = ?")
        params.append(status)
    where.append("(valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)")
    sql = (f"SELECT * FROM memory_entities WHERE {' AND '.join(where)} "
           f"ORDER BY entity_id DESC LIMIT ?")
    params.append(int(limit))
    con = _connect(db_path)
    try:
        cur = con.execute(sql, params)
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        for r in rows:
            r["payload"] = _parse_payload(r.get("payload"))
        return rows
    finally:
        con.close()


# ─────────────────────────── Threads ───────────────────────────
def thread_for(agent: str, topic: str,
               *,
               db_path: str | None = None) -> dict:
    """Get or create a thread for (agent, topic). Returns the row dict.
    Updates last_active_at on get."""
    now = datetime.utcnow().isoformat()
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT * FROM memory_threads WHERE agent=? AND topic=? "
            "AND status='open' ORDER BY thread_id DESC LIMIT 1",
            (agent, topic))
        row = cur.fetchone()
        if row:
            out = _row_to_dict(cur, row)
            con.execute(
                "UPDATE memory_threads SET last_active_at=? "
                "WHERE thread_id=?",
                (now, out["thread_id"]))
            con.commit()
        else:
            cur = con.execute(
                "INSERT INTO memory_threads "
                "(agent, topic, started_at, last_active_at) "
                "VALUES (?,?,?,?)",
                (agent, topic, now, now))
            con.commit()
            tid = cur.lastrowid or -1
            cur = con.execute(
                "SELECT * FROM memory_threads WHERE thread_id=?", (tid,))
            out = _row_to_dict(cur, cur.fetchone())
        out["open_questions"] = _parse_payload(out.get("open_questions")) or []
        out["decisions"] = _parse_payload(out.get("decisions")) or {}
        return out
    finally:
        con.close()


def update_thread(thread_id: int,
                  *,
                  summary: str | None = None,
                  open_questions: list | None = None,
                  decisions: dict | None = None,
                  status: str | None = None,
                  db_path: str | None = None) -> None:
    sets: list[str] = ["last_active_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []
    if summary is not None:
        sets.append("summary = ?")
        params.append(summary)
    if open_questions is not None:
        sets.append("open_questions = ?")
        params.append(json.dumps(open_questions, default=str))
    if decisions is not None:
        sets.append("decisions = ?")
        params.append(json.dumps(decisions, default=str))
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    params.append(thread_id)
    con = _connect(db_path)
    try:
        con.execute(f"UPDATE memory_threads SET {', '.join(sets)} "
                    f"WHERE thread_id = ?", params)
        con.commit()
    finally:
        con.close()


def most_recent_thread(agent: str,
                       *,
                       status: str = "open",
                       db_path: str | None = None) -> dict | None:
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT * FROM memory_threads WHERE agent=? AND status=? "
            "ORDER BY last_active_at DESC LIMIT 1",
            (agent, status))
        row = cur.fetchone()
        if not row:
            return None
        out = _row_to_dict(cur, row)
        out["open_questions"] = _parse_payload(out.get("open_questions")) or []
        out["decisions"] = _parse_payload(out.get("decisions")) or {}
        return out
    finally:
        con.close()


# ─────────────────────────── Preferences ───────────────────────────
def upsert_pref(agent: str, key: str, value: Any,
                *,
                confidence: float = 1.0,
                db_path: str | None = None) -> None:
    """Upsert a preference. Increments last_seen on existing rows.
    Confidence is a float [0..1]; the caller decides how to set it."""
    val_json = (json.dumps(value, default=str)
                if not isinstance(value, str) else value)
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO memory_preferences "
            "(agent, key, value, confidence) VALUES (?,?,?,?) "
            "ON CONFLICT(agent, key) DO UPDATE SET "
            "  value = excluded.value, "
            "  confidence = excluded.confidence, "
            "  last_seen = CURRENT_TIMESTAMP",
            (agent, key, val_json, confidence))
        con.commit()
    finally:
        con.close()


def get_pref(agent: str, key: str,
             *,
             default: Any = None,
             db_path: str | None = None) -> Any:
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT value FROM memory_preferences WHERE agent=? AND key=?",
            (agent, key))
        row = cur.fetchone()
        return _parse_payload(row[0]) if row else default
    finally:
        con.close()


def list_prefs(agent: str,
               *,
               min_confidence: float = 0.0,
               limit: int = 50,
               db_path: str | None = None) -> list[dict]:
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT * FROM memory_preferences "
            "WHERE agent=? AND confidence >= ? "
            "ORDER BY last_seen DESC LIMIT ?",
            (agent, min_confidence, int(limit)))
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        for r in rows:
            r["value"] = _parse_payload(r.get("value"))
        return rows
    finally:
        con.close()


def decay_prefs(agent: str | None = None,
                *,
                factor: float = 0.95,
                older_than_days: int = 7,
                db_path: str | None = None) -> int:
    """Sleep-time use: decay confidence on prefs not reinforced lately.
    Returns the number of prefs that were decayed."""
    where = ["last_seen < datetime('now', ?)"]
    params: list[Any] = [f"-{int(older_than_days)} days"]
    if agent:
        where.append("agent = ?")
        params.append(agent)
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"UPDATE memory_preferences SET confidence = confidence * ? "
            f"WHERE {' AND '.join(where)}",
            [factor] + params)
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


# ─────────────────────────── Relationships ───────────────────────────
def link(entity_a: int, entity_b: int, kind: str,
         *,
         metadata: dict | None = None,
         db_path: str | None = None) -> int:
    """Create an entity-to-entity link. Returns rel_id."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO memory_relationships "
            "(entity_a, entity_b, kind, metadata) VALUES (?,?,?,?)",
            (entity_a, entity_b, kind,
             json.dumps(metadata, default=str) if metadata else None))
        con.commit()
        return cur.lastrowid or -1
    finally:
        con.close()


def neighbors(entity_id: int,
              *,
              kind: str | None = None,
              db_path: str | None = None) -> list[dict]:
    """Find entities linked to/from this one."""
    where = ["(entity_a = ? OR entity_b = ?)"]
    params: list[Any] = [entity_id, entity_id]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"SELECT * FROM memory_relationships WHERE {' AND '.join(where)} "
            f"ORDER BY rel_id DESC",
            params)
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        for r in rows:
            r["metadata"] = _parse_payload(r.get("metadata"))
        return rows
    finally:
        con.close()


# ─────────────────────────── Convenience ───────────────────────────
def stats(agent: str | None = None,
          *,
          db_path: str | None = None) -> dict:
    """Quick totals — useful for /memory debug endpoints and smoke
    tests. Returns counts per table, optionally filtered by agent."""
    con = _connect(db_path)
    try:
        result: dict[str, Any] = {}
        for table in ("memory_events", "memory_entities",
                      "memory_threads", "memory_preferences",
                      "memory_relationships"):
            if table in ("memory_relationships",):
                # No agent column.
                cur = con.execute(f"SELECT COUNT(*) FROM {table}")
            elif agent:
                cur = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE agent = ?",
                    (agent,))
            else:
                cur = con.execute(f"SELECT COUNT(*) FROM {table}")
            result[table] = cur.fetchone()[0]
        return result
    finally:
        con.close()
