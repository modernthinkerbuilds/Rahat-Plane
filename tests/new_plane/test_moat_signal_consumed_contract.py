"""§1.5 MOAT CONTRACT (P0, before Genie) — a published typed signal must be
*consumed in an actual decision*, not merely read.

Per the PM thesis §8.3 (moat §1, §4 #6): cross-agent cross-pollination only
counts when a reader folds a signal into a decision and records that with
`store.mark_consumed(signal_id, consumer)`. Reading a signal into a prompt
and then NOT marking it consumed leaves the moat's load-bearing rule as
decoration.

CURRENT STATE (traced):
  * `orchestrator.handle()` pulls `recent_signals` into the synth prompt —
    a genuine decision input — at orchestrator.py:803-809
    (`adapter.signals_recent(...)` → `_build_prompt(..., recent_signals=)`).
  * It never calls `store.mark_consumed(...)` on those signals.
  * `mark_consumed` is invoked nowhere in the runtime — only by the HTTP
    adapter endpoint (`new_plane/.../server.py:346`) and tests
    (PRE_SCALE C-P0). So `unconsumed_count()` reports ~100%.

This test pins the rule the moat depends on. It is EXPECTED TO FAIL until
the consumption wiring lands — that's the point: `xfail(strict=False)` so it
flips to an XPASS (a loud "remove the xfail / the moat is wired now" signal)
the moment the architect threads `mark_consumed` into the decision path,
without breaking the suite in the meantime.
"""
from __future__ import annotations

import pytest

from new_plane.signals import store
from new_plane.miya_runner.orchestrator import Turn, handle


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Hermetic: native client, isolated signal DB, no cost log, test mode."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    signal_db = tmp_path / "signals.db"
    monkeypatch.setenv("OPENCLAW_SIGNALS_DB", str(signal_db))
    store.set_db_path(signal_db)
    store.init_db()
    monkeypatch.setenv("OPENCLAW_COST_LOG", "")
    from new_plane.miya_runner import cost_router
    monkeypatch.setattr(cost_router, "COST_LOG_PATH", "")
    # Open-coaching turns get the wider (unscoped) signal view, so a kobe
    # signal is visible regardless of intent scoping.
    yield


def test_signal_read_into_decision_is_marked_consumed():  # HARD PIN (moat wired 2026-06-16)
    """Publish a kobe signal, run a turn that pulls it into the synth
    prompt (a decision), and assert that signal is now recorded as consumed
    by miya. Fails today — the moat is unwired."""
    sid = store.publish(
        agent="kobe", type_="pace_update",
        payload={"behind_pace": False, "summary": "On pace"},
        trace_id="moat-t1",
    )

    # Open-coaching turn: orchestrate path pulls recent_signals (the kobe
    # signal above) into _build_prompt — a real decision input.
    resp = handle(Turn(user_message="how am I tracking toward my goal",
                       chat_id="c-moat"))
    assert resp.trace_id  # the turn ran

    # The moat rule: a signal folded into a decision must be marked consumed.
    rows = store.recent(limit=50)
    consumed_row = next((r for r in rows if r["id"] == sid), None)
    assert consumed_row is not None, "published signal vanished"
    assert "miya" in consumed_row["consumed_by"], (
        "MOAT VIOLATION: the kobe signal was read into the synth decision "
        "but never marked consumed. The typed cross-agent interface is "
        "decoration until mark_consumed runs on the decision path."
    )


def test_unconsumed_count_drops_after_a_consuming_turn():  # HARD PIN (moat wired 2026-06-16)
    """A turn that consumes a signal should reduce unconsumed_count for the
    publishing agent. Fails today for the same root cause."""
    store.publish(agent="kobe", type_="pace_update",
                  payload={"v": 1}, trace_id="moat-t2")
    before = store.unconsumed_count(agent="kobe")
    assert before >= 1

    handle(Turn(user_message="how am I tracking toward my goal",
                chat_id="c-moat"))

    after = store.unconsumed_count(agent="kobe")
    assert after < before, (
        f"unconsumed_count for kobe did not drop "
        f"({before} -> {after}); the consumed signal was never recorded."
    )


# ─── Guard the contract surface itself (these MUST pass — they pin the API
#     the wiring will call, so the moat test above stays meaningful). ──────
def test_mark_consumed_api_records_consumer():
    """`mark_consumed` itself works — so when the decision path finally
    calls it, the moat tests above will pass. This pins the primitive."""
    sid = store.publish(agent="fraser", type_="design_done",
                        payload={"w": "Cindy"}, trace_id="moat-api")
    assert store.mark_consumed(sid, "miya") is True       # newly added
    assert store.mark_consumed(sid, "miya") is False      # idempotent
    row = next(r for r in store.recent(limit=10) if r["id"] == sid)
    assert "miya" in row["consumed_by"]
    assert store.unconsumed_count(agent="fraser") == 0


def test_mark_consumed_unknown_signal_raises():
    with pytest.raises(KeyError):
        store.mark_consumed(999_999, "miya")
