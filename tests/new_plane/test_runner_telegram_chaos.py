"""Telegram poll-loop chaos + edge cases.

`new_plane/miya_runner/telegram.py` (the poller) had thin coverage
(SUITE_MAP §9.6): long-poll timeout, network failure, multi-message
polls, oversized/unicode messages, malformed updates, Markdown-fallback,
and the offset/ chat-filter invariants the `__main__.py` loop relies on.
This file is all hermetic — every HTTP call is monkeypatched; nothing
touches the wire.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import telegram as tg
from new_plane.miya_runner.telegram import (
    TelegramClient, parse_update, _split_for_telegram, _MAX_TG_LEN,
)


def _client():
    return TelegramClient("test-token", expected_chat_id="42")


def _msg(update_id, chat_id, text, *, key="message"):
    return {"update_id": update_id, key: {"text": text, "chat": {"id": chat_id}}}


# ─── get_updates resilience ───────────────────────────────────────────
class TestGetUpdatesChaos:
    def test_long_poll_timeout_returns_empty_no_crash(self, monkeypatch):
        c = _client()
        monkeypatch.setattr(c, "_http", lambda *a, **k: {"ok": True, "result": []})
        assert c.get_updates(offset=5) == []

    def test_network_error_returns_empty(self, monkeypatch):
        # _http swallows URLError/OSError and returns {} — get_updates must
        # then yield [] (no 'ok'), never raise.
        c = _client()
        monkeypatch.setattr(c, "_http", lambda *a, **k: {})
        assert c.get_updates(offset=5) == []

    def test_not_ok_response_returns_empty(self, monkeypatch):
        c = _client()
        monkeypatch.setattr(c, "_http",
                            lambda *a, **k: {"ok": False, "description": "boom"})
        assert c.get_updates(offset=1) == []

    def test_offset_and_timeout_are_passed_through(self, monkeypatch):
        # Offset persistence: the loop advances `offset=last_id+1`; the
        # client must forward it (and the long-poll timeout) verbatim.
        seen = {}

        def fake_http(method, params=None, *, http_timeout=25):
            seen["method"] = method
            seen["params"] = params
            seen["http_timeout"] = http_timeout
            return {"ok": True, "result": []}

        c = _client()
        monkeypatch.setattr(c, "_http", fake_http)
        c.get_updates(offset=99, long_poll_s=10)
        assert seen["method"] == "getUpdates"
        assert seen["params"] == {"offset": 99, "timeout": 10}
        assert seen["http_timeout"] == 25  # long_poll_s + 15

    def test_multi_message_in_one_poll_preserved_in_order(self, monkeypatch):
        batch = [_msg(10, "42", "first"), _msg(11, "42", "second"),
                 _msg(12, "42", "third")]
        c = _client()
        monkeypatch.setattr(c, "_http", lambda *a, **k: {"ok": True, "result": batch})
        got = c.get_updates(offset=10)
        parsed = [parse_update(u) for u in got]
        assert [p.text for p in parsed] == ["first", "second", "third"]
        assert [p.update_id for p in parsed] == [10, 11, 12]


# ─── parse_update edge cases ──────────────────────────────────────────
class TestParseUpdateChaos:
    def test_empty_text_field_returns_none(self):
        # Telegram sends voice/photo updates with no `text`.
        assert parse_update({"update_id": 1,
                             "message": {"chat": {"id": "42"}}}) is None

    def test_missing_chat_id_returns_none(self):
        assert parse_update({"update_id": 1,
                             "message": {"text": "hi"}}) is None

    def test_non_message_update_returns_none(self):
        # inline_query / channel_post / poll updates carry no `message`.
        assert parse_update({"update_id": 1,
                             "inline_query": {"query": "x"}}) is None

    def test_edited_message_is_picked_up(self):
        up = {"update_id": 7, "edited_message": {"text": "fixed typo",
              "chat": {"id": "42"}}}
        tu = parse_update(up)
        assert tu is not None and tu.text == "fixed typo" and tu.update_id == 7

    def test_unicode_emoji_rtl_zwj_preserved(self):
        weird = "pace? 🏃‍♂️‍ ‫مرحبا‬ 👍🏾"
        tu = parse_update(_msg(3, "42", weird))
        assert tu is not None and tu.text == weird

    def test_chat_id_is_stringified(self):
        # __main__.py:234 compares `tu.chat_id != expected_chat_id`; the
        # loop's expected_chat_id is a str (from env). parse_update must
        # also stringify so a numeric chat id from the API still matches.
        tu = parse_update({"update_id": 1,
                           "message": {"text": "hi", "chat": {"id": 42}}})
        assert tu is not None and tu.chat_id == "42" and isinstance(tu.chat_id, str)


# ─── message splitting (4096 hard limit) ──────────────────────────────
class TestMessageSplitting:
    def test_exact_boundary_is_single_chunk(self):
        chunks = list(_split_for_telegram("x" * _MAX_TG_LEN))
        assert len(chunks) == 1

    def test_oversized_single_paragraph_hard_splits(self):
        chunks = list(_split_for_telegram("x" * (_MAX_TG_LEN * 2 + 50)))
        assert len(chunks) >= 3
        assert all(len(c) <= _MAX_TG_LEN for c in chunks)

    @pytest.mark.parametrize("n", [1, 100, _MAX_TG_LEN - 1, _MAX_TG_LEN,
                                   _MAX_TG_LEN + 1, 9001])
    def test_no_chunk_ever_exceeds_limit(self, n):
        text = ("para\n\n" * (n // 6 + 1))[:n] or "x"
        chunks = list(_split_for_telegram(text))
        assert chunks, "split must always yield at least one chunk"
        assert all(len(c) <= _MAX_TG_LEN for c in chunks)


# ─── send_message chunking + Markdown fallback ────────────────────────
class TestSendMessage:
    def test_oversized_text_posts_multiple_chunks(self, monkeypatch):
        posts = []

        def fake_post(method, body):
            posts.append(body)
            return {"ok": True}

        c = _client()
        monkeypatch.setattr(c, "_http_post", fake_post)
        c.send_message("42", "y" * (_MAX_TG_LEN * 2 + 10))
        assert len(posts) >= 2
        assert all(len(p["text"]) <= _MAX_TG_LEN for p in posts)

    def test_markdown_parse_error_falls_back_to_plain(self, monkeypatch):
        posts = []

        def fake_post(method, body):
            posts.append(body)
            # First (Markdown) attempt fails; plain-text retry succeeds.
            return {"ok": "parse_mode" not in body}

        c = _client()
        monkeypatch.setattr(c, "_http_post", fake_post)
        c.send_message("42", "*unbalanced markdown")
        assert len(posts) == 2
        assert "parse_mode" in posts[0]          # first try used Markdown
        assert "parse_mode" not in posts[1]      # retry dropped it


# ─── offset invariant the loop depends on ─────────────────────────────
class TestOffsetInvariant:
    def test_offset_advance_is_monotonic_under_backwards_update_id(self):
        """__main__.py:231-233 advances `last_id = max(last_id, update_id)`
        for every update (even unparseable ones). A Telegram update-id
        reset / out-of-order batch must NOT lower the confirmed offset,
        else the loop re-fetches forever."""
        batch = [_msg(100, "42", "a"), _msg(98, "42", "b"),  # backwards id
                 {"update_id": 101}]                          # unparseable
        last_id = 0
        for raw in batch:
            tu = parse_update(raw)
            uid = tu.update_id if tu else int(raw.get("update_id", last_id))
            last_id = max(last_id, uid)
        assert last_id == 101  # highest seen wins; next offset = 102
