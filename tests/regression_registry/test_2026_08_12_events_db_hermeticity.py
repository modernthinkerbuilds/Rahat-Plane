"""Bug pin (2026-08-12) — the events DB must be hermetic under test mode.

CAUGHT BY THE PRE-PUSH GATE on the owner's Mac (the gate earning its
keep): test_whats_on_without_location_explains failed because
handle_whats_on — which since 9bf5ae0 reads the events inventory —
resolved bridges.events.store.db_path() all the way to the LIVE
vault/rahat.db. The test predates the inventory and sets no
RAHAT_EVENTS_DB, so the morning refresh's real Santa Cruz rows leaked
into a "no location, no events" scenario and changed the handler's
branch. On the cloud replica the suite was green purely because no
live DB exists there — a hermeticity hole is invisible until the live
side has data.

Reads are the symptom; WRITES are the disease: the same fallthrough
would have let a test's upsert_events() land in the production
inventory — the 2026-05-08 corruption incident class that
RAHAT_TEST_MODE exists to prevent.

THE PIN. Under RAHAT_TEST_MODE=1, db_path() must resolve inside the
sandbox in every configuration:
  * explicit RAHAT_EVENTS_DB always wins (tests that isolate per-tmp);
  * else RAHAT_TEST_VAULT_DIR (the standard test sandbox);
  * else a tempdir fallback — NEVER the vault, NEVER RAHAT_VITALS_DB
    (which in a dev shell may point at the live file).
And the original symptom: with events in the "live" DB and no
overrides, the no-location /whatson still explains RAHAT_GENIE_LOCATION
instead of leaking live inventory into the reply.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.delenv("RAHAT_EVENTS_DB", raising=False)
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    return tmp_path


def test_test_mode_resolves_inside_the_sandbox(env, tmp_path, monkeypatch):
    from bridges.events import store
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    p = store.db_path()
    assert str(tmp_path) in p
    assert "vault/rahat.db" not in p.replace(os.sep, "/")


def test_test_mode_never_falls_through_to_vitals_or_vault(
        env, monkeypatch, tmp_path):
    from bridges.events import store
    # Worst case: no sandbox dir set AND a vitals env pointing at what
    # would be the live DB in a dev shell. Test mode must ignore it.
    monkeypatch.delenv("RAHAT_TEST_VAULT_DIR", raising=False)
    monkeypatch.setenv("RAHAT_VITALS_DB",
                       str(tmp_path / "pretend_live_rahat.db"))
    p = store.db_path()
    assert "pretend_live_rahat.db" not in p
    assert "rahat_test" in p or "events_test.db" in p


def test_explicit_events_db_still_wins(env, monkeypatch, tmp_path):
    from bridges.events import store
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "mine.db"))
    assert store.db_path() == str(tmp_path / "mine.db")


def test_live_rows_cannot_leak_into_a_no_location_whatson(
        env, tmp_path, monkeypatch):
    """The original gate failure, end to end: real-looking rows in a
    'live' DB + an old-style test env (no RAHAT_EVENTS_DB) must NOT
    surface in /whatson — the sandbox is empty, so the no-location
    explanation renders."""
    from bridges.events import store
    live = tmp_path / "live" / "rahat.db"
    live.parent.mkdir(parents=True)
    from datetime import datetime
    # Write the decoy THROUGH the store, but explicitly to the fake
    # live path (a refresh job would do this with test mode off).
    store.upsert_events(
        [{"title": "Hazardous Waste Station Open",
          "start_ts": "2026-08-15 00:00:00", "city": "Santa Cruz"}],
        "santa-cruz", now=datetime(2026, 8, 12, 7), path=str(live))
    monkeypatch.setenv("RAHAT_VITALS_DB", str(live))   # dev-shell shape
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    out = handler.handle_whats_on(llm=lambda p: "{}")
    assert "Hazardous Waste" not in out, "live inventory leaked into a test"
    assert "RAHAT_GENIE_LOCATION" in out
