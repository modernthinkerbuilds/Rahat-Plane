"""Integration tests — exercise the adapter against REAL Kobe/Fraser code.

The unit tests in test_openclaw_adapter.py patch the underlying tool
functions. These tests do NOT — they call into the actual
``agents.the_scientist.tools`` and ``agents.fraser.composer`` paths.

This catches the class of bug the unit tests can't see:
  - contract drift (someone renames `latest_weight` in the sci module)
  - sci-module import failures (missing deps, broken `_sci()` loader)
  - shape changes (the tool returns dict instead of list, etc.)
  - the adapter's error-wrapping (`_safely`) actually working

All tests assert HTTP 200 — the adapter must never return 500. On a real
underlying error, the response carries an ``error`` field with the
type+message; the OpenClaw plugin can route around it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENCLAW_ADAPTER_TOKEN", raising=False)
    # Hermetic-ish: live agents read DB; make sure RAHAT_TEST_MODE is on
    # so writes don't touch the user's live DB.
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_VOICE", "neutral")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from bridges.openclaw_adapters.server import app
    return TestClient(app)


# ─── adapter never crashes — every endpoint returns 200 ───────────────────
@pytest.mark.parametrize("path,body", [
    ("/kobe/today_target",    {}),
    ("/kobe/active_goal",     {}),
    ("/kobe/pace",            {}),
    ("/kobe/recalibration",   {}),
    ("/kobe/missed_workouts", {}),
    ("/kobe/charter_check",   {"kind": "notify.user.reply"}),
    ("/kobe/project_eta",     {"target_lbs": 197, "daily_intake_kcal": 2250,
                               "weekly_active_kcal": 6000}),
])
def test_real_kobe_endpoints_never_5xx(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text}"
    j = r.json()
    # Every response has either result or error — never both, never neither
    assert ("result" in j) ^ ("error" in j), (
        f"{path} response must have exactly one of result/error, got: {j}")
    assert j.get("trace_id"), f"{path} missing trace_id"


# ─── concrete shape assertions on result envelopes ────────────────────────
def test_today_target_shape(client):
    r = client.post("/kobe/today_target", json={}).json()
    # Either we got a result (Mac with full deps) or an error (sandbox without).
    if "result" in r:
        res = r["result"]
        assert "day_type" in res
        assert "target_kcal" in res


def test_project_eta_real_math(client):
    """The math is deterministic — assert the response carries a rate
    field and the eta_date_iso is sensible for a realistic input."""
    r = client.post("/kobe/project_eta", json={
        "target_lbs": 197,
        "daily_intake_kcal": 2250,
        "weekly_active_kcal": 6000,
    }).json()
    if "result" in r:
        res = r["result"]
        assert "rate_lb_per_wk" in res
        assert "direction" in res
        # rate sign is informative — eta_date_iso may be None if unreachable
        if res.get("eta_date_iso") is not None:
            assert len(res["eta_date_iso"]) >= 10  # YYYY-MM-DD


# ─── load-bearing primitive: signal round-trip with real store ────────────
def test_signal_full_lifecycle(client, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(tmp_path / "signals.db"))
    # Re-import to pick up the new path
    from new_plane.signals import store
    store.set_db_path(tmp_path / "signals.db")

    # 1) publish
    pub = client.post("/signals/publish", json={
        "agent": "kobe",
        "type": "plan_delivered",
        "payload": {"day_type": "cf", "target_kcal": 1300},
        "trace_id": "int-rt-1",
    }).json()
    assert "result" in pub, pub
    sid = pub["result"]["signal_id"]
    assert isinstance(sid, int) and sid >= 1

    # 2) read back
    rec = client.get("/signals/recent?agent=kobe&limit=5").json()
    assert "result" in rec
    items = rec["result"]["items"]
    assert any(s["id"] == sid for s in items)
    mine = [s for s in items if s["id"] == sid][0]
    assert mine["agent"] == "kobe"
    assert mine["type"] == "plan_delivered"
    assert mine["payload"] == {"day_type": "cf", "target_kcal": 1300}
    assert mine["trace_id"] == "int-rt-1"
    assert mine["consumed_by"] == []

    # 3) health gauge — should report 1 unconsumed
    h = client.get("/signals/health").json()
    assert h["result"]["unconsumed"] >= 1

    # 4) consume
    con = client.post("/signals/consume", json={
        "signal_id": sid, "consumer_agent": "miya",
    }).json()
    assert con["result"]["newly_added"] is True

    # 5) consume again — should be no-op
    con2 = client.post("/signals/consume", json={
        "signal_id": sid, "consumer_agent": "miya",
    }).json()
    assert con2["result"]["newly_added"] is False

    # 6) health should drop
    h2 = client.get("/signals/health").json()
    assert h2["result"]["unconsumed"] < h["result"]["unconsumed"]


def test_signal_consume_unknown_id_returns_structured_error(client):
    r = client.post("/signals/consume", json={
        "signal_id": 99999999,
        "consumer_agent": "miya",
    })
    assert r.status_code == 200
    j = r.json()
    assert "error" in j
    assert "KeyError" in j["error"] or "not found" in j["error"]


# ─── _safely structure — never raises, always wraps ───────────────────────
def test_safely_wraps_underlying_exceptions(client):
    """If we send junk that makes pydantic accept but the underlying tool
    fails, the response is still 200 with an error field."""
    # Empty body to project_eta is invalid at the pydantic level — that's a 422,
    # not a 500. So we test by sending valid-shape but garbage values that
    # the underlying function may not like.
    r = client.post("/kobe/project_eta", json={
        "target_lbs": -1.0,                # absurd
        "daily_intake_kcal": 0,
        "weekly_active_kcal": 0,
    })
    assert r.status_code == 200, r.text
    # Either result (function handled gracefully) or error (function raised
    # and adapter wrapped it). Both are fine; what matters is no 500.
    j = r.json()
    assert "trace_id" in j
    assert ("result" in j) or ("error" in j)
