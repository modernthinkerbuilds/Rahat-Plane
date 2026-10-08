"""Regression (2026-10-07, Benji T3): the inbox poll never crashes.

LIVE (vault/benji_runner.log, 2026-09-28): a DNS outage on the Mac made
every 15-minute poll die with a socket.gaierror traceback — 46 of them —
and nothing retried or said so. The mailbox is now reached with three
tries, 10 s then 30 s apart; a third failure is one WARNING naming the
exception class, the poll returns normally and the runner exits 0.
"""
from __future__ import annotations

import logging
import socket

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", "owner@example.com")
    monkeypatch.delenv("RAHAT_JOBSEARCH_DB", raising=False)
    import agents.benji  # noqa: F401
    import importlib
    from bridges.jobsearch import store
    importlib.reload(store)


def _dns_down():
    raise socket.gaierror(8, "nodename nor servname provided, or not known")


def test_a_mailbox_that_never_answers_is_one_warning_not_a_traceback(env, caplog):
    from new_plane.benji_runner import inbox
    waits: list[int] = []
    with caplog.at_level(logging.INFO):
        out = inbox.poll_inbox(fetch=_dns_down, sleep=waits.append)
    assert out == {"handled": 0, "ignored": 0, "total": 0, "error": "gaierror"}
    assert waits == [10, 30]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "gaierror" in warnings[0].getMessage()
    assert all(r.exc_info is None for r in caplog.records)      # no traceback anywhere


def test_a_mailbox_that_comes_back_is_read_on_the_retry(env):
    from new_plane.benji_runner import inbox
    tries = iter([_dns_down, _dns_down, lambda: []])
    waits: list[int] = []
    out = inbox.poll_inbox(fetch=lambda: next(tries)(), sleep=waits.append)
    assert out == {"handled": 0, "ignored": 0, "total": 0} and waits == [10, 30]


def test_the_runner_exits_zero_when_the_mailbox_is_down(env, monkeypatch, caplog):
    from new_plane.benji_runner import inbox, main
    monkeypatch.setattr(inbox, "_imap_fetch_recent", _dns_down)
    monkeypatch.setattr(inbox.time, "sleep", lambda s: None)
    with caplog.at_level(logging.INFO):
        assert main.main(["--inbox"]) == 0
    assert sum(1 for r in caplog.records if r.levelno >= logging.WARNING) == 1
    assert "Traceback" not in caplog.text
