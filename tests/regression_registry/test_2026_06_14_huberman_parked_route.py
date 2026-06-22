"""Regression: Huberman is a PARKED but functional route (2026-06-14).

Decision (autonomous cutover-prep window, owner directive "park huberman,
there is no hrv data yet"): Huberman is NOT built first-class. The
agents/huberman/ package is a stub. The @huberman route stays live and
auditable — it delegates to Kobe's mesh and logs path="huberman_route" —
but no Huberman agent is registered.

This test pins three contracts so the parked state can't silently rot:

  1. classify_delegation routes "@huberman <body>" to huberman_route
     (and "@huberman" with no body to orchestrate, not an empty call).
  2. native_client.huberman_route delegates to Kobe with an explicit
     "@huberman " prefix and tags the path "huberman_route".
  3. huberman_bridge degrades safely with NO HRV data: current_state()
     returns all-None (hrv_ms/hrv_band/sleep_hours), which downstream
     means "don't apply the constraint" (2026-05-19 user rule). This is
     the no-HRV-data safety floor that makes parking Huberman safe.
"""
from __future__ import annotations

from new_plane.miya_runner.delegate_classifier import classify_delegation


# ── 1. classifier routes @huberman correctly ─────────────────────────────
def test_at_huberman_with_body_routes_to_huberman_route():
    path, body = classify_delegation("@huberman is my HRV ok for a PR today")
    assert path == "huberman_route"
    assert body == "is my HRV ok for a PR today"


def test_at_huberman_no_body_falls_back_to_orchestrate():
    path, _ = classify_delegation("@huberman")
    assert path == "orchestrate"


# ── 2. native_client.huberman_route delegates to Kobe, tags the path ──────
def test_huberman_route_delegates_to_kobe_with_prefix(monkeypatch):
    import agents.the_scientist.handler as kobe_handler
    seen = {}

    def _fake_route(message, *a, **k):
        seen["message"] = message
        return "kobe answered the huberman query"

    monkeypatch.setattr(kobe_handler, "route", _fake_route)

    from new_plane.miya_runner import native_client
    res = native_client.huberman_route("how's my recovery")

    assert res.ok
    assert res.result["path"] == "huberman_route"
    assert res.result["text"] == "kobe answered the huberman query"
    # Delegated to Kobe with an explicit @huberman marker for mesh routing.
    assert seen["message"] == "@huberman how's my recovery"


# ── 3. no-HRV-data safety floor (owner: "there is no hrv data yet") ───────
def test_huberman_bridge_safe_when_no_hrv_data(tmp_path):
    from core import huberman_bridge

    empty_db = str(tmp_path / "empty.db")  # no rows → nothing to report
    state = huberman_bridge.current_state(db_path=empty_db)

    # All None == "not reported, do NOT assume" → no auto-deload fires.
    assert state.hrv_ms is None
    assert state.hrv_band is None
    assert state.sleep_hours is None
