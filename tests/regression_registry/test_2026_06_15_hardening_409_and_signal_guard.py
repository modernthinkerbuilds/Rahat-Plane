"""Regression: hardening quick-wins (2026-06-15, Lane 2).

1. Telegram HTTP 409 (two pollers on one bot token) now raises
   TelegramConflictError instead of being swallowed — so the serve loop
   can exit and let launchd own the singleton rather than spin. (The
   2026-06-15 cutover hit a multi-minute 409 loop from a terminal + launchd
   instance both polling.)
2. Non-409 HTTP errors are still swallowed (return {} → []), unchanged.
3. The signal store gets a RAHAT_TEST_MODE sandbox guard (mirrors core.io):
   with test mode on and no explicit OPENCLAW_SIGNALS_DB, it never resolves
   to the real ~/.rahat signals DB. Explicit override still wins.
"""
from __future__ import annotations

from urllib import error as _urlerr

import pytest

from new_plane.miya_runner.telegram import TelegramClient, TelegramConflictError


def _patch_urlopen(monkeypatch, exc):
    def _raise(url, timeout=0):
        raise exc
    monkeypatch.setattr(
        "new_plane.miya_runner.telegram._urlreq.urlopen", _raise)


def test_get_updates_409_raises_conflict(monkeypatch):
    _patch_urlopen(monkeypatch, _urlerr.HTTPError(
        "http://x/getUpdates", 409, "Conflict", {}, None))
    with pytest.raises(TelegramConflictError):
        TelegramClient("tok").get_updates()


def test_get_updates_non_409_http_error_swallowed(monkeypatch):
    _patch_urlopen(monkeypatch, _urlerr.HTTPError(
        "http://x/getUpdates", 500, "Server Error", {}, None))
    assert TelegramClient("tok").get_updates() == []


def test_get_updates_network_error_swallowed(monkeypatch):
    _patch_urlopen(monkeypatch, _urlerr.URLError("no route"))
    assert TelegramClient("tok").get_updates() == []


def test_signal_store_test_mode_sandbox(monkeypatch):
    monkeypatch.delenv("OPENCLAW_SIGNALS_DB", raising=False)
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    p = store._default_path()
    assert "rahat_signals_test_" in p.name
    assert ".rahat" not in str(p), "must not resolve to the real home signals DB"


def test_explicit_signals_db_still_wins(monkeypatch, tmp_path):
    target = tmp_path / "explicit.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(target))
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    assert store._default_path() == target
