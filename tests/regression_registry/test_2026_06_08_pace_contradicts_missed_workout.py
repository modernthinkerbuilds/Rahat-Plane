"""Pin: 2026-06-08 (Bug H) — bot called the user "ahead of pace" while
simultaneously listing a missed workout.

SYMPTOM (production, new-plane RahatBadeMiya v2):
    > where am I on pace
    Bot: Hau ustad. You're on a tear this week.
         Ahead of pace — comfortable buffer.
         Burned 3,424 / 6,000 — comfortable buffer.

         Missed: Mon CrossFit.
    Two contradictory facts in one message: a missed Monday CrossFit
    should have put the user BEHIND prorated pace, not ahead.

ROOT CAUSE:
    `get_recalibration()` returned a STRUCTURED field `behind_pace=True`
    but a TEXT `summary` that read "Ahead of pace — comfortable buffer."
    The arbitration layer correctly detected the contradiction (verdict
    `behind_pace`), but the synth prompt didn't surface the verdict
    strongly enough; Gemini Flash paraphrased the misleading `summary`.

FIX:
    - Arbitration verdict promoted to a leading INSTRUCTION block in the
      synth prompt; cost router escalates to Pro on arbitration-fire.

THIS PIN ASSERTS (deterministic, offline):
    1. `arbitrate(facts)` sides with the STRUCTURED field — when
       `recalibration.behind_pace is True` the verdict is `behind_pace`,
       regardless of a contradicting `summary` string.
    2. The orchestrator propagates that verdict to
       `Response.arbitration_rule`.

    NOTE — the user-visible "response text must not say 'ahead of pace'"
    guarantee is a LIVE-MODEL property: offline the structured fallback
    echoes the summary verbatim. That residual is pinned as an xfail in
    tests/evals/test_synthesizer_grounding.py (PF-2026-06-10-004). Here
    we pin the deterministic half that the suite originally missed.
"""
from __future__ import annotations

from new_plane.miya_runner import native_client as nc
from new_plane.miya_runner.orchestrator import Turn, handle
from new_plane.miya_sim.orchestrator import arbitrate


# The exact production fact shape: structured field says behind, text lies.
_BUG_H_RECAL = {
    "behind_pace": True,
    "summary": "Ahead of pace. Burned 3,424 / 6,000 — comfortable buffer.",
}


def test_arbitration_sides_with_structured_field_not_summary():
    facts = {
        "active_goal": {"result": {"active": False}},
        "recalibration": {"result": _BUG_H_RECAL},
    }
    verdict = arbitrate(facts)
    assert verdict is not None, "arbitration must fire on behind_pace=True"
    assert verdict["rule"] == "behind_pace", (
        f"verdict={verdict!r}; the misleading 'Ahead of pace' summary must "
        f"NOT override the structured behind_pace field")


def test_orchestrator_surfaces_behind_pace_verdict(monkeypatch):
    monkeypatch.setattr(nc, "kobe_active_goal",
                        lambda trace_id=None: nc._ok(trace_id or "t",
                                                     {"active": False}))
    monkeypatch.setattr(nc, "kobe_recalibration",
                        lambda trace_id=None: nc._ok(trace_id or "t",
                                                     dict(_BUG_H_RECAL)))
    resp = handle(Turn(user_message="where am I on pace", chat_id="c-bugH"))
    assert resp.arbitration_rule == "behind_pace", (
        f"arbitration_rule={resp.arbitration_rule!r}; Bug H re-emerges if the "
        f"orchestrator drops the behind_pace verdict")
