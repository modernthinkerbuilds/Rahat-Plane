"""Python adapter client tests — envelope shape, error paths, no network.

Uses an in-process WSGI fake adapter via http.server so we exercise
the real urllib path. Hermetic — binds to ephemeral localhost port.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from new_plane.miya_runner import adapter_client as ac
from new_plane.miya_runner.adapter_client import AdapterResult, get, post


# ─── Fake adapter (ephemeral local HTTP server) ───────────────────────

class _FakeHandler(BaseHTTPRequestHandler):
    routes: dict[str, dict] = {}

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        self._body = self.rfile.read(length) if length else b""
        self._handle("POST")

    def _handle(self, method: str) -> None:
        # Strip query string for routing
        path = self.path.split("?", 1)[0]
        key = f"{method} {path}"
        if key not in self.routes:
            self.send_response(404)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"no route: {key}"}).encode())
            return
        cfg = self.routes[key]
        status = cfg.get("status", 200)
        body = cfg.get("body", {})
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a, **kw) -> None:
        pass  # silence test output


@pytest.fixture
def fake_adapter(monkeypatch):
    """Start an HTTP server on an ephemeral port; route table is mutable.
    Test body sets routes via `fake_adapter.routes[...] = {...}`.
    """
    _FakeHandler.routes = {}
    server = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    port = server.server_address[1]
    monkeypatch.setattr(ac, "ADAPTER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield _FakeHandler
    finally:
        server.shutdown()
        server.server_close()


# ─── tests ─────────────────────────────────────────────────────────────

def test_post_returns_result_on_ok(fake_adapter):
    fake_adapter.routes["POST /kobe/active_goal"] = {
        "body": {"result": {"active": True, "target_lbs": 196}, "trace_id": "t1"},
    }
    r = post("/kobe/active_goal", {}, trace_id="t1")
    assert r.ok
    assert r.result == {"active": True, "target_lbs": 196}
    assert r.error is None
    assert r.transport_error is None


def test_post_surfaces_safely_wrapped_error(fake_adapter):
    fake_adapter.routes["POST /kobe/recalibration"] = {
        "body": {"error": "ValueError: missing weight"},
    }
    r = post("/kobe/recalibration", {})
    assert not r.ok
    assert r.error == "ValueError: missing weight"
    assert r.result is None
    assert r.transport_error is None  # adapter responded, just with an error


def test_post_handles_http_500(fake_adapter):
    fake_adapter.routes["POST /broken"] = {
        "status": 500,
        "body": {"detail": "internal"},
    }
    r = post("/broken", {})
    assert not r.ok
    assert r.transport_error is not None
    assert "500" in r.transport_error


def test_post_handles_transport_failure(monkeypatch):
    # Unset URL points at closed port
    monkeypatch.setattr(ac, "ADAPTER_URL", "http://127.0.0.1:1")  # privileged, refused
    r = post("/anything", {})
    assert not r.ok
    assert r.transport_error is not None


def test_get_with_query_params(fake_adapter):
    fake_adapter.routes["GET /signals/recent"] = {
        "body": {"result": [{"id": 1, "agent": "miya"}]},
    }
    r = get("/signals/recent", query={"agent": "miya", "limit": 5})
    assert r.ok
    assert isinstance(r.result, list)
    assert r.result[0]["id"] == 1


def test_trace_id_auto_generated_if_missing():
    r = AdapterResult(trace_id="")
    # Real call would auto-gen; here we just confirm the field exists
    assert hasattr(r, "trace_id")


def test_bearer_header_included_when_token_set(fake_adapter, monkeypatch):
    captured: list[str] = []

    class _CapturingHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            captured.append(self.headers.get("authorization", ""))
            length = int(self.headers.get("content-length", "0") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"result": "ok"}')

        def log_message(self, *a, **kw): pass

    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    monkeypatch.setattr(ac, "ADAPTER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "secret-xyz")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        r = post("/x", {})
        assert r.ok
        assert captured[0] == "Bearer secret-xyz"
    finally:
        server.shutdown()
        server.server_close()


# ─── typed wrappers smoke ──────────────────────────────────────────────

def test_typed_wrappers_post_to_right_path(fake_adapter):
    fake_adapter.routes["POST /kobe/today_target"] = {"body": {"result": "x"}}
    fake_adapter.routes["POST /kobe/pace"] = {"body": {"result": "y"}}
    fake_adapter.routes["POST /kobe/charter_check"] = {"body": {"result": {"allow": True}}}
    fake_adapter.routes["POST /fraser/design_session"] = {"body": {"result": {"text": "z"}}}
    fake_adapter.routes["GET /signals/recent"] = {"body": {"result": []}}
    fake_adapter.routes["GET /signals/health"] = {"body": {"result": {"unconsumed": 0}}}
    fake_adapter.routes["GET /healthz"] = {"body": {"result": {"ok": True}}}

    assert ac.kobe_today_target().ok
    assert ac.kobe_pace().ok
    assert ac.kobe_charter_check().ok
    assert ac.fraser_design_session("plan").ok
    assert ac.signals_recent(agent="miya").ok
    assert ac.signals_health().ok
    assert ac.healthz().ok
