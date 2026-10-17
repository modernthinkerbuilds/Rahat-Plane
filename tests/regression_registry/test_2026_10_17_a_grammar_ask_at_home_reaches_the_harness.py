"""Regression pin — 2026-10-17: with the harness serving, a grammar ask
(`/coffee`, `/eat`, `/dessert`, `/save`, "more", "which one", "something
else", "just me", "going") reaches the harness as the words it stands
for; the grammar's lookups stay in code.

LIVE 2026-10-17 (the household at home, RAHAT_BOURDAIN_HARNESS=serve):
`/coffee` ran the trip-era pipeline, which has no leg at home and asked
"Where are you?"; the answer, a town, went to the harness, which had
never heard the ask. Two brains, two memories. From here the grammar is
a typed entry into one brain: `/coffee near downtown` is handed over as
"coffee near downtown", `/eat` as "where should we eat", `/eat lunch` as
"lunch", `/save X, a friend` as "save X, a friend", and an exact reply
as itself. `/list`, `/who`, `/cost`, `/kb` and help are answered by code.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from agents.bourdain import handler, harness_bridge  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 10)       # a Saturday at home: no trip leg


@pytest.fixture
def serve(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_BOURDAIN_HARNESS", "serve")
    seen: list[str] = []

    def fake_answer(text, *, chat_id, now, holding_line=None):
        seen.append(text)
        return "harness: " + text

    monkeypatch.setattr(harness_bridge, "answer", fake_answer)
    monkeypatch.setattr(handler, "handle_saved", lambda cid, args, *, now, db_path=None: "LIST " + (args or "").strip(),
                        raising=False)
    monkeypatch.setattr(handler, "handle_who", lambda cid, *, now: "WHO")
    return seen


@pytest.mark.parametrize("sent, handed", [
    ("/coffee", "coffee"),
    ("/coffee near downtown", "coffee near downtown"),
    ("/coffee@SomeBot", "coffee"),
    ("/eat", "where should we eat"),
    ("/eat lunch", "lunch"),
    ("/eat pizza tonight", "pizza tonight"),
    ("/dessert", "dessert"),
    ("/save Deli X, a friend swears by it", "save Deli X, a friend swears by it"),
    ("more", "more"),
    ("Which one?", "Which one?"),
    ("something else", "something else"),
    ("just me", "just me"),
    ("going", "going"),
    ("where should we eat", "where should we eat"),
])
def test_a_grammar_ask_is_handed_to_the_harness_as_words(serve, sent, handed):
    out = handler.route(sent, chat_id="111", now=NOW)
    assert serve == [handed], (sent, serve)
    assert out == "harness: " + handed
    assert "Where are you" not in out


@pytest.mark.parametrize("sent, expect", [
    ("/list", "LIST "),
    ("/list new york", "LIST new york"),
    ("my list", "LIST "),
    ("saved places", "LIST "),
    ("/who", "WHO"),
    ("/where", "WHO"),
    ("hi", handler.HELP),
    ("/help", handler.HELP),
    ("/unknown thing", handler.HELP),
    ("", handler.HELP),
])
def test_the_lookups_are_answered_by_code(serve, sent, expect):
    out = handler.route(sent, chat_id="111", now=NOW)
    assert serve == [], (sent, serve)
    assert out == expect


def test_a_bare_save_command_asks_what_to_save(serve):
    out = handler.route("/save", chat_id="111", now=NOW)
    assert serve == [] and "/save <name>" in out


def test_the_words_a_command_stands_for():
    assert handler.ask_for("/eat", "") == "where should we eat"
    assert handler.ask_for("/eat", "dinner, something cozy") == "dinner, something cozy"
    assert handler.ask_for("/coffee", "") == "coffee"
    assert handler.ask_for("/dessert", "near the park") == "dessert near the park"
    assert handler.ask_for("/save", "Deli X, a friend") == "save Deli X, a friend"


def test_off_mode_still_answers_the_grammar_in_code(monkeypatch, tmp_path):
    """The legacy pipeline (off and shadow modes) is unchanged: this pin is about serve mode."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_BOURDAIN_HARNESS", raising=False)
    called = []
    monkeypatch.setattr(harness_bridge, "answer", lambda *a, **k: called.append(a) or "no")
    out = handler.route("/coffee", chat_id="111", now=NOW)
    assert not called and isinstance(out, str) and out
