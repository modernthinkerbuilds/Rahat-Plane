"""Regression: runner boots without the HTTP adapter in native mode (2026-06-14).

Cutover-prep. The new-plane runner defaults to the native client (direct
Python imports of Kobe/Fraser) and has no remote dependency. Before this
fix, __main__._preflight() always called the HTTP adapter's healthz() and
*refused to boot* if the :8766 FastAPI adapter was down — even in native
mode. That tied the production runner to com.rahat.sugar.bridge and would
have made the post-cutover service fragile.

Contract pinned here:
  - native mode (default): _preflight() returns 0 even when the HTTP
    adapter is unreachable.
  - HTTP mode (NEW_MIYA_USE_HTTP_CLIENT=1): _preflight() still returns
    non-zero when the adapter is unreachable (gate preserved).
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import __main__ as runner_main
from new_plane.miya_runner import adapter_client, native_client


@pytest.fixture
def http_adapter_down(monkeypatch):
    """Simulate the FastAPI adapter being unreachable."""
    def _dead_healthz(*a, **k):
        return adapter_client.AdapterResult(
            trace_id="t", transport_error="connection refused")
    monkeypatch.setattr(adapter_client, "healthz", _dead_healthz)
    # runner_main.adapter is bound to adapter_client at import; patch it too.
    monkeypatch.setattr(runner_main.adapter, "healthz", _dead_healthz,
                        raising=False)


def test_native_mode_boots_with_adapter_down(monkeypatch, http_adapter_down):
    monkeypatch.delenv("NEW_MIYA_USE_HTTP_CLIENT", raising=False)
    assert runner_main._preflight() == 0


def test_explicit_native_mode_boots_with_adapter_down(monkeypatch, http_adapter_down):
    monkeypatch.setenv("NEW_MIYA_USE_HTTP_CLIENT", "0")
    assert runner_main._preflight() == 0


def test_http_mode_still_gates_on_adapter(monkeypatch, http_adapter_down):
    monkeypatch.setenv("NEW_MIYA_USE_HTTP_CLIENT", "1")
    assert runner_main._preflight() == 2


def test_native_healthz_has_no_remote_dependency():
    r = native_client.healthz()
    assert r.ok
    assert r.result.get("client") == "native"
