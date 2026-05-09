"""core.decisions — append-only trace log for the agent mesh.

Every meaningful action in the runtime gets a row here:
    - inbound message routed by Miya
    - agent.route() called
    - tool invocation (telegram_send, llm_generate, ledger write)
    - charter verdict (approved / modified / vetoed)
    - tick fire that produced a reply

A `trace_id` ties multi-step decisions together. With 20 agents this is
the difference between "I can debug Tuesday 9pm" and "no idea what
happened."

API is deliberately tiny:
    new_trace()          → fresh trace_id (uuid4)
    log(actor, op, ...)  → append a row
    tail(n=50)           → return the last n rows
    by_trace(trace_id)   → return all rows for that trace, ordered

The table is created on first import — idempotent. Lives alongside the
existing projections (`raw_vitals`, `governance_log`, etc.) in the same
SQLite file. Read-heavy queries are cheap because of the two indexes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from core import io as cio


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           DATETIME DEFAULT CURRENT_TIMESTAMP,
    trace_id     TEXT NOT NULL,
    span_id      TEXT,
    parent_id    TEXT,
    actor        TEXT NOT NULL,
    op           TEXT NOT NULL,
    input_json   TEXT,
    output_json  TEXT,
    latency_ms   INTEGER,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost_usd     REAL,
    outcome      TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS decisions_trace ON decisions(trace_id);
CREATE INDEX IF NOT EXISTS decisions_actor_ts ON decisions(actor, ts DESC);
"""


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


def new_trace() -> str:
    """Mint a fresh trace_id. Use one per inbound user message / sensor event."""
    return uuid.uuid4().hex


def log(actor: str,
        op: str,
        *,
        trace_id: str,
        span_id: str | None = None,
        parent_id: str | None = None,
        input: Any = None,
        output: Any = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        outcome: str = "ok",
        error: str | None = None,
        db_path: str | None = None) -> int:
    """Append a decision row. Returns the inserted decision_id.

    Failures are swallowed and printed — never let observability crash
    the runtime. Use `outcome="error"` and `error=...` to record agent
    failures; reserve thrown exceptions for "the trace log itself broke."
    """
    try:
        con = cio.db(db_path) if db_path else cio.db()
        try:
            _ensure_schema(con)
            cur = con.execute(
                "INSERT INTO decisions (trace_id, span_id, parent_id, actor, op, "
                "input_json, output_json, latency_ms, tokens_in, tokens_out, "
                "cost_usd, outcome, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trace_id, span_id, parent_id, actor, op,
                 json.dumps(input, default=str) if input is not None else None,
                 json.dumps(output, default=str) if output is not None else None,
                 latency_ms, tokens_in, tokens_out,
                 cost_usd, outcome, error))
            con.commit()
            return cur.lastrowid or -1
        finally:
            con.close()
    except Exception as e:
        print(f"[decisions] log failed: {e}")
        return -1


def tail(n: int = 50, *, actor: str | None = None,
         db_path: str | None = None) -> list[dict]:
    """Return the most recent n decision rows. Filter by actor if given."""
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        # Order by ts DESC, decision_id DESC — the secondary key is the
        # tiebreaker for rows that landed in the same wall-clock second
        # (DEFAULT CURRENT_TIMESTAMP is second-resolution in SQLite).
        # Without it, callers that assume "newest first" see undefined
        # order for sub-second bursts. by_trace() has the same tiebreaker
        # in ASC; this is the matching DESC pair.
        if actor:
            rows = con.execute(
                "SELECT * FROM decisions WHERE actor=? "
                "ORDER BY ts DESC, decision_id DESC LIMIT ?",
                (actor, n)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM decisions "
                "ORDER BY ts DESC, decision_id DESC LIMIT ?",
                (n,)).fetchall()
        cols = [d[0] for d in con.execute("SELECT * FROM decisions LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


def by_trace(trace_id: str, *, db_path: str | None = None) -> list[dict]:
    """Return all rows for a trace_id, oldest first."""
    con = cio.db(db_path) if db_path else cio.db()
    try:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM decisions WHERE trace_id=? ORDER BY ts ASC, decision_id ASC",
            (trace_id,)).fetchall()
        cols = [d[0] for d in con.execute("SELECT * FROM decisions LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        con.close()


# ─────────────────────────── Convenience: span context manager ───────────────────────────
class span:
    """Context manager that times a block and logs one decision row.

        with span("scientist.route", trace_id=tid, input=msg) as s:
            s.output = sci.route(msg)
            # latency captured automatically; outcome=error on exception
    """

    def __init__(self, op: str, *, trace_id: str, actor: str = "?",
                 input: Any = None, parent_id: str | None = None,
                 db_path: str | None = None):
        self.op = op
        self.trace_id = trace_id
        self.actor = actor
        self.input = input
        self.parent_id = parent_id
        self.db_path = db_path
        self.span_id = uuid.uuid4().hex
        self.output: Any = None
        self.outcome = "ok"
        self.error: str | None = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.cost_usd: float | None = None
        self._start: datetime | None = None

    def __enter__(self) -> "span":
        self._start = datetime.utcnow()
        return self

    def __exit__(self, exc_type, exc_val, _tb) -> bool:
        latency = None
        if self._start:
            latency = int(
                (datetime.utcnow() - self._start).total_seconds() * 1000)
        if exc_val is not None:
            self.outcome = "error"
            self.error = f"{exc_type.__name__}: {exc_val}"
        log(self.actor, self.op,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_id=self.parent_id,
            input=self.input,
            output=self.output,
            latency_ms=latency,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost_usd,
            outcome=self.outcome,
            error=self.error,
            db_path=self.db_path)
        return False  # propagate exceptions
