"""§1.2 — `new_plane/miya_runner/pending.py` unit tests (previously zero
coverage, PRE_SCALE G).

Pins: record→latest round-trip, 60s TTL prune, reply resolution semantics
(digit / ordinal / affirmative / negative / first-word), clear(), and
restart durability (the queue is signal-store-backed, so it survives a
process bounce — proven by re-pointing the store at the same file).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from new_plane.signals import store
from new_plane.miya_runner import pending


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    yield db


# ─── enqueue / dequeue ────────────────────────────────────────────────
def test_record_then_latest_roundtrips():
    sid = pending.record(chat_id="c1", question="Run or rest?",
                         options=["Run", "Rest"])
    assert sid > 0
    p = pending.latest("c1")
    assert p is not None
    assert p["payload"]["options"] == ["Run", "Rest"]


def test_record_rejects_empty_options():
    with pytest.raises(ValueError):
        pending.record(chat_id="c1", question="?", options=[])


def test_latest_is_scoped_per_chat():
    pending.record(chat_id="A", question="qA", options=["a"])
    pending.record(chat_id="B", question="qB", options=["b"])
    assert pending.latest("A")["payload"]["question"] == "qA"
    assert pending.latest("B")["payload"]["question"] == "qB"


def test_newest_pending_supersedes_older():
    pending.record(chat_id="c1", question="old", options=["x"])
    pending.record(chat_id="c1", question="new", options=["y"])
    assert pending.latest("c1")["payload"]["question"] == "new"


# ─── TTL prune ────────────────────────────────────────────────────────
def test_ttl_prunes_expired_pending(monkeypatch):
    pending.record(chat_id="c1", question="q", options=["a", "b"])
    # Move "now" 61s into the future → the 60s TTL window has passed.
    real_now = pending._now()

    monkeypatch.setattr(pending, "_now",
                        lambda: real_now + timedelta(seconds=61))
    assert pending.latest("c1") is None
    # ... and a fresh-enough window still resolves.
    monkeypatch.setattr(pending, "_now",
                        lambda: real_now + timedelta(seconds=30))
    assert pending.latest("c1") is not None


def test_resolve_returns_none_when_expired(monkeypatch):
    pending.record(chat_id="c1", question="q", options=["Run", "Rest"])
    real_now = pending._now()
    monkeypatch.setattr(pending, "_now",
                        lambda: real_now + timedelta(seconds=120))
    assert pending.resolve("c1", "1") is None


# ─── resolve semantics ────────────────────────────────────────────────
@pytest.mark.parametrize("reply,expected", [
    ("1", "Run"), ("2", "Rest"),
    ("first", "Run"), ("second", "Rest"), ("two", "Rest"),
    ("yes", "Run"), ("sure", "Run"), ("ok", "Run"),
    ("no", None), ("skip", None),
    ("Run", "Run"), ("rest", "Rest"),  # case-insensitive exact
    ("99", None),                      # out-of-range digit
    ("maybe", None),                   # no match
])
def test_resolve_matches(reply, expected):
    pending.record(chat_id="c1", question="Run or rest?",
                   options=["Run", "Rest"])
    assert pending.resolve("c1", reply) == expected


def test_resolve_first_word_label():
    pending.record(chat_id="c1", question="pick",
                   options=["A) hill sprints", "B) zone 2"])
    assert pending.resolve("c1", "A") == "A) hill sprints"


def test_resolve_no_pending_returns_none():
    assert pending.resolve("nochat", "yes") is None


# ─── clear ────────────────────────────────────────────────────────────
def test_clear_disables_resolution():
    pending.record(chat_id="c1", question="q", options=["Run", "Rest"])
    assert pending.clear("c1") == 1
    # After clear, the newest pending has empty options → no resolution.
    assert pending.resolve("c1", "1") is None


def test_clear_when_nothing_pending_is_zero():
    assert pending.clear("c1") == 0


# ─── restart durability ───────────────────────────────────────────────
def test_pending_survives_process_bounce(tmp_path, monkeypatch):
    """The pending queue is SQLite-backed, so it must survive a 'restart'
    — modeled by dropping the cached path and re-pointing at the same file
    (what a fresh process does on boot)."""
    db = tmp_path / "durable.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(db))
    store.set_db_path(db)
    store.init_db()
    pending.record(chat_id="c1", question="q", options=["Run", "Rest"])

    # Simulate a process bounce: clear the cached path, reopen the same file.
    store._DB_PATH = None
    store.set_db_path(db)
    store.init_db()

    assert pending.resolve("c1", "2") == "Rest", (
        "pending did not survive a restart — durability is broken"
    )
