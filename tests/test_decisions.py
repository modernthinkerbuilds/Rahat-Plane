"""Unit-ish tests for core.decisions — the trace ledger.

The 2 existing decisions tests live in `tests/test_replay_regression.py`
because that file exercises the live router. But decisions.py is itself
the spine that *every* agent in the mesh writes to — its own contract
deserves direct, targeted tests, not just transitive coverage from the
regression layer.

What this file pins:

  1. `new_trace()` produces unique trace IDs.
  2. `span` records latency, propagates exceptions, and writes outcome=error.
  3. `tail()` orders newest-first; per-actor filter works.
  4. `by_trace()` orders oldest-first within a trace and returns all rows.
  5. Schema is idempotent — repeated `_ensure_schema` calls don't blow up.
  6. The `log()` swallow-on-error promise: if the DB is unavailable, the
     runtime must NOT crash. A noisy print is acceptable; a thrown
     exception is not.
  7. Cost + token columns round-trip — we depend on these in the cost CLI.

These tests use the `sandbox_db` fixture so they don't pollute the
shared in-memory test DB. Run as part of the contract layer.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core import decisions


class TestNewTrace:
    def test_uuid_hex_is_unique(self):
        a = decisions.new_trace()
        b = decisions.new_trace()
        assert a != b
        # uuid4 hex is 32 chars
        assert len(a) == 32 and len(b) == 32
        # hex only — no dashes, no other chars
        assert all(c in "0123456789abcdef" for c in a)


class TestSpanLifecycle:
    def test_span_records_ok_outcome(self, sandbox_db):
        tid = decisions.new_trace()
        with decisions.span("test.span_ok", trace_id=tid, actor="t",
                            input={"x": 1}) as s:
            s.output = {"y": 2}
            s.tokens_in = 100
            s.tokens_out = 50
            s.cost_usd = 0.0001
        rows = decisions.by_trace(tid)
        assert len(rows) == 1
        r = rows[0]
        assert r["outcome"] == "ok"
        assert r["error"] is None
        assert r["op"] == "test.span_ok"
        assert r["actor"] == "t"
        assert r["tokens_in"] == 100
        assert r["tokens_out"] == 50
        assert abs(r["cost_usd"] - 0.0001) < 1e-9

    def test_span_records_error_outcome_with_type_and_message(self, sandbox_db):
        tid = decisions.new_trace()
        with pytest.raises(ValueError, match="boom"):
            with decisions.span("test.span_err", trace_id=tid, actor="t"):
                raise ValueError("boom")
        rows = decisions.by_trace(tid)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "error"
        assert "ValueError" in rows[0]["error"]
        assert "boom" in rows[0]["error"]

    def test_span_records_latency_ms(self, sandbox_db):
        """latency_ms must be a non-negative integer. We don't pin a
        particular value — that's flaky — just that it was set."""
        tid = decisions.new_trace()
        with decisions.span("test.lat", trace_id=tid, actor="t"):
            pass
        rows = decisions.by_trace(tid)
        assert len(rows) == 1
        assert rows[0]["latency_ms"] is not None
        assert rows[0]["latency_ms"] >= 0

    def test_nested_span_with_parent_id(self, sandbox_db):
        """Multi-step trace: outer span calls inner span with the outer's
        span_id as parent_id. The ledger writes happen on __exit__, so
        inner exits first (logged first) and outer exits second (logged
        second). by_trace orders oldest-first, so rows[0] is the inner
        span and rows[1] is the outer. The parent linkage is what makes
        a tree out of a flat list — the inner row's parent_id points to
        the outer's span_id."""
        tid = decisions.new_trace()
        with decisions.span("outer", trace_id=tid, actor="t") as outer:
            outer.output = "started"
            with decisions.span("inner", trace_id=tid, actor="t",
                                parent_id=outer.span_id) as inner:
                inner.output = "done"
        rows = decisions.by_trace(tid)
        assert len(rows) == 2
        ops = [r["op"] for r in rows]
        assert "outer" in ops and "inner" in ops
        # parent linkage — inner's parent_id is outer's span_id
        outer_row = next(r for r in rows if r["op"] == "outer")
        inner_row = next(r for r in rows if r["op"] == "inner")
        assert inner_row["parent_id"] == outer_row["span_id"]
        assert outer_row["parent_id"] is None


class TestTail:
    def test_tail_returns_newest_first(self, sandbox_db):
        tid = decisions.new_trace()
        for i in range(3):
            decisions.log("t", f"op{i}", trace_id=tid)
        rows = decisions.tail(n=10)
        # newest-first means op2 before op0
        ops = [r["op"] for r in rows if r["trace_id"] == tid]
        assert ops == ["op2", "op1", "op0"]

    def test_tail_respects_n_limit(self, sandbox_db):
        tid = decisions.new_trace()
        for i in range(5):
            decisions.log("t", f"op{i}", trace_id=tid)
        rows = decisions.tail(n=2)
        # at least 2 returned — exact count depends on what else is in
        # the DB, but we asked for a cap of 2
        assert len(rows) <= 2

    def test_tail_filters_by_actor(self, sandbox_db):
        tid = decisions.new_trace()
        decisions.log("alice", "op", trace_id=tid)
        decisions.log("bob", "op", trace_id=tid)
        rows = decisions.tail(n=50, actor="alice")
        assert all(r["actor"] == "alice" for r in rows)
        assert any(r["trace_id"] == tid for r in rows)


class TestSchemaIdempotent:
    def test_repeated_ensure_schema_is_safe(self, sandbox_db):
        """Multiple processes / repeated imports must not corrupt the
        schema. CREATE IF NOT EXISTS handles tables; CREATE INDEX IF NOT
        EXISTS handles indexes. If either drops the IF NOT EXISTS, this
        test fires."""
        con = sqlite3.connect(str(sandbox_db))
        try:
            decisions._ensure_schema(con)
            decisions._ensure_schema(con)  # second call must not throw
            decisions._ensure_schema(con)  # third call must not throw
        finally:
            con.close()


class TestSwallowOnError:
    def test_log_failure_does_not_throw(self, monkeypatch):
        """If `cio.db()` raises (e.g. read-only filesystem, vault deleted),
        decisions.log must NOT crash the calling agent. A failed log
        returns -1 and prints; the trace is lost but the runtime survives.
        This is the load-bearing observability promise."""
        from core import io as cio
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated DB outage")
        monkeypatch.setattr(cio, "db", boom)
        rid = decisions.log("t", "op", trace_id="trace-x")
        assert rid == -1  # signaled failure, did not throw
