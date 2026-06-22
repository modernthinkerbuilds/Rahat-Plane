"""Phase B: live-DB unification, flag-gated.

When NEW_MIYA_USE_LIVE_DB=1, the runner orchestrator mirrors each turn
into core.decisions (vault/rahat.db). When the flag is OFF (default),
the runner only writes to its own signal store.

This is reversible — flipping the flag back stops new writes; existing
rows stay where they were written.

Tests run under RAHAT_TEST_MODE so writes hit the test sandbox DB,
not the live vault.
"""
from __future__ import annotations

import os

import pytest

from new_plane.miya_runner.orchestrator import Turn, handle


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    from new_plane.signals import store
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")


def _decision_count_for(trace_id: str) -> int:
    from core import decisions as _dec
    return len(_dec.by_trace(trace_id))


def test_default_off_no_live_db_writes(monkeypatch):
    """Default behavior: no NEW_MIYA_USE_LIVE_DB env, runner does NOT
    mirror to vault/rahat.db. Only signal store is written."""
    monkeypatch.delenv("NEW_MIYA_USE_LIVE_DB", raising=False)
    resp = handle(Turn(user_message="hi", chat_id="c1"))
    assert resp.signals  # signal published
    assert _decision_count_for(resp.trace_id) == 0


def test_explicit_off_no_live_db_writes(monkeypatch):
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "0")
    resp = handle(Turn(user_message="hi", chat_id="c1"))
    assert _decision_count_for(resp.trace_id) == 0


def test_flag_on_writes_decision_row(monkeypatch):
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "1")
    resp = handle(Turn(user_message="what is my plan today", chat_id="c1"))
    # Should have exactly one miya.v2 row for this trace
    count = _decision_count_for(resp.trace_id)
    assert count == 1, f"Expected 1 decision row, got {count}"


def test_flag_on_decision_row_contents(monkeypatch):
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "1")
    # 2026-06-14: "what is my plan today" delegates → op="delegated" row
    # shape {text}, not the orchestrate op="turn" {tools_used, model} shape
    # this test pins. Open-coaching message keeps it on the orchestrate path.
    msg = "how am I tracking toward my goal"
    resp = handle(Turn(user_message=msg, chat_id="c1"))

    from core import decisions as _dec
    rows = _dec.by_trace(resp.trace_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "miya.v2"
    assert row["op"] == "turn"
    assert row["outcome"] in ("ok", "vetoed")
    # input_json + output_json should be valid JSON containing key fields
    import json
    in_data = json.loads(row["input_json"])
    out_data = json.loads(row["output_json"])
    assert in_data["chat_id"] == "c1"
    assert in_data["user_message"] == msg
    assert "tools_used" in out_data
    assert "model" in out_data


def test_flag_on_writes_token_usage_when_present(monkeypatch):
    """Synthesizer reports prompt/output tokens; the decision should
    capture them so the eval suite can compute cost over time."""
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "1")
    # Stub the synthesizer to return known token counts
    from new_plane.miya_runner import synthesizer
    from new_plane.miya_runner.synthesizer import SynthesisResult
    monkeypatch.setattr(
        synthesizer, "synthesize",
        lambda **kw: SynthesisResult(
            text="stub response",
            model="gemini-2.5-flash",
            prompt_tokens=350,
            output_tokens=85,
        ),
    )
    # 2026-06-14: open-coaching message keeps the turn on the orchestrate
    # path, which calls synthesizer.synthesize (stubbed here) and records
    # its token counts. The delegate path re-voices but logs op="delegated"
    # without the tokens_in/out fields this test asserts.
    resp = handle(Turn(user_message="how am I tracking toward my goal", chat_id="c1"))

    from core import decisions as _dec
    rows = _dec.by_trace(resp.trace_id)
    assert rows[0]["tokens_in"] == 350
    assert rows[0]["tokens_out"] == 85


def test_live_db_write_failure_does_not_crash_turn(monkeypatch):
    """If core.decisions.log explodes, the turn should still complete
    and return a valid Response. Observability must never break runtime."""
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "1")
    from core import decisions as _dec
    def boom(**kw):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(_dec, "log", boom)

    # Should not raise
    resp = handle(Turn(user_message="hi", chat_id="c1"))
    assert resp.trace_id
    assert resp.sent in (True, False)


def test_flag_on_charter_veto_records_vetoed_outcome(monkeypatch):
    """When charter blocks the send, the live-DB row should record
    outcome='vetoed' with the reason in the error field."""
    monkeypatch.setenv("NEW_MIYA_USE_LIVE_DB", "1")
    # Stub charter to veto
    monkeypatch.setattr(
        "new_plane.miya_runner.native_client.kobe_charter_check",
        lambda **kw: __import__("new_plane.miya_runner.adapter_client",
                                fromlist=["AdapterResult"]).AdapterResult(
            trace_id=kw.get("trace_id", "t"),
            result={"allow": False, "reason": "quiet hours"},
            http_status=200,
        ),
    )
    resp = handle(Turn(user_message="hi", chat_id="c1"))
    assert resp.sent is False

    from core import decisions as _dec
    rows = _dec.by_trace(resp.trace_id)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "vetoed"
    assert "quiet hours" in (rows[0]["error"] or "")
