"""Bug pin (2026-08-19, live) — the HealthKit bridge forbids connection
reuse, because pooled sockets were eating background syncs.

THE INCIDENT. "The automation doesn't work automatically; manual works
sometimes." The HAE Activity Log told the real story: a background
upload failed with "The network connection was lost", then three
manual exports seconds apart — first failed the same way, the next
ones succeeded. That is the stale-socket race: the app pools its HTTP
connection; iOS suspends the app for hours; uvicorn closes the idle
socket after ~5s; on wake the app WRITES TO THE DEAD SOCKET. A human
in the foreground retries and wins a fresh socket; a background
automation fails once and gives up silently — so scheduled syncs
looked like they never fired at all.

THE PIN. Every response from the bridge — success, 401, 400, legacy
endpoint, health check — carries `Connection: close`, so a compliant
HTTP client never pools a socket to this service and every upload
starts on a fresh connection. Keep-alive buys nothing on a LAN bridge
taking a handful of uploads a day.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_VITALS_DB", str(tmp_path / "v.db"))
    monkeypatch.setenv("HAE_API_KEY", "k")
    from bridges.healthkit import server
    return TestClient(server.app)


def test_health_check_says_connection_close(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("connection", "").lower() == "close"


def test_successful_hae_ingest_says_connection_close(client):
    r = client.post("/hae", json={"data": {"metrics": []}},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.headers.get("connection", "").lower() == "close"


def test_error_responses_say_connection_close_too(client):
    """The FAILING responses matter most — a 401 kept alive would still
    poison the client's pool for the next attempt."""
    r = client.post("/hae", json={}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    assert r.headers.get("connection", "").lower() == "close"


def test_legacy_vitals_endpoint_says_connection_close(client):
    r = client.post("/vitals", json={"metric_type": "weight",
                                     "value": 154.0})
    assert r.headers.get("connection", "").lower() == "close"
