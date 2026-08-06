"""Regression (2026-08-01) — env-supplied paths must survive a literal $HOME.

THE BUG. `.env` shipped ``OPENCLAW_SIGNALS_DB=$HOME/.rahat/…`` and
``OPENCLAW_COST_LOG=$HOME/.rahat/…``. python-dotenv does NOT expand
``$VARS`` (and launchd plist env blocks don't either), so both landed as
literal relative paths. The runner's WorkingDirectory is the repo root,
so five weeks of unattended operation quietly created a literal
``$HOME/.rahat/`` directory INSIDE the repo, split-braining the signal
store and the bandit cost log across two locations. Gitignore line 73
was the only thing between that directory and the public repo.

THE CONTRACT. Any env-supplied path in the runner is passed through
``os.path.expandvars`` + ``expanduser``; if a ``$`` survives expansion
(undefined variable), the code falls back to its safe default rather
than creating a ``$``-named directory.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ─────────────────────────── signals store ───────────────────────────
def test_signals_db_literal_home_is_expanded(monkeypatch):
    from new_plane.signals import store
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", "$HOME/.rahat/sig.db")
    p = store._default_path()
    assert "$" not in str(p), f"unexpanded $ survived: {p}"
    assert str(p) == "/tmp/fakehome/.rahat/sig.db"


def test_signals_db_tilde_is_expanded(monkeypatch):
    from new_plane.signals import store
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", "~/.rahat/sig.db")
    p = store._default_path()
    assert str(p) == "/tmp/fakehome/.rahat/sig.db"


def test_signals_db_undefined_var_falls_back(monkeypatch):
    """An unexpandable $VAR must NOT produce a '$…' path — fall back to
    the safe default (test-mode tmp path here, since RAHAT_TEST_MODE=1)."""
    from new_plane.signals import store
    monkeypatch.delenv("RAHAT_NO_SUCH_VAR", raising=False)
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", "$RAHAT_NO_SUCH_VAR/sig.db")
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    p = store._default_path()
    assert "$" not in str(p), f"'$'-named path escaped the guard: {p}"


def test_signals_db_clean_path_passthrough(monkeypatch, tmp_path):
    from new_plane.signals import store
    target = tmp_path / "sig.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(target))
    assert store._default_path() == target


# ─────────────────────────── cost router ───────────────────────────
def test_cost_log_literal_home_is_expanded(monkeypatch):
    from new_plane.miya_runner import cost_router as cr
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.setenv("OPENCLAW_COST_LOG", "$HOME/.rahat/cost.log")
    assert cr._resolve_cost_log_path() == "/tmp/fakehome/.rahat/cost.log"


def test_cost_log_undefined_var_falls_back(monkeypatch):
    from new_plane.miya_runner import cost_router as cr
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.delenv("RAHAT_NO_SUCH_VAR", raising=False)
    monkeypatch.setenv("OPENCLAW_COST_LOG", "$RAHAT_NO_SUCH_VAR/cost.log")
    resolved = cr._resolve_cost_log_path()
    assert "$" not in resolved
    assert resolved.endswith("/.rahat/cost_router.log")


def test_cost_log_empty_string_still_disables(monkeypatch):
    """Empty string is the documented opt-out — must stay an opt-out,
    not fall back to the default."""
    from new_plane.miya_runner import cost_router as cr
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    assert cr._resolve_cost_log_path() == ""


def test_no_literal_dollar_dir_would_be_created(monkeypatch, tmp_path):
    """End-to-end shape of the incident: literal $HOME env + relative
    resolution must never yield a path whose first component starts
    with '$' (that's what materialized `$HOME/` inside the repo)."""
    from new_plane.signals import store
    from new_plane.miya_runner import cost_router as cr
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", "$HOME/.rahat/sig.db")
    monkeypatch.setenv("OPENCLAW_COST_LOG", "$HOME/.rahat/cost.log")
    for p in (store._default_path(), Path(cr._resolve_cost_log_path())):
        first = Path(p).parts[0] if Path(p).parts else ""
        assert not str(first).startswith("$"), (
            f"path {p} would create a literal '$…' directory"
        )
        assert Path(p).is_absolute(), f"path {p} is relative — repo-cwd trap"
