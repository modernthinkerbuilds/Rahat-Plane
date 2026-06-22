"""Pin: 2026-06-14 — Telegram 409 single-poller exit (PRE_SCALE D-P1).

A bot token is a single-poller resource. When two pollers run (a restart
overlap or a stray terminal instance during the cutover), Telegram returns
HTTP 409 Conflict. The runner must NOT spin on generic backoff forever — it
must surface the 409 as a terminal signal and exit so launchd owns the
singleton (the 2026-06-15 cutover 409 loop is the reason).

This pins both halves of the implemented fix:
  * `telegram.TelegramClient._http` raises `TelegramConflictError` on 409.
  * the runner's poll loop tolerates a brief overlap then EXITS after
    repeated 409s (it does not spin).
All hermetic — no network.
"""
from __future__ import annotations

import urllib.error

import pytest

from new_plane.miya_runner import telegram as tg
from new_plane.miya_runner.telegram import TelegramClient, TelegramConflictError


def _client():
    return TelegramClient("test-token", expected_chat_id="42")


def test_http_409_raises_conflict_error(monkeypatch):
    """A 409 from urllib must become a TelegramConflictError (the terminal
    signal), not a generic error the loop would back off on."""
    def fake_urlopen(*a, **k):
        raise urllib.error.HTTPError(
            url="https://api.telegram.org", code=409,
            msg="Conflict", hdrs=None, fp=None)

    monkeypatch.setattr(tg._urlreq, "urlopen", fake_urlopen)
    c = _client()
    with pytest.raises(TelegramConflictError):
        c.get_updates(offset=1)


def test_non_409_http_error_does_not_raise_conflict(monkeypatch):
    """A 500 (transient server error) must NOT be a conflict — the loop
    should back off and retry, not exit."""
    def fake_urlopen(*a, **k):
        raise urllib.error.HTTPError(
            url="https://api.telegram.org", code=500,
            msg="Server Error", hdrs=None, fp=None)

    monkeypatch.setattr(tg._urlreq, "urlopen", fake_urlopen)
    c = _client()
    # get_updates swallows non-409 transport errors → [], never a conflict.
    assert c.get_updates(offset=1) == []


def test_conflict_error_carries_method_for_logging():
    """The terminal error names the offending API method so the operator
    can see WHICH call 409'd in the logs."""
    err = TelegramConflictError("getUpdates")
    assert "getUpdates" in str(err)


def test_repeated_conflicts_exit_threshold_is_three():
    """The poll loop exits after 3 consecutive 409s (it does not spin
    forever). We pin the documented threshold by reading the loop source so
    a refactor that removes the exit fires this test.
    (Driving the real blocking loop is covered by the runner integration
    tests; here we assert the exit policy is present and bounded.)"""
    import inspect
    from new_plane.miya_runner import __main__ as runner_main
    src = inspect.getsource(runner_main)
    assert "TelegramConflictError" in src, "loop no longer handles 409"
    assert "conflict_errors >= 3" in src, (
        "the 409 exit threshold changed/disappeared — the runner may now "
        "spin on repeated conflicts instead of letting launchd own the "
        "singleton"
    )
    assert "break" in src
