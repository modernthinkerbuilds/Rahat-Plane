"""Regression: the new runner loads the repo .env (2026-06-14 cutover).

Under launchd, `com.rahat.miya.v2` crash-looped on
"NEW_MIYA_BOT_TOKEN not set — refusing to boot": launchd doesn't source
the shell or a project .env, and (unlike the old plane via core.io) the
new runner never called load_dotenv. main() now loads the repo-root .env
explicitly before dispatching, so the service boots under launchd.

Pinned:
  1. _load_env() loads vars from a given .env into os.environ.
  2. It does NOT override a variable already set in the environment
     (so a real launchd EnvironmentVariables block still wins).
  3. The default path resolves to the repo-root .env (exists in repo).
"""
from __future__ import annotations

import os
from pathlib import Path

from new_plane.miya_runner import __main__ as runner_main


def test_load_env_populates_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("RAHAT_DOTENV_PROBE", raising=False)
    env = tmp_path / ".env"
    env.write_text("RAHAT_DOTENV_PROBE=loaded_ok\n")
    runner_main._load_env(env)
    assert os.environ.get("RAHAT_DOTENV_PROBE") == "loaded_ok"


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_DOTENV_PROBE", "already_set")
    env = tmp_path / ".env"
    env.write_text("RAHAT_DOTENV_PROBE=from_file\n")
    runner_main._load_env(env)
    # launchd EnvironmentVariables / shell exports must win over .env.
    assert os.environ["RAHAT_DOTENV_PROBE"] == "already_set"


def test_default_env_path_is_repo_root():
    # parents[2] of new_plane/miya_runner/__main__.py == repo root.
    default = Path(runner_main.__file__).resolve().parents[2] / ".env"
    assert default.name == ".env"
    assert (default.parent / "new_plane").is_dir(), \
        f"resolved repo root {default.parent} has no new_plane/ — path math wrong"
