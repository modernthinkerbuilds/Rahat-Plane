"""Replay the live Telegram bug-history transcripts.

Source: `specs/test_lead/TELEGRAM_BUG_HISTORY.md`. Each entry there is a
production bug the user observed; this harness turns every entry into an
executable contract so the bot's real history can never silently regress.

Design (deterministic + hermetic):
  • Routing contracts (`must_route_to`) are asserted on
    `classify_delegation` — the deterministic routing brain. For the
    paraphrase bugs (H/I), routing AWAY from `orchestrate` is the actual
    fix mechanism: a kobe_route turn never reaches the synth layer, so it
    *cannot* emit the hallucinated phrase. Pinning the route therefore
    pins the user-visible guarantee without depending on the LLM.
  • Arbitration contracts (`must_arbitrate`) are asserted on the pure
    `arbitrate(facts)` function.
  • Forbidden-text / forbidden-tool contracts run `handle()` end-to-end
    and assert the broken output never appears. These hold regardless of
    Kobe's deterministic text because the forbidden strings are synth
    hallucinations that a delegated route never produces.

The one place reality diverges from the doc: Bug H's "must_not_contain
'ahead of pace'" is a LIVE-MODEL property — offline the structured
fallback echoes the recalibration summary verbatim. That gap is pinned
separately as an xfail in tests/evals/test_synthesizer_grounding.py
(PF-2026-06-10-004). Here we pin the deterministic half: arbitration
fires `behind_pace`.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
from new_plane.miya_runner.orchestrator import Turn, handle
from new_plane.miya_sim.orchestrator import arbitrate


# ── Routing contracts — one or more messages that must share a route ──
ROUTING_CASES = [
    # Bug I (2026-06-09) — WOD lookup must be deterministic, not synth.
    ("bug-I-tommorow-wod-typo", ["What is tommorows WOD"], "kobe_route"),
    ("bug-I-canonical-tomorrows-wod", ["what is tomorrow's WOD"], "kobe_route"),
    ("bug-I-plan-still-works", ["/plan"], "kobe_route"),
    # Bug K (2026-05-16) — WOD lookup routes to Kobe, not Fraser-design.
    ("bug-K-wod-lookup-not-fraser", ["what is the WOD"], "kobe_route"),
    # Bug L (2026-05-17) — /plan slash routes to Kobe.
    ("bug-L-show-plan-slash", ["/plan"], "kobe_route"),
    # Bug M (2026-05-17) — every slash command routes to Kobe.
    ("bug-M-slash-always-kobe",
     ["/pace", "/plan", "/today", "/week", "/next", "/help",
      "/fix Mon 800", "/pain neck mild", "/profile"], "kobe_route"),
    # Bug N (2026-05-23) — NL plan mutation.
    ("bug-N-tolerate-plan-mutation", ["tolerate partner"], "kobe_route"),
    # Bug O (2026-05-23) — NL pain capture.
    ("bug-O-pain-NL-captured", ["my hip hurts"], "kobe_route"),
    # Bug P — bare-number weight log.
    ("bug-P-bare-number-weight-log", ["154", "154.5", "154 kg"], "kobe_route"),
    # Bug Q — HRV log.
    ("bug-Q-hrv-log", ["HRV 38", "hrv: 42", "my HRV is 50"], "kobe_route"),
    # Bug R — burn log.
    ("bug-R-burn-log", ["burned 800 cal", "crossfit 900 cal", "z2 600 kcal"],
     "kobe_route"),
    # Bug S — recovery protocols.
    ("bug-S-recovery-protocols",
     ["7/15 breathing", "seven fifteen breathing", "box breathing",
      "pre-fuel", "post-recovery routine"], "kobe_route"),
    # Bug J (history) — bare "Yes" has no agent affinity → synth path
    # (orchestrate). The new plane must NOT reject it the way old Miya did.
    ("bug-J-yes-followup", ["Yes"], "orchestrate"),
]


@pytest.mark.parametrize("case", ROUTING_CASES, ids=lambda c: c[0])
def test_history_routing_contract(case):
    cid, messages, expected = case
    for msg in messages:
        path, _ = classify_delegation(msg)
        assert path == expected, (
            f"{cid}: {msg!r} routed to {path!r}, expected {expected!r} — "
            f"this history bug would re-emerge")


# ── Bug H (2026-06-08) — arbitration must fire behind_pace ────────────
def test_bug_H_arbitration_fires_behind_pace():
    """The recalibration's structured field said behind; only the text
    summary said 'Ahead'. Arbitration must side with the structured
    field. (Text-level suppression is PF-2026-06-10-004, xfail in the
    synth-grounding evals.)"""
    facts = {
        "active_goal": {"active": False},
        "recalibration": {
            "behind_pace": True,
            "summary": "Ahead of pace. Burned 3,424 / 6,000 — comfortable buffer.",
        },
    }
    verdict = arbitrate(facts)
    assert verdict is not None and verdict["rule"] == "behind_pace"


# ── Behavior contracts via handle() — forbidden output never appears ──
def test_bug_J_yes_does_not_emit_old_router_fallback():
    """Old Miya answered a bare 'Yes' with 'I'm not sure how to route
    that'. The new plane must never produce that generic rejection."""
    resp = handle(Turn(user_message="Yes", chat_id="c-replay-J"))
    low = resp.text.lower()
    assert "i'm not sure how to route that" not in low
    assert "not sure how to route" not in low


def test_bug_K_wod_lookup_does_not_use_fraser_design():
    """'what is the WOD' is a lookup — it must not invoke Fraser's design
    tool (which would hallucinate a workout, the 2026-05-16 bug)."""
    resp = handle(Turn(user_message="what is the WOD", chat_id="c-replay-K"))
    assert "fraser_design_session" not in resp.used_tools
    assert resp.routing.get("path") == "kobe_route"


def test_bug_I_wod_lookup_response_has_no_synth_hallucination():
    """A WOD-lookup turn is delegated to Kobe; because it never reaches
    the synth layer, the Bug-I hallucinations ('hasn't been synced',
    'ahead of plan', 'solid buffer') cannot appear in the reply."""
    for msg in ["What is tommorows WOD", "what is tomorrow's WOD"]:
        resp = handle(Turn(user_message=msg, chat_id="c-replay-I"))
        low = resp.text.lower()
        assert "hasn't been synced" not in low
        assert "ahead of plan" not in low
        assert "solid buffer" not in low
        assert resp.routing.get("path") == "kobe_route"
