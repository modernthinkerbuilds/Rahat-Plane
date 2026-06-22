"""Regression: native_client / adapter_client surface parity (2026-06-15, Lane 5).

The orchestrator hard-switches between native_client (default) and
adapter_client (NEW_MIYA_USE_HTTP_CLIENT=1) and calls adapter.kobe_route /
fraser_route / huberman_route. adapter_client lacked all three, so HTTP mode
crashed with AttributeError (root cause of the pre-existing new_plane HTTP
test failures and a broken ADR-014 OpenClaw-integration surface).

This contract test pins that BOTH clients expose the identical delegation +
health + signal surface with compatible route signatures, so the two can
never silently diverge again.
"""
from __future__ import annotations

import inspect

from new_plane.miya_runner import adapter_client, native_client

# The surface the orchestrator + runner depend on from "adapter".
REQUIRED_CALLABLES = [
    "kobe_route", "fraser_route", "huberman_route",
    "healthz", "signals_recent", "signals_health",
]
ROUTE_FNS = ["kobe_route", "fraser_route", "huberman_route"]
ROUTE_PARAMS = {"message", "chat_id", "trace_id"}


def test_both_clients_expose_required_surface():
    for name in REQUIRED_CALLABLES:
        assert callable(getattr(native_client, name, None)), \
            f"native_client missing {name}"
        assert callable(getattr(adapter_client, name, None)), \
            f"adapter_client missing {name} (HTTP mode would AttributeError)"


def test_route_signatures_match():
    for name in ROUTE_FNS:
        for mod in (native_client, adapter_client):
            params = set(inspect.signature(getattr(mod, name)).parameters)
            assert ROUTE_PARAMS <= params, \
                f"{mod.__name__}.{name} params {params} missing {ROUTE_PARAMS - params}"


def test_no_drift_native_superset_of_adapter_routes():
    # Every route the HTTP client offers must also exist natively (and vice
    # versa for the three delegation routes) — the exact drift that broke us.
    for name in ROUTE_FNS:
        assert hasattr(native_client, name) and hasattr(adapter_client, name)
