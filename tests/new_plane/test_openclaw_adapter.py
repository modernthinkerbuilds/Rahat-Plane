"""Smoke tests for bridges/openclaw_adapters/server.py.

Hermetic — the adapter calls real agent functions, but the test wraps them
with monkeypatch where needed. Pre-cleared `OPENCLAW_ADAPTER_TOKEN` so auth
is disabled in dev mode; a separate test exercises the auth path.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OPENCLAW_ADAPTER_TOKEN", raising=False)
    from bridges.openclaw_adapters.server import app
    return TestClient(app)


def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ts" in body


def test_version_returns_version_and_sha(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json()["version"]


def test_kobe_today_target_calls_tool(client):
    with patch("agents.the_scientist.tools.get_today_target",
               return_value={"day_type": "cf", "target_kcal": 1300}) as m:
        r = client.post("/kobe/today_target", json={"trace_id": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "t1"
    assert body["result"] == {"day_type": "cf", "target_kcal": 1300}
    m.assert_called_once()


def test_kobe_project_eta_passes_args(client):
    with patch("agents.the_scientist.tools.project_goal_eta",
               return_value={"eta_date_iso": "2026-09-01"}) as m:
        r = client.post("/kobe/project_eta", json={
            "target_lbs": 196,
            "daily_intake_kcal": 2250,
            "weekly_active_kcal": 6000,
        })
    assert r.status_code == 200
    m.assert_called_once_with(
        target_lbs=196,
        target_kg=None,
        daily_intake_kcal=2250,
        weekly_active_kcal=6000,
    )


def test_kobe_charter_check_returns_allowed(client):
    with patch("agents.the_scientist.tools._charter_check",
               return_value=(True, None)):
        r = client.post("/kobe/charter_check", json={"kind": "notify.user.reply"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["allowed"] is True
    assert body["result"]["reason"] is None


def test_kobe_charter_check_returns_veto(client):
    with patch("agents.the_scientist.tools._charter_check",
               return_value=(False, "quiet hours")):
        r = client.post("/kobe/charter_check", json={
            "kind": "notify.user.nudge",
            "now_iso": "2026-05-30T23:00:00+00:00",
        })
    body = r.json()
    assert body["result"]["allowed"] is False
    assert "quiet" in body["result"]["reason"].lower()


def test_fraser_design_session_returns_text(client):
    with patch("agents.fraser.composer.design_session",
               return_value="## Part 1: Warm-up..."):
        r = client.post("/fraser/design_session", json={
            "message": "60 min session no running",
            "chat_id": "C1",
        })
    assert r.status_code == 200
    assert "Part 1" in r.json()["result"]["text"]


def test_fraser_design_session_recovers_from_error(client):
    """Adapter must never crash — agent errors come back as structured."""
    with patch("agents.fraser.composer.design_session",
               side_effect=RuntimeError("LLM blew up")):
        r = client.post("/fraser/design_session", json={"message": "x"})
    assert r.status_code == 200
    assert "error" in r.json()
    assert "LLM blew up" in r.json()["error"]


# ─── auth-mode separate fixture ────────────────────────────────────────────
@pytest.fixture
def authed_client(monkeypatch):
    monkeypatch.setenv("OPENCLAW_ADAPTER_TOKEN", "secret-abc-123")
    from bridges.openclaw_adapters.server import app
    return TestClient(app)


def test_auth_rejects_missing_token(authed_client):
    r = authed_client.post("/kobe/today_target", json={})
    assert r.status_code == 401


def test_auth_rejects_wrong_token(authed_client):
    r = authed_client.post(
        "/kobe/today_target",
        json={},
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_auth_accepts_correct_token(authed_client):
    with patch("agents.the_scientist.tools.get_today_target",
               return_value={"ok": True}):
        r = authed_client.post(
            "/kobe/today_target",
            json={},
            headers={"Authorization": "Bearer secret-abc-123"},
        )
    assert r.status_code == 200
    assert r.json()["result"] == {"ok": True}
