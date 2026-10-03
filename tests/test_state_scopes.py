"""core.state_scopes — five scopes with expiry; promotion is explicit
and records its justification (ADR-018 §2.7)."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import state_scopes as ss  # noqa: E402

NOW = datetime(2026, 10, 3, 18, 40, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "scopes.db")


def test_day_scope_expires_at_midnight(db):
    rid = ss.put("bourdain", "chat1", "override", {"who": ["spouse"], "rule": "no egg"}, scope="day",
                 now=NOW, source_trace="t1", db_path=db)
    assert rid > 0
    assert ss.get("bourdain", "chat1", "override", now=NOW, db_path=db) == {"who": ["spouse"], "rule": "no egg"}
    assert ss.get("bourdain", "chat1", "override", now=NOW.replace(hour=23, minute=59, second=58), db_path=db)
    assert ss.get("bourdain", "chat1", "override", now=datetime(2026, 10, 4, 0, 0, 1), db_path=db) is None


def test_overrides_stack_and_partial_withdrawal(db):
    a = ss.put("bourdain", "c", "override", {"who": ["spouse"], "rule": "no egg"}, scope="day", now=NOW, db_path=db)
    ss.put("bourdain", "c", "override", {"who": ["spouse"], "rule": "no chicken"}, scope="day", now=NOW, db_path=db)
    assert [r["value"]["rule"] for r in ss.get_all("bourdain", "c", "override", now=NOW, db_path=db)] == ["no egg", "no chicken"]
    assert ss.withdraw(a, now=NOW, db_path=db)
    assert [r["value"]["rule"] for r in ss.get_all("bourdain", "c", "override", now=NOW, db_path=db)] == ["no chicken"]
    assert not ss.withdraw(a, now=NOW, db_path=db)          # already withdrawn


def test_session_replace_keeps_one_last_answer(db):
    ss.put("bourdain", "c", "last_answer", {"picks": ["a"]}, scope="session", now=NOW, replace=True, db_path=db)
    ss.put("bourdain", "c", "last_answer", {"picks": ["b"]}, scope="session", now=NOW, replace=True, db_path=db)
    rows = ss.get_all("bourdain", "c", "last_answer", now=NOW, db_path=db)
    assert len(rows) == 1 and rows[0]["value"] == {"picks": ["b"]}
    assert rows[0]["expires_at"] == "2026-10-04 06:40:00"      # 12 h session


def test_standing_never_expires_and_scope_filter(db):
    ss.put("bourdain", "household", "rule", {"text": "no chains"}, scope="standing", now=NOW, db_path=db)
    rows = ss.get_all("bourdain", "household", "rule", now=datetime(2030, 1, 1), db_path=db)
    assert rows and rows[0]["expires_at"] is None
    assert ss.get_all("bourdain", "household", "rule", now=NOW, scope="day", db_path=db) == []


def test_promotion_records_justification_and_refuses_downward(db):
    rid = ss.put("bourdain", "household", "rule", {"text": "mexican means a taqueria"}, scope="day", now=NOW, db_path=db)
    new = ss.promote(rid, to_scope="standing", now=NOW, justified_by="verdict:42", db_path=db)
    rows = ss.get_all("bourdain", "household", "rule", now=datetime(2027, 1, 1), scope="standing", db_path=db)
    assert rows[0]["row_id"] == new and rows[0]["promoted_from"] == rid and rows[0]["source_trace"] == "verdict:42"
    with pytest.raises(ValueError):
        ss.promote(new, to_scope="trip", now=NOW, db_path=db, justified_by="x")
    with pytest.raises(ValueError):
        ss.promote(rid, to_scope="session", now=NOW, db_path=db, justified_by="x")


def test_unknown_scope_rejected(db):
    with pytest.raises(ValueError):
        ss.put("a", "b", "k", 1, scope="forever", now=NOW, db_path=db)


def test_sweep_marks_expired(db):
    ss.put("a", "b", "k", 1, scope="turn", now=NOW, db_path=db)
    assert ss.sweep(now=NOW, db_path=db) == 0
    assert ss.sweep(now=datetime(2026, 10, 3, 19, 0), db_path=db) == 1
