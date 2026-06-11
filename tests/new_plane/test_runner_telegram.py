"""Telegram client tests — message splitting, update parsing, idempotent webhook delete.

The HTTP layer is mocked via monkeypatch on urllib so tests stay
hermetic. Real network calls are forbidden by the test harness.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from new_plane.miya_runner import telegram as tg
from new_plane.miya_runner.telegram import (
    TelegramClient, parse_update, _split_for_telegram,
)


# ─── parse_update ──────────────────────────────────────────────────────

def test_parse_update_returns_text_message():
    up = {
        "update_id": 42,
        "message": {
            "text": "hello miya",
            "chat": {"id": 12345},
        },
    }
    u = parse_update(up)
    assert u is not None
    assert u.update_id == 42
    assert u.chat_id == "12345"
    assert u.text == "hello miya"


def test_parse_update_returns_edited_message():
    up = {
        "update_id": 43,
        "edited_message": {
            "text": "edited content",
            "chat": {"id": 999},
        },
    }
    u = parse_update(up)
    assert u is not None
    assert u.text == "edited content"
    assert u.chat_id == "999"


def test_parse_update_returns_none_for_channel_post():
    up = {"update_id": 44, "channel_post": {"text": "x", "chat": {"id": 1}}}
    assert parse_update(up) is None


def test_parse_update_returns_none_for_non_text():
    up = {"update_id": 45, "message": {"photo": [], "chat": {"id": 1}}}
    assert parse_update(up) is None


def test_parse_update_returns_none_for_missing_chat_id():
    up = {"update_id": 46, "message": {"text": "x", "chat": {}}}
    assert parse_update(up) is None


# ─── message splitting ────────────────────────────────────────────────

def test_short_message_yields_single_chunk():
    chunks = list(_split_for_telegram("hello"))
    assert chunks == ["hello"]


def test_message_at_limit_yields_single_chunk():
    msg = "x" * 4000
    chunks = list(_split_for_telegram(msg))
    assert len(chunks) == 1
    assert chunks[0] == msg


def test_long_message_splits_at_paragraph_boundary():
    p1 = "alpha " * 500  # ~3000 chars
    p2 = "beta " * 500
    msg = p1 + "\n\n" + p2
    chunks = list(_split_for_telegram(msg))
    assert len(chunks) >= 2
    assert all(len(c) <= 4000 for c in chunks)
    # First chunk contains p1, second contains p2
    assert "alpha" in chunks[0]
    assert "beta" in chunks[1]


def test_very_long_paragraph_hard_splits():
    p = "x" * 10000
    chunks = list(_split_for_telegram(p))
    assert all(len(c) <= 4000 for c in chunks)
    assert sum(len(c) for c in chunks) == 10000


# ─── TelegramClient ────────────────────────────────────────────────────

def test_client_requires_token():
    with pytest.raises(ValueError):
        TelegramClient(token="")


def test_client_normalizes_expected_chat_id():
    c = TelegramClient(token="abc", expected_chat_id=12345)
    assert c.expected_chat_id == "12345"
    c2 = TelegramClient(token="abc")
    assert c2.expected_chat_id is None


def test_get_updates_returns_empty_on_http_error(monkeypatch):
    # Force urlopen to raise
    def boom(*a, **kw):
        raise ConnectionError("boom")
    monkeypatch.setattr(tg._urlreq, "urlopen", boom)
    c = TelegramClient(token="abc")
    assert c.get_updates() == []


def test_get_updates_returns_result_on_ok(monkeypatch):
    payload = {"ok": True, "result": [{"update_id": 7, "message": {"text": "hi", "chat": {"id": 1}}}]}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()

    def fake_urlopen(*a, **kw):
        return FakeResp()
    monkeypatch.setattr(tg._urlreq, "urlopen", fake_urlopen)
    c = TelegramClient(token="abc")
    out = c.get_updates(offset=5)
    assert len(out) == 1
    assert out[0]["update_id"] == 7


def test_delete_webhook_does_not_raise_on_failure(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("boom")
    monkeypatch.setattr(tg._urlreq, "urlopen", boom)
    c = TelegramClient(token="abc")
    c.delete_webhook()  # no exception


def test_send_message_returns_response(monkeypatch):
    sent: list[dict] = []

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true, "result": {"message_id": 99}}'

    def fake_urlopen(req, *a, **kw):
        # Capture the body we sent
        try:
            sent.append(json.loads(req.data.decode()))
        except Exception:
            sent.append({})
        return FakeResp()

    monkeypatch.setattr(tg._urlreq, "urlopen", fake_urlopen)
    c = TelegramClient(token="abc")
    resp = c.send_message("123", "hello")
    assert resp.get("ok") is True
    assert sent[0]["chat_id"] == "123"
    assert sent[0]["text"] == "hello"
    assert sent[0]["parse_mode"] == "Markdown"


def test_send_message_falls_back_to_plain_on_markdown_error(monkeypatch):
    """If Markdown parsing fails Telegram-side, retry as plain text."""
    sent: list[dict] = []
    call_count = [0]

    class FakeRespOk:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    class FakeRespBad:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": false, "description": "Markdown parse error"}'

    def fake_urlopen(req, *a, **kw):
        try:
            sent.append(json.loads(req.data.decode()))
        except Exception:
            sent.append({})
        call_count[0] += 1
        # First call returns bad (markdown error), second returns ok
        return FakeRespBad() if call_count[0] == 1 else FakeRespOk()

    monkeypatch.setattr(tg._urlreq, "urlopen", fake_urlopen)
    c = TelegramClient(token="abc")
    resp = c.send_message("123", "weird *Markdown")
    assert call_count[0] == 2  # retry happened
    assert resp.get("ok") is True
    assert "parse_mode" not in sent[1]  # retry without parse_mode
