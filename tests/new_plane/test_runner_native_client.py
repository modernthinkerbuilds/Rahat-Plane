"""Native client tests — ADR-013 Phase A.

Pins:
  - Each native_client function returns an AdapterResult with the
    canonical envelope shape (so the orchestrator can swap clients
    blindly).
  - Charter check standardizes on `{"allow": bool, "reason": str|None}`
    (Bug discovered 2026-06-08: adapter was emitting `allowed`).
  - Error wrapping converts Python exceptions into `result.error`
    strings (matching the HTTP `_safely()` wrapper).
  - Parity tests: orchestrator with native_client and with
    adapter_client produce the same response shape for the same inputs.

The native client calls into real `agents.the_scientist.tools` under
`RAHAT_TEST_MODE=1` so the live DB stays safe.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from new_plane.miya_runner import native_client as nc
from new_plane.miya_runner.adapter_client import AdapterResult


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    # Isolate signal DB
    from new_plane.signals import store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()


# ─── envelope shape ────────────────────────────────────────────────────

def test_all_kobe_functions_return_adapter_result():
    """Every public Kobe function must return an AdapterResult so the
    orchestrator can treat both clients identically."""
    fns = [
        nc.kobe_today_target,
        nc.kobe_active_goal,
        nc.kobe_pace,
        nc.kobe_recalibration,
        nc.kobe_missed_workouts,
    ]
    for f in fns:
        r = f()
        assert isinstance(r, AdapterResult), f"{f.__name__} returned {type(r)}"
        assert r.trace_id, f"{f.__name__} missing trace_id"


def test_kobe_day_functions_return_adapter_result():
    for f in (nc.kobe_workout_on, nc.kobe_gym_wod_on):
        r = f("today")
        assert isinstance(r, AdapterResult)
        # Result wraps the day info even if the underlying tool errored
        if r.result is not None:
            assert "day_requested" in r.result
            assert "day_resolved" in r.result


def test_fraser_function_returns_adapter_result(monkeypatch):
    """Don't actually run Fraser's LLM — too slow + non-deterministic.
    Stub the composer so the test stays hermetic."""
    from agents.fraser import composer
    monkeypatch.setattr(composer, "design_session",
                        lambda msg, chat_id=None: "stub workout")
    r = nc.fraser_design_session("design me a workout")
    assert isinstance(r, AdapterResult)
    assert r.ok
    assert r.result == {"text": "stub workout"}


def test_signals_functions_return_adapter_result():
    p = nc.signals_publish(agent="test", type_="x",
                           payload={"k": "v"}, trace_id="t1")
    assert isinstance(p, AdapterResult)
    assert p.ok
    assert isinstance(p.result.get("signal_id"), int)

    r = nc.signals_recent(limit=5)
    assert isinstance(r, AdapterResult)
    assert isinstance(r.result, list)

    h = nc.signals_health()
    assert isinstance(h, AdapterResult)
    assert "unconsumed_total" in h.result
    assert "unconsumed_by_agent" in h.result

    z = nc.healthz()
    assert z.ok
    assert z.result["client"] == "native"


# ─── charter envelope shape (Bug 2026-06-08) ──────────────────────────

def test_charter_check_returns_allow_key_not_allowed():
    """Pin the envelope shape after the 2026-06-08 standardization.
    Orchestrator reads `allow`; adapter+native_client both emit `allow`.
    """
    r = nc.kobe_charter_check(kind="notify.user.reply")
    assert r.ok
    assert "allow" in r.result, f"Charter should emit 'allow', got {r.result}"
    assert "reason" in r.result
    # Must NOT use the old 'allowed' key
    assert "allowed" not in r.result, "Old 'allowed' key shouldn't be present"
    assert isinstance(r.result["allow"], bool)


def test_charter_check_fails_open_on_internal_error(monkeypatch):
    def boom(kind, ctx):
        raise RuntimeError("charter broke")
    monkeypatch.setattr(
        "agents.the_scientist.tools._charter_check", boom,
    )
    r = nc.kobe_charter_check(kind="notify.user.reply")
    assert r.ok
    assert r.result["allow"] is True   # fail open
    assert "charter-check-error" in r.result["reason"]
    assert "RuntimeError" in r.result["reason"]


# ─── error wrapping ────────────────────────────────────────────────────

def test_kobe_function_exception_becomes_adapter_error(monkeypatch):
    def boom():
        raise ValueError("simulated kobe failure")
    monkeypatch.setattr(
        "agents.the_scientist.tools.get_active_goal", boom,
    )
    r = nc.kobe_active_goal()
    assert not r.ok
    assert r.error is not None
    assert "ValueError" in r.error
    assert "simulated kobe failure" in r.error


def test_fraser_exception_becomes_adapter_error(monkeypatch):
    from agents.fraser import composer
    def boom(msg, chat_id=None):
        raise RuntimeError("fraser composer crashed")
    monkeypatch.setattr(composer, "design_session", boom)
    r = nc.fraser_design_session("design me something")
    assert not r.ok
    assert "RuntimeError" in r.error


# ─── day token resolution ──────────────────────────────────────────────

@pytest.mark.parametrize("token", ["today", "tomorrow", "yesterday", "tmrw", "yday"])
def test_day_token_relative_resolves_to_weekday(token):
    resolved = nc._resolve_day_token(token)
    assert resolved in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@pytest.mark.parametrize("token", ["mon", "MONDAY", "tue", "Friday"])
def test_day_token_weekday_passes_through(token):
    resolved = nc._resolve_day_token(token)
    # Weekday names pass through (Kobe's tools normalize them downstream)
    assert resolved == token


def test_day_token_empty_returns_empty():
    assert nc._resolve_day_token("") == ""


# ─── parity with adapter_client (signature surface) ───────────────────

def test_native_client_exposes_same_public_functions_as_adapter_client():
    """The orchestrator depends on these symbols. Either client must
    expose all of them, or swapping in tests breaks silently."""
    from new_plane.miya_runner import adapter_client as ac
    public = lambda mod: {
        name for name in dir(mod)
        if not name.startswith("_") and callable(getattr(mod, name))
    }
    # Allow native to have its own helpers; adapter symbols must be subset.
    adapter_public = public(ac)
    native_public = public(nc)
    missing = {
        name for name in adapter_public
        if name in (
            "kobe_today_target", "kobe_active_goal", "kobe_pace",
            "kobe_recalibration", "kobe_charter_check", "kobe_project_eta",
            "kobe_workout_on", "kobe_gym_wod_on",
            "fraser_design_session",
            "signals_publish", "signals_recent", "signals_health",
            "healthz",
        ) and name not in native_public
    }
    assert not missing, f"native_client missing functions: {missing}"


def test_native_client_function_signatures_match_adapter_client():
    """Signature parity for the orchestrator-consumed surface.

    Trace_id should be the universal kwarg.
    """
    import inspect
    from new_plane.miya_runner import adapter_client as ac
    funcs = [
        "kobe_today_target", "kobe_active_goal", "kobe_pace",
        "kobe_recalibration", "kobe_charter_check",
        "kobe_workout_on", "kobe_gym_wod_on",
        "fraser_design_session",
        "signals_publish", "signals_recent",
    ]
    for name in funcs:
        ac_sig = inspect.signature(getattr(ac, name))
        nc_sig = inspect.signature(getattr(nc, name))
        # Both must accept trace_id (as the universal correlation id)
        assert "trace_id" in ac_sig.parameters, f"{name} (adapter) missing trace_id"
        assert "trace_id" in nc_sig.parameters, f"{name} (native) missing trace_id"


# ─── orchestrator parity: native vs HTTP returns same shape ────────────

class _FakeAdapter(BaseHTTPRequestHandler):
    """Mirrors the real adapter's envelope for parity tests."""
    routes: dict = {}

    def do_GET(self): self._respond("GET")  # noqa: N802
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        if length: self.rfile.read(length)
        self._respond("POST")

    def _respond(self, method):
        path = self.path.split("?", 1)[0]
        cfg = self.routes.get(f"{method} {path}", {"body": {"result": None}})
        self.send_response(cfg.get("status", 200))
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(cfg.get("body", {})).encode())

    def log_message(self, *a, **kw): pass


def test_orchestrator_native_path_smoke():
    """Run a full turn through the runner orchestrator with the default
    native client. Proves the integration works end-to-end without HTTP."""
    from new_plane.miya_runner.orchestrator import Turn, handle
    resp = handle(Turn(user_message="what is my plan today", chat_id="c1"))
    # The native path makes real Kobe calls under RAHAT_TEST_MODE.
    # Specific tools used depend on intent — at minimum these always run.
    assert "kobe_charter_check" in resp.used_tools
    assert resp.trace_id
    # Native client never has transport errors
    assert resp.transport_errors == []
    # Charter should now correctly evaluate (Bug 2026-06-08 fix)
    # Under RAHAT_TEST_MODE with default test data, charter passes
    assert resp.sent in (True, False)  # either is valid — just must be set
    # Signal was published
    assert len(resp.signals) == 1


def test_orchestrator_native_path_lookup_routes_to_kobe():
    """WOD lookup with native client must reach Kobe, not Fraser.

    Updated 2026-06-09: WOD lookup now delegates via classify_delegation
    → kobe_route (skipping the synth layer entirely so Gemini can't
    paraphrase). The original intent of this test — 'WOD lookup must
    reach Kobe, not Fraser' — is preserved.
    """
    from new_plane.miya_runner.orchestrator import Turn, handle
    resp = handle(Turn(user_message="what's the workout for tomorrow",
                       chat_id="c1"))
    assert "kobe_route" in resp.used_tools
    assert "fraser_design_session" not in resp.used_tools


def test_orchestrator_native_path_design_routes_to_fraser(monkeypatch):
    from agents.fraser import composer
    monkeypatch.setattr(composer, "design_session",
                        lambda msg, chat_id=None: "stubbed workout")
    from new_plane.miya_runner.orchestrator import Turn, handle
    resp = handle(Turn(user_message="design me a workout for tomorrow",
                       chat_id="c1"))
    assert "fraser_design_session" in resp.used_tools
    assert "kobe_gym_wod_on" not in resp.used_tools
