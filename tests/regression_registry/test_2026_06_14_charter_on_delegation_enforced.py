"""Pin: 2026-06-14 / refined 2026-06-16 — outbound charter on the delegation
paths is CONTEXT-AWARE (owner decision D1; PRE_SCALE D-P0).

Before: `kobe_route`/`fraser_route`/`huberman_route` returned `sent=True`
without consulting the outbound charter at all.

After (context-aware): every delegation branch runs `_delegated_outbound_charter`
so the reply is GOVERNED + AUDITED, but suppression is asymmetric:
  - USER-INITIATED reply (the default): a charter veto is logged but the reply
    STILL SENDS. When the user asks, they always get an answer — even "no".
  - PROACTIVE/unprompted send (`Turn(..., proactive=True)`): a veto suppresses
    the reply (`sent=False`, text dropped, vetoed row mirrored to the ledger).

This guards both halves so a refactor that (a) drops the gate, or (b) starts
silently dropping user answers, fires immediately.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import native_client as nc
from new_plane.miya_runner.adapter_client import AdapterResult
from new_plane.miya_runner.orchestrator import Turn, handle


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    store.set_db_path(tmp_path / "sig.db")
    store.init_db()
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(tmp_path / "sig.db"))
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    monkeypatch.setenv("NEW_MIYA_REVOICE", "0")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    yield


def _veto(monkeypatch):
    def fake(*, kind="notify.user.reply", ctx=None, trace_id=None):
        return AdapterResult(trace_id=trace_id or "t",
                             result={"allow": False, "reason": "quiet hours"},
                             http_status=200)
    monkeypatch.setattr(nc, "kobe_charter_check", fake)


def test_user_initiated_reply_is_never_suppressed_even_on_veto(monkeypatch):
    """Context-aware D1: a vetoing charter does NOT drop a reply to a message
    the user sent — the user always gets an answer. The gate is still consulted
    (audit), but the answer sends."""
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda m: "deterministic reply")
    _veto(monkeypatch)
    resp = handle(Turn(user_message="/pace", chat_id="c"))
    assert resp.routing["path"] == "kobe_route"
    assert resp.sent is True, "user-initiated reply must NOT be suppressed"
    assert "deterministic reply" in resp.text
    assert "kobe_charter_check" in resp.used_tools, "gate must still be consulted (audit)"


def test_proactive_send_is_suppressed_on_veto(monkeypatch):
    """A proactive/unprompted send IS suppressed when the charter vetoes."""
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda m: "deterministic reply")
    _veto(monkeypatch)
    resp = handle(Turn(user_message="/pace", chat_id="c", proactive=True))
    assert resp.routing["path"] == "kobe_route"
    assert resp.sent is False, "proactive send must be suppressed on charter veto"
    assert resp.text == ""
    assert resp.veto_reason and "quiet hours" in resp.veto_reason


def test_allowing_charter_lets_delegated_reply_through(monkeypatch):
    monkeypatch.setattr("agents.the_scientist.handler.route",
                        lambda m: "deterministic reply")

    def fake(*, kind="notify.user.reply", ctx=None, trace_id=None):
        return AdapterResult(trace_id=trace_id or "t",
                             result={"allow": True, "reason": None},
                             http_status=200)
    monkeypatch.setattr(nc, "kobe_charter_check", fake)

    resp = handle(Turn(user_message="/pace", chat_id="c"))
    assert resp.sent is True
    assert "deterministic reply" in resp.text
    assert "kobe_charter_check" in resp.used_tools
