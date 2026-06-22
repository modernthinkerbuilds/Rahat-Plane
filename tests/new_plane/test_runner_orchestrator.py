"""Runner orchestrator tests — end-to-end with a fake in-process adapter.

The fake adapter mirrors the real FastAPI adapter's envelope shape.
Covers: tool-call ordering, budget, arbitration, charter fail-open,
signal publication, cost router invocation.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from new_plane.miya_runner import adapter_client as ac
from new_plane.miya_runner import orchestrator
from new_plane.miya_runner.orchestrator import Turn, handle


class _FakeAdapter(BaseHTTPRequestHandler):
    """Programmable in-process adapter. Tests set `responses` before calls."""
    responses: dict[str, dict] = {}
    seen_calls: list[tuple[str, dict]] = []

    def do_GET(self):  # noqa: N802
        self._respond("GET")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}
        path = self.path.split("?", 1)[0]
        self.seen_calls.append((path, payload))
        self._respond("POST")

    def _respond(self, method: str):
        path = self.path.split("?", 1)[0]
        key = f"{method} {path}"
        cfg = self.responses.get(key, {"body": {"result": None}})
        self.send_response(cfg.get("status", 200))
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(cfg.get("body", {})).encode())

    def log_message(self, *a, **kw): pass


@pytest.fixture
def fake_adapter(monkeypatch, tmp_path):
    """Start fake adapter; force the orchestrator onto the HTTP path.

    Default after ADR-013 Phase A is native_client. These tests exercise
    the HTTP path (proves the adapter contract is correct + that the
    HTTP fallback still works for OpenClaw). Native path is covered by
    test_runner_native_client.py and the parity tests.
    """
    # Swap the orchestrator's `adapter` symbol so HTTP calls flow through
    # the fake HTTP server instead of native_client.
    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.adapter", ac, raising=False,
    )

    _FakeAdapter.responses = {}
    _FakeAdapter.seen_calls = []
    server = HTTPServer(("127.0.0.1", 0), _FakeAdapter)
    port = server.server_address[1]
    monkeypatch.setattr(ac, "ADAPTER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "")

    # Isolated signal DB so we don't pollute the user's real one
    from new_plane.signals import store as signal_store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    signal_store.set_db_path(signal_db)
    signal_store.init_db()

    # Disable cost-log writes
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield _FakeAdapter
    finally:
        server.shutdown()
        server.server_close()


# ─── basic flow ───────────────────────────────────────────────────────

def test_handle_kobe_intent_pulls_facts_and_publishes_signal(fake_adapter):
    fake_adapter.responses["POST /kobe/active_goal"] = {
        "body": {"result": {"active": True, "target_lbs": 196, "weeks_to_target": 3,
                            "summary": "196 lbs by Sep 1"}},
    }
    fake_adapter.responses["POST /kobe/recalibration"] = {
        "body": {"result": {"behind_pace": False, "summary": "On pace"}},
    }
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "body": {"result": {"allow": True}},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    # 2026-06-14: "what's my plan today" now delegates to kobe_route
    # (delegate_classifier._PLAN_QUERY_RE), which skips the granular
    # active_goal/recalibration/charter flow this test pins. Switched to an
    # open-coaching message that still orchestrates (needs_kobe=True, no
    # plan/wod/log trigger) so the granular fact-pull flow stays covered.
    resp = handle(Turn(user_message="how am I tracking toward my goal", chat_id="c1"))
    assert resp.sent is True
    assert "kobe_active_goal" in resp.used_tools
    assert "kobe_recalibration" in resp.used_tools
    assert "kobe_charter_check" in resp.used_tools
    assert resp.arbitration_rule is None  # not behind pace
    assert len(resp.signals) == 1  # miya_synthesized

    # Verify the adapter was actually called
    paths = [p for p, _ in fake_adapter.seen_calls]
    assert "/kobe/active_goal" in paths
    assert "/kobe/recalibration" in paths
    assert "/kobe/charter_check" in paths


def test_handle_behind_pace_triggers_arbitration(fake_adapter):
    fake_adapter.responses["POST /kobe/active_goal"] = {
        "body": {"result": {"active": False}},
    }
    fake_adapter.responses["POST /kobe/recalibration"] = {
        "body": {"result": {"behind_pace": True, "summary": "Behind by 5500"}},
    }
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "body": {"result": {"allow": True}},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    # "where am I on pace" → triggers `pace` keyword in Kobe intent regex,
    # so the orchestrator pulls recalibration; that returns behind_pace=True,
    # arbitration verdict fires.
    resp = handle(Turn(user_message="where am I on pace", chat_id="c1"))
    assert resp.arbitration_rule == "behind_pace"
    # Arbitration firing should escalate to Pro model
    assert "arbitration-fired" in resp.routing.get("reason", "")


def test_handle_design_request_calls_fraser(fake_adapter):
    fake_adapter.responses["POST /kobe/active_goal"] = {
        "body": {"result": {"active": False}},
    }
    fake_adapter.responses["POST /kobe/recalibration"] = {
        "body": {"result": {"behind_pace": False}},
    }
    fake_adapter.responses["POST /fraser/design_session"] = {
        "body": {"result": {"text": "5 rounds: 10 squats, 8 pullups"}},
    }
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "body": {"result": {"allow": True}},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    resp = handle(Turn(user_message="design me a workout", chat_id="c1"))
    assert "fraser_design_session" in resp.used_tools
    paths = [p for p, _ in fake_adapter.seen_calls]
    assert "/fraser/design_session" in paths


def test_handle_charter_veto_blocks_send(fake_adapter):
    fake_adapter.responses["POST /kobe/active_goal"] = {"body": {"result": {"active": False}}}
    fake_adapter.responses["POST /kobe/recalibration"] = {"body": {"result": {}}}
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "body": {"result": {"allow": False, "reason": "cool-down active"}},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    # 2026-06-14: "what's my plan" now delegates to kobe_route, which does
    # NOT run the orchestrator's outbound charter gate (the D1 gap — see
    # tests/new_plane/test_charter_on_delegation_D1.py, flagged as the top
    # safety item). This test pins the orchestrate-path outbound veto, which
    # is the gate that DOES fire today; use an open-coaching message so it
    # exercises that path. The delegation-path behaviour is pinned (both
    # ways, owner-gated) in the D1 file.
    resp = handle(Turn(user_message="how am I tracking toward my goal", chat_id="c1"))
    assert resp.sent is False
    assert resp.veto_reason == "cool-down active"
    assert resp.text == ""


def test_handle_charter_transport_error_fails_open(fake_adapter):
    # Force the charter endpoint to fail with a 500 — simulates an
    # adapter-side crash. Runner must fail-OPEN (still send) so transient
    # adapter blips don't cause silent message drops.
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "status": 500, "body": {"detail": "boom"},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    resp = handle(Turn(user_message="hi", chat_id="c1"))
    assert resp.sent is True
    assert any("charter_check" in te for te in resp.transport_errors)


def test_handle_non_kobe_intent_only_calls_charter(fake_adapter):
    fake_adapter.responses["POST /kobe/charter_check"] = {
        "body": {"result": {"allow": True}},
    }
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    resp = handle(Turn(user_message="hello", chat_id="c1"))
    # No Kobe context words → no Kobe tool calls
    assert "kobe_active_goal" not in resp.used_tools
    assert "kobe_recalibration" not in resp.used_tools
    assert "kobe_charter_check" in resp.used_tools


def test_handle_respects_autonomy_budget(fake_adapter):
    """Even a design+Kobe request must not exceed 3 tool calls."""
    fake_adapter.responses["POST /kobe/active_goal"] = {"body": {"result": {}}}
    fake_adapter.responses["POST /kobe/recalibration"] = {"body": {"result": {}}}
    fake_adapter.responses["POST /fraser/design_session"] = {"body": {"result": {"text": "x"}}}
    fake_adapter.responses["POST /kobe/charter_check"] = {"body": {"result": {"allow": True}}}
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    resp = handle(Turn(user_message="design my workout this week", chat_id="c1"))
    # charter_check is mandatory and not budgeted; the rest must total ≤ 3
    budgeted = [t for t in resp.used_tools if t != "kobe_charter_check"]
    assert len(budgeted) <= 3


def test_handle_adapter_down_still_produces_response(monkeypatch, tmp_path):
    """If the adapter is unreachable, runner must NOT crash. Fail-open.

    Forces HTTP mode so this exercises the adapter-down path (native
    client has no remote dependency, so this scenario is HTTP-only).
    """
    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.adapter", ac, raising=False,
    )
    monkeypatch.setattr(ac, "ADAPTER_URL", "http://127.0.0.1:1")  # closed
    monkeypatch.setattr(ac, "ADAPTER_TOKEN", "")

    from new_plane.signals import store as signal_store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    signal_store.set_db_path(signal_db)
    signal_store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")

    resp = handle(Turn(user_message="hi", chat_id="c1"))
    # Transport errors expected, but the loop didn't crash
    assert len(resp.transport_errors) > 0
    # Charter fails open on transport error
    assert resp.sent is True
    # Fallback text is non-empty
    assert resp.text


def test_handle_signal_payload_contains_routing_and_synthesis(fake_adapter):
    fake_adapter.responses["POST /kobe/active_goal"] = {"body": {"result": {"active": False}}}
    fake_adapter.responses["POST /kobe/recalibration"] = {"body": {"result": {"behind_pace": False}}}
    fake_adapter.responses["POST /kobe/charter_check"] = {"body": {"result": {"allow": True}}}
    fake_adapter.responses["GET /signals/recent"] = {"body": {"result": []}}

    resp = handle(Turn(user_message="plan today", chat_id="c1"))
    # Read it back from the signal store
    from new_plane.signals.store import recent
    sigs = recent(agent="miya", limit=1)
    assert sigs
    payload = sigs[0]["payload"]
    assert "routing" in payload
    assert payload["routing"]["model"] in ("gemini-2.5-flash", "gemini-2.5-pro")
    assert "synthesis" in payload
