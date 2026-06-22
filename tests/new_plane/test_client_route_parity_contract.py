"""§1.2/§1.3 — native ⇄ adapter client route parity + native-exception
non-silent-drop (PRE_SCALE B-P0 / D).

B-P0: `native_client` defines `kobe_route` / `fraser_route` /
`huberman_route`; `adapter_client` defines NONE of them. The orchestrator
hard-switches on `NEW_MIYA_USE_HTTP_CLIENT` and calls `adapter.kobe_route`
→ AttributeError in HTTP mode (the root cause of the compare-harness reds
and several delegation failures before they were repointed).

This file pins the divergence PERMANENTLY so it can't silently widen:
  * the delegation route surface is asserted on both clients — xfail today
    because adapter_client lacks it (flip to a hard pin when the surface is
    unified per owner decision D2);
  * native client raising mid-turn must surface an error to the user, never
    a silent drop.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import native_client as nc
from new_plane.miya_runner import adapter_client as ac
from new_plane.miya_runner.orchestrator import Turn, handle


_DELEGATION_ROUTE_SURFACE = ("kobe_route", "fraser_route", "huberman_route")


def test_native_client_has_the_delegation_route_surface():
    """Native client (production default) MUST expose the route methods the
    orchestrator's delegation branches call."""
    for name in _DELEGATION_ROUTE_SURFACE:
        assert hasattr(nc, name) and callable(getattr(nc, name)), (
            f"native_client missing {name} — delegation path would crash"
        )


def test_adapter_client_route_parity_with_native():
    """Both clients MUST expose the identical delegation route surface so the
    orchestrator can swap blindly (NEW_MIYA_USE_HTTP_CLIENT). The B-P0
    divergence is closed: adapter_client now defines kobe_route/fraser_route/
    huberman_route (each POSTs to /kobe/route etc.). Pinned GREEN so that a
    revert — which would re-AttributeError every delegating turn in HTTP
    mode — fires this test.

    NOTE: this asserts SURFACE parity (the methods exist + are callable). It
    does not assert the server-side /route endpoints exist; that is the
    remaining half of owner decision D2 (keep HTTP for OpenClaw vs delete the
    branch) and is tracked in the findings report, not here."""
    for name in _DELEGATION_ROUTE_SURFACE:
        assert hasattr(ac, name) and callable(getattr(ac, name)), (
            f"REGRESSION (B-P0 reopened): adapter_client missing {name} — "
            f"HTTP mode diverges from native and AttributeErrors on delegation"
        )
    # And the call signatures must match native so the orchestrator's call
    # site (adapter.kobe_route(msg, chat_id=, trace_id=)) works on both.
    import inspect
    for name in _DELEGATION_ROUTE_SURFACE:
        ac_params = set(inspect.signature(getattr(ac, name)).parameters)
        nc_params = set(inspect.signature(getattr(nc, name)).parameters)
        assert {"chat_id", "trace_id"} <= ac_params, f"{name} adapter sig"
        assert {"chat_id", "trace_id"} <= nc_params, f"{name} native sig"


def test_adapter_server_defines_the_route_endpoints():
    """D2 (finished 2026-06-14): the HTTP adapter server
    (bridges/openclaw_adapters/server.py) defines /kobe/route, /fraser/route,
    /huberman/route handlers, so adapter_client's POSTs resolve end-to-end
    (not just at the client surface). Asserted at the function level to avoid
    booting FastAPI (starlette/TestClient is flaky in this sandbox). A revert
    of the endpoints fires this test."""
    import inspect
    from bridges.openclaw_adapters import server as adapter_server
    src = inspect.getsource(adapter_server)
    for path in ('"/kobe/route"', '"/fraser/route"', '"/huberman/route"'):
        assert path in src, (
            f"adapter server missing the {path} endpoint — HTTP-mode "
            f"delegation would 404 (B-P0/D2 reopened)"
        )
    # The handler functions exist and are callable.
    for fn in ("kobe_route", "fraser_route", "huberman_route"):
        assert callable(getattr(adapter_server, fn, None)), (
            f"adapter server route handler {fn} missing"
        )


def test_native_route_exception_is_not_silently_dropped(monkeypatch, tmp_path):
    """If Kobe's route() raises mid-turn, the user must get a non-empty
    reply (the error), never silence. native_client wraps the exception as
    AdapterResult.error; the orchestrator surfaces it."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    store.set_db_path(tmp_path / "sig.db")
    store.init_db()
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(tmp_path / "sig.db"))
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")

    def boom(msg):
        raise RuntimeError("kobe internals crashed")

    monkeypatch.setattr("agents.the_scientist.handler.route", boom)
    resp = handle(Turn(user_message="/pace", chat_id="c-crash"))

    assert resp.trace_id
    assert resp.text.strip() != "", "user got a SILENT drop on a route crash"
    assert ("RuntimeError" in resp.text or "crashed" in resp.text), (
        "the crash was swallowed without surfacing an error to the user"
    )
