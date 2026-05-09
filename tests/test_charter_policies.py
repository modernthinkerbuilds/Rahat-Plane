"""Charter — built-in policy unit tests.

The Charter is the policy plane every WorkOrder passes through. Three
built-in policies ship with Rahat (per `core/charter.py`):

  * `quiet_hours`         — globs `notify.*.nudge` only. Replies pass.
  * `hrv_red_blocks`      — vetoes `coach.push_*` when HRV < 30.
  * `external_veto_check` — honors any `external_veto_subject` in ctx.

These tests pin the wiring with no Miya / no Telegram mocks — the
charter is exercised directly. If the matrix here regresses, the rest
of the system is no longer trustworthy regardless of orchestrator
behavior.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core import charter
from core.charter import WorkOrder, Verdict


# ─────────────────────────── quiet_hours ───────────────────────────
class TestQuietHours:
    def _wo(self, *, kind="notify.user.nudge", priority=5):
        return WorkOrder(kind=kind, payload={}, requester="t", priority=priority)

    def test_2300_nudge_vetoed(self, sandbox_db):
        v = charter.review(self._wo(),
                           ctx={"now": datetime(2026, 5, 8, 23, 0)})
        assert not v.approved
        assert "quiet hours" in (v.reason or "").lower()

    def test_2229_nudge_passes(self, sandbox_db):
        # 22:29 is before the 22:30 cutoff — must still pass.
        v = charter.review(self._wo(),
                           ctx={"now": datetime(2026, 5, 8, 22, 29)})
        assert v.approved

    def test_2230_nudge_vetoed_boundary(self, sandbox_db):
        # 22:30 is the exact start of the quiet window.
        v = charter.review(self._wo(),
                           ctx={"now": datetime(2026, 5, 8, 22, 30)})
        assert not v.approved

    def test_0700_nudge_passes_boundary(self, sandbox_db):
        # 07:00 is the exact end — must pass.
        v = charter.review(self._wo(),
                           ctx={"now": datetime(2026, 5, 8, 7, 0)})
        assert v.approved

    def test_0659_nudge_vetoed_boundary(self, sandbox_db):
        # 06:59 is still inside the quiet window.
        v = charter.review(self._wo(),
                           ctx={"now": datetime(2026, 5, 8, 6, 59)})
        assert not v.approved

    def test_priority_1_bypasses(self, sandbox_db):
        v = charter.review(self._wo(priority=1),
                           ctx={"now": datetime(2026, 5, 8, 23, 30)})
        assert v.approved
        assert "urgent" in (v.reason or "").lower()

    def test_priority_2_bypasses(self, sandbox_db):
        v = charter.review(self._wo(priority=2),
                           ctx={"now": datetime(2026, 5, 8, 23, 30)})
        assert v.approved

    def test_priority_3_does_not_bypass(self, sandbox_db):
        v = charter.review(self._wo(priority=3),
                           ctx={"now": datetime(2026, 5, 8, 23, 30)})
        assert not v.approved

    def test_user_reply_never_vetoed(self, sandbox_db):
        """The 2026-05 outage regression — replies must pass at all
        hours. quiet_hours globs `notify.*.nudge`, NOT `notify.*`."""
        wo = WorkOrder(kind="notify.user.reply", payload={},
                       requester="t", priority=5)
        v = charter.review(wo, ctx={"now": datetime(2026, 5, 8, 23, 30)})
        assert v.approved


# ─────────────────────────── hrv_red_blocks ───────────────────────────
class TestHRVRedBlocks:
    def _wo(self, kind="coach.push_intensity"):
        return WorkOrder(kind=kind, payload={}, requester="t", priority=3)

    def test_hrv_below_30_vetoes_push(self, sandbox_db):
        v = charter.review(self._wo(), ctx={"hrv_today": 25})
        assert not v.approved
        assert "red" in (v.reason or "").lower()

    def test_hrv_at_30_passes(self, sandbox_db):
        v = charter.review(self._wo(), ctx={"hrv_today": 30})
        assert v.approved

    def test_no_hrv_in_ctx_passes(self, sandbox_db):
        v = charter.review(self._wo(), ctx={})
        assert v.approved

    def test_red_band_does_not_block_unrelated_kinds(self, sandbox_db):
        """`hrv_red_blocks` globs `coach.push_*`, not `notify.*`. A
        nudge at HRV 25 must still proceed (subject to quiet_hours)."""
        wo = WorkOrder(kind="notify.user.nudge", payload={},
                       requester="t", priority=5)
        v = charter.review(wo, ctx={"hrv_today": 25,
                                    "now": datetime(2026, 5, 8, 12, 0)})
        # quiet_hours doesn't fire at noon; hrv_red_blocks doesn't
        # match this kind. So the verdict is approved.
        assert v.approved


# ─────────────────────────── external_veto_check ───────────────────────────
class TestExternalVeto:
    def test_external_subject_vetoes_any_kind(self, sandbox_db):
        wo = WorkOrder(kind="notify.user.reply", payload={},
                       requester="t", priority=5)
        v = charter.review(wo, ctx={"external_veto_subject": "operator hold"})
        assert not v.approved
        assert "external veto" in (v.reason or "").lower()

    def test_no_subject_passes(self, sandbox_db):
        wo = WorkOrder(kind="notify.user.reply", payload={},
                       requester="t", priority=5)
        v = charter.review(wo, ctx={})
        assert v.approved


# ─────────────────────────── Crash containment ───────────────────────────
class TestPolicyCrash:
    """A misbehaving policy that throws must NOT crash the entire
    review pipeline. The contract (per core/charter.py:122-125) is:
    treat exceptions as a veto with a clear reason."""

    def test_exception_in_policy_is_veto_not_crash(self, sandbox_db, monkeypatch):
        @charter.policy("notify.test.crash", name="boom")
        def _boom(wo, ctx):
            raise RuntimeError("simulated policy bug")

        wo = WorkOrder(kind="notify.test.crash", payload={},
                       requester="t", priority=5)
        v = charter.review(wo, ctx={})
        assert not v.approved
        assert "crashed" in (v.reason or "").lower()

        # Cleanup — pop the test policy so it doesn't leak.
        charter._REGISTRY[:] = [
            p for p in charter._REGISTRY if p[0] != "boom"
        ]


# ─────────────────────────── governance_log persistence ───────────────────────────
class TestGovernanceLog:
    """Every review writes one row. This is the audit anchor — without
    it, after-the-fact debugging of "why did Miya not send?" is
    impossible."""

    def test_review_writes_governance_row(self, sandbox_db):
        from core import io as cio
        wo = WorkOrder(kind="notify.user.reply", payload={},
                       requester="t-actor", priority=5)
        charter.review(wo, ctx={})

        con = cio.db()
        try:
            row = con.execute(
                "SELECT actor, subject, decision FROM governance_log "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        actor, subject, decision = row
        assert actor == "t-actor"
        assert subject == "notify.user.reply"
        assert decision in ("approved", "modified", "vetoed")
