"""new_plane.env.setting — the runtime's settings under the RAHAT_ prefix,
and an older `.env` keeps working until it is renamed (2026-11-06).

A running Mac reads its signal store path, its cost log and its adapter
token from `.env`. Renaming the keys in code must not move the live store
to a new, empty file on the next restart: an older key that differs only
in its prefix is used, with one warning naming the key to rename. Tests
never see that fallback (RAHAT_TEST_MODE), so a developer's `.env` cannot
point a test at a live file. "OLDPFX" stands in for any older prefix.
"""
from __future__ import annotations

import logging
import os

import pytest

from new_plane import env


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # Whatever this process inherited (a developer's .env loaded by an
    # import, an exported shell variable), these tests start with none of
    # the runtime's settings under any prefix.
    tails = ("_SIGNALS_DB", "_COST_LOG", "_ADAPTER_URL", "_ADAPTER_TOKEN", "_ADAPTER_PORT")
    for k in list(os.environ):
        if k.endswith(tails):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("RAHAT_TEST_MODE", raising=False)
    env._warned.clear()


def test_the_new_name_wins_and_an_empty_value_is_kept(monkeypatch):
    monkeypatch.setenv("RAHAT_SIGNALS_DB", "/data/signals.db")
    monkeypatch.setenv("OLDPFX_SIGNALS_DB", "/old/signals.db")
    assert env.setting("RAHAT_SIGNALS_DB") == "/data/signals.db"
    monkeypatch.setenv("RAHAT_COST_LOG", "")            # "" switches the cost log off
    assert env.setting("RAHAT_COST_LOG") == ""


def test_an_older_key_is_used_once_renamed_in_the_log(monkeypatch, caplog):
    monkeypatch.setenv("OLDPFX_SIGNALS_DB", "/old/signals.db")
    with caplog.at_level(logging.WARNING, logger="rahat.env"):
        assert env.setting("RAHAT_SIGNALS_DB") == "/old/signals.db"
        assert env.setting("RAHAT_SIGNALS_DB") == "/old/signals.db"
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["RAHAT_SIGNALS_DB is read from OLDPFX_SIGNALS_DB; rename that key to "
                     "RAHAT_SIGNALS_DB in .env"]                       # once, not per call


def test_two_older_candidates_are_not_guessed(monkeypatch, caplog):
    monkeypatch.setenv("OLDPFX_SIGNALS_DB", "/a.db")
    monkeypatch.setenv("OTHER_SIGNALS_DB", "/b.db")
    with caplog.at_level(logging.WARNING, logger="rahat.env"):
        assert env.setting("RAHAT_SIGNALS_DB") is None
    assert "2 older keys end in _SIGNALS_DB" in caplog.text


def test_tests_never_reach_an_older_key(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("OLDPFX_SIGNALS_DB", "/live/signals.db")
    assert env.setting("RAHAT_SIGNALS_DB") is None
    assert env.setting("NOT_RAHAT_NAME") is None


def test_the_signal_store_and_the_adapter_token_read_through_it(monkeypatch, tmp_path):
    """Outside test mode an older `.env` keeps the live signal store where it
    was and keeps the adapter's auth on."""
    from bridges.http_adapter import auth
    from new_plane.signals import store
    monkeypatch.setenv("OLDPFX_SIGNALS_DB", str(tmp_path / "live_signals.db"))
    assert store._default_path() == tmp_path / "live_signals.db"
    monkeypatch.setenv("OLDPFX_ADAPTER_TOKEN", "s3cret")
    assert auth.get_expected_token() == "s3cret"
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    assert store._default_path() != tmp_path / "live_signals.db"
