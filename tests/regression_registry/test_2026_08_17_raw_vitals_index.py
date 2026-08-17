"""Perf pin (2026-08-17) — raw_vitals is indexed, self-applied by ingest.

FOUND during the aggregate-vs-raw storage review: raw_vitals had NO
index at all, so every consumer — Kobe's daily-kcal SUM, weight
lookups, the Scientist's reads, every future Huberman trend query —
was a full table scan. Invisible at 37K rows; a per-question drag at
HAE's ~5K rows/day (~1.8M rows/yr). The owner's instinct was to
aggregate-and-prune; the decision was to keep raw data (rollups are
lossy + irreversible, and Huberman's read shapes don't exist yet) and
index instead.

THE PINS.
  * _ensure_schema creates idx_raw_vitals_metric_ts on
    (metric_type, timestamp) — and _ensure_schema runs inside EVERY
    ingest, so the live DB migrates itself on the next HAE sync with
    no manual step. Idempotent: repeated ingests don't error.
  * The index actually serves Kobe's kcal query shape (equality on
    metric_type) — asserted via EXPLAIN QUERY PLAN, not vibes.
  * A pre-existing DB (created before this commit) gains the index on
    first ingest — the migration path IS the write path.

COORDINATION: raw_vitals is a shared table (Architect B's agents may
read it). An index changes performance only, never results — but this
pin documents the shape so nobody re-indexes it differently by
accident.
"""
from __future__ import annotations

import sqlite3

import pytest

from bridges.healthkit.ingest import ingest_payload


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    c = sqlite3.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def _ingest_something(c: sqlite3.Connection) -> None:
    ingest_payload({"data": {"metrics": [
        {"name": "step_count",
         "data": [{"date": "2026-08-17 10:00:00", "qty": 500}]}]}}, c)


def _index_names(c: sqlite3.Connection) -> set[str]:
    return {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='raw_vitals'")}


def test_ingest_creates_the_index(con):
    _ingest_something(con)
    assert "idx_raw_vitals_metric_ts" in _index_names(con)


def test_reingest_is_idempotent_on_the_index(con):
    _ingest_something(con)
    _ingest_something(con)                     # IF NOT EXISTS — no error
    assert "idx_raw_vitals_metric_ts" in _index_names(con)


def test_preexisting_unindexed_db_migrates_on_first_ingest(con):
    """The live-DB path: table exists from the old listener era, no
    index. The next sync's ingest must add it."""
    con.execute("CREATE TABLE raw_vitals "
                "(metric_type TEXT, value REAL, timestamp TEXT)")
    con.execute("INSERT INTO raw_vitals VALUES "
                "('active_calories', 900.0, '2026-08-16 23:59:00')")
    assert _index_names(con) == set()          # genuinely unindexed
    _ingest_something(con)
    assert "idx_raw_vitals_metric_ts" in _index_names(con)


def test_kobes_kcal_query_shape_uses_the_index(con):
    _ingest_something(con)
    plan = " ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT COALESCE(SUM(value),0) FROM raw_vitals "
        "WHERE metric_type='active_calories' "
        "AND substr(timestamp,1,10)='2026-08-16'"))
    assert "idx_raw_vitals_metric_ts" in plan, (
        f"Kobe's query is still a table scan: {plan}")
