"""Pin: 2026-06-16 (re-eval pass) — huberman_route is a user-facing path that
BYPASSES the entire voice/validation sink.

SYMPTOM (re-derived from code + empirical run, RAHAT_TEST_MODE=1):
    The architect's 2026-06-13 claim is "single voice sink — every
    kobe_route / fraser_route reply re-voiced through synth." But the THIRD
    delegation branch, `huberman_route` (reached by any `@huberman …`
    message — classify_delegation routes it there), runs ONLY the outbound
    charter gate. It does NOT call:
        _scrub_voice_leak   (prefix voice-leak strip)
        _revoice_through_synth   (the single voice sink)
        _validate_outbound   (wrong-1RM / pace / goal correction)

    Reproduced: an @huberman turn whose underlying Kobe-mesh route returns
        "Kobe thinks you should rest today, as Fraser said your deadlift
         is 999 kg."
    ships VERBATIM to the user — specialist attribution leaked AND a
    fabricated 999 kg shipped unvalidated. The same string through
    kobe_route gets the 999 kg corrected to the profile value.

WHY IT MATTERS: @huberman is live (the bridge is parked but the ROUTE is
not — native_client.huberman_route delegates to Kobe's mesh). So a real
user-facing path has zero voice-leak, zero hallucination, and zero
fact-fidelity defense. P0 for the "single voice sink" claim.

PROPOSED FIX: see PROPOSED_FIXES.md PF-2026-06-16-001 — route every
delegation branch through one `_finalize(text, path, turn)` sink that runs
scrub + revoice + validate + charter uniformly (the §B-P2 `_finalize`
refactor the PRE_SCALE plan already names). The architect must fix the
CLASS (three hand-copied branches that drift), not patch huberman alone.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_runner.orchestrator import Turn, handle


_LEAK = ("Kobe thinks you should rest today, as Fraser said your "
         "deadlift is 999 kg.")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("NEW_MIYA_REVOICE", "1")
    monkeypatch.setenv("NEW_MIYA_VALIDATE", "1")
    from new_plane.signals import store
    store.set_db_path(tmp_path / "sig.db")
    store.init_db()
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(tmp_path / "sig.db"))
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    yield


def test_huberman_is_a_live_user_facing_route():
    """@huberman is a real user path (routes to huberman_route), not dead."""
    path, _ = classify_delegation("@huberman should I train today")
    assert path == "huberman_route"


def test_huberman_route_now_runs_through_finalize_sink():
    """FIXED 2026-06-16 (P0-1, architect): huberman_route goes through the
    same `_finalize_delegated` sink as kobe/fraser, so the validator corrects
    the fabricated 1RM before it ships. (Mid-sentence attribution like "as
    Fraser said" is the known-insufficient scrubber case tracked by P0-2 /
    the voice-leak corpus; the deterministic numeric wall is what closes the
    live ship-a-hallucination risk.)"""
    with patch("agents.the_scientist.handler.route", lambda m: _LEAK):
        resp = handle(Turn(user_message="@huberman should I train today",
                           chat_id="c"))
    assert resp.routing["path"] == "huberman_route"
    # The fabricated 999 kg is corrected by the validator (previously raw).
    assert "999" not in resp.text
    # And the turn is never silently dropped (P1-5 never-empty guard).
    assert resp.text.strip()


def test_huberman_route_should_validate_fabricated_1rm():
    """HARD PIN (flipped from xfail 2026-06-16): a fabricated 999 kg deadlift
    must be corrected/removed before a huberman_route reply ships, exactly as
    kobe_route does — now true via the shared `_finalize_delegated` sink."""
    with patch("agents.the_scientist.handler.route", lambda m: _LEAK):
        resp = handle(Turn(user_message="@huberman should I train today",
                           chat_id="c"))
    assert "999" not in resp.text, (
        "huberman_route shipped a fabricated 1RM with no validator pass"
    )
