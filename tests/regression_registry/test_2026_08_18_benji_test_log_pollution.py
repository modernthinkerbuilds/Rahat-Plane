"""Regression (2026-08-18, launch night) — tests must never write the
live runner log.

THE INCIDENT. The pre-push gate at 20:06 filled vault/benji_runner.log
with fixture lines — 'stranger@evil.com', fake digests, 'digests
paused' — because main.py attached its FileHandler (and loaded .env) at
IMPORT time, and the S3/HTML pins import the module. The owner then
read fixture noise as production behavior during a live debugging
session. Hermeticity isn't only DBs and sockets: the log file is state.

THE PIN. Importing the runner module and running a command under
RAHAT_TEST_MODE attaches no handler for the benji log file; runtime
configuration happens only inside main(), and never under test mode.
"""
from __future__ import annotations

import importlib
import logging

import pytest


def test_import_and_cmd_attach_no_live_log_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    import new_plane.benji_runner.main as main
    importlib.reload(main)
    main._configure_runtime()          # must be a no-op under test mode
    for lg in (logging.getLogger(), logging.getLogger("benji_runner")):
        for h in lg.handlers:
            base = getattr(h, "baseFilename", "") or ""
            assert "benji_runner.log" not in base, (
                "test run attached the LIVE log handler")


def test_configure_is_not_module_level(monkeypatch):
    import inspect
    import new_plane.benji_runner.main as main
    src = inspect.getsource(main)
    head = src.split("def _configure_runtime")[0]
    assert "configure(" not in head.replace(
        "from new_plane.log_setup import configure", "")
