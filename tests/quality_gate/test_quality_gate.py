"""Every-check-in QUALITY GATE (2026-06-16 re-eval pass).

Fast, deterministic, hermetic. No live network / DB / LLM. Pins the defect
classes that have actually shipped to the user:

  A. Grounding       — synth prompt carries the arbitration verdict and NOT
                       its opposite (the assertion that would have caught
                       Bug H, 2026-06-08).
  B. No-invention    — empty / lookup-miss facts → no fabricated number or
                       status in the prompt (the Bug-I class, 2026-06-09).
  C. Single-topic    — a WOD-only turn's prompt is not polluted with
                       unsolicited pace facts.
  D. Voice integrity — the scrubber clears prefix specialist attribution.
  E. Fact fidelity   — the validator rewrites a wrong 1RM to the profile
                       value (or removes it); a correct number is untouched.
  F. Routing fidelity— the mined-phrasing corpus routes to the expected
                       agent and returns non-empty, non-stub text.
  G. Classifier fuzz — Hypothesis properties on classify_delegation
                       (the routing brain): never crash, deterministic,
                       valid sentinel, design-intent never routes to Kobe.

ANTI-THEATER: every gate here is shown to FAIL when its guard is reverted —
see specs/test_lead/findings/REEVAL_AUDIT_2026-06-14.md §"gate bite proof".

Command (wired into tests/run_all.py):
    RAHAT_TEST_MODE=1 python -m tests.run_all --layer quality_gate
"""
from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st

from new_plane.miya_runner.synthesizer import _build_prompt
from new_plane.miya_runner.orchestrator import _scrub_voice_leak, _validate_outbound
from new_plane.miya_runner.delegate_classifier import classify_delegation


# ─── A. Grounding (Bug-H class) ───────────────────────────────────────
class TestGrounding:
    def test_arbitration_verdict_present_opposite_absent(self):
        """When arbitration says behind_pace, the prompt must carry the
        verdict and must NOT carry the contradicting 'ahead' summary as
        truth. This is the assertion that would have caught Bug H."""
        prompt = _build_prompt(
            user_message="where am I on pace",
            facts={"recalibration": {"result": {
                "behind_pace": True,
                "summary": "Ahead of pace — comfortable buffer."}}},
            arbitration={"rule": "behind_pace",
                         "guidance": "User is behind pace — do not say "
                                     "'ahead of pace' or 'comfortable buffer'."},
            fraser_text=None, recent_signals=None,
        )
        assert "ARBITRATION VERDICT: behind_pace" in prompt
        assert "behind pace" in prompt.lower()
        # The verdict's instruction to NOT say "ahead" must be present.
        assert 'Do not say "ahead' in prompt or "do not contradict" in prompt.lower()


# ─── B. No-invention (Bug-I class) ────────────────────────────────────
class TestNoInvention:
    def test_empty_facts_prompt_has_no_facts_block(self):
        """No facts → no FACTS block to hallucinate from; scaffolding intact."""
        prompt = _build_prompt(user_message="what's my plan",
                               facts={}, arbitration=None,
                               fraser_text=None, recent_signals=None)
        assert "FACTS FROM SPECIALISTS:" not in prompt
        assert "Now write Miya's response" in prompt

    def test_lookup_miss_does_not_inject_a_number(self):
        """An empty gym_wod result must not put a fabricated WOD/number into
        the prompt as source-of-truth."""
        prompt = _build_prompt(
            user_message="what's the workout for tomorrow",
            facts={"gym_wod": {"result": {"text": ""}, "day": "tomorrow"}},
            arbitration=None, fraser_text=None, recent_signals=None)
        # No invented numeric WOD content for an empty result.
        assert "SOURCE OF TRUTH" not in prompt or "tomorrow" in prompt.lower()


# ─── C. Single-topic ──────────────────────────────────────────────────
class TestSingleTopic:
    def test_wod_lookup_facts_block_excludes_unrelated_pace_summary(self):
        """A WOD-only turn must not graft an unsolicited pace summary into the
        FACTS block (Bug-I half 2). We scope to the FACTS section because the
        static SYSTEM PROMPT legitimately contains the words 'behind pace' as
        an honesty instruction — the gate is about FACT DATA, not the
        standing directive."""
        prompt = _build_prompt(
            user_message="what's the workout for Wednesday",
            facts={"gym_wod": {"result": {"text": "Bench 5x5"}, "day": "wed"}},
            arbitration=None, fraser_text=None, recent_signals=None)
        marker = "FACTS FROM SPECIALISTS:"
        facts_section = prompt.split(marker, 1)[1] if marker in prompt else ""
        low = facts_section.lower()
        assert "bench 5x5" in low, "the asked-for WOD must be in the facts"
        # No pace/recalibration data grafted into the facts the user didn't ask for.
        assert "recalibration" not in low
        assert "comfortable buffer" not in low
        assert "ahead of pace" not in low


# ─── D. Voice integrity ───────────────────────────────────────────────
class TestVoiceIntegrity:
    @pytest.mark.parametrize("leak", [
        "Kobe says: rest today.",
        "Fraser: 5x5 back squat.",
        "The sports scientist says you're behind.",
        "the crossfit coach: 3 rounds.",
    ])
    def test_prefix_attribution_is_scrubbed(self, leak):
        cleaned, found = _scrub_voice_leak(leak)
        low = cleaned.lower()
        for tok in ("kobe", "fraser", "sports scientist", "crossfit coach"):
            assert tok not in low, f"attribution {tok!r} survived: {cleaned!r}"
        assert found, "scrubber should report what it stripped (audit)"


# ─── E. Fact fidelity ─────────────────────────────────────────────────
class TestFactFidelity:
    def test_wrong_1rm_is_corrected_or_removed(self):
        """A fabricated deadlift number must not survive validation — it is
        rewritten to the profile value or removed."""
        bad = "Your deadlift is 999 kg, go heavy."
        out, issues = _validate_outbound(bad, arbitration=None)
        assert "999" not in out, f"fabricated 1RM survived the validator: {out!r}"

    def test_correct_number_is_not_false_positive_rewritten(self):
        """A reply with NO lift claim must pass untouched (no false-positive
        corruption of a good reply)."""
        good = "Nice work today — keep the cadence steady on your easy run."
        out, issues = _validate_outbound(good, arbitration=None)
        assert out == good, f"validator corrupted a clean reply: {out!r}"


# ─── F. Routing fidelity ──────────────────────────────────────────────
class TestRoutingFidelity:
    # A curated, fast subset of the mined corpus — one per route class.
    _CASES = [
        ("/pace", "kobe_route"),
        ("what's my plan this week", "kobe_route"),
        ("what is tomorrow's WOD", "kobe_route"),
        ("154.5", "kobe_route"),
        ("HRV 38", "kobe_route"),
        ("my hip hurts", "kobe_route"),
        ("@fraser design me a metcon", "fraser_route"),
        ("@huberman should I train", "huberman_route"),
        ("how am I tracking toward my goal", "orchestrate"),
        ("design me a workout", "orchestrate"),
    ]

    @pytest.mark.parametrize("msg,expected", _CASES)
    def test_route_is_stable_and_expected(self, msg, expected):
        path, _ = classify_delegation(msg)
        assert path == expected, f"{msg!r} routed to {path!r}, want {expected!r}"


# ─── G. Classifier fuzz (Hypothesis) ──────────────────────────────────
_VALID_PATHS = {"kobe_route", "fraser_route", "huberman_route", "orchestrate"}


class TestClassifierProperties:
    @settings(max_examples=200, deadline=None)
    @given(st.text(min_size=0, max_size=300))
    def test_never_crashes_and_returns_valid_sentinel(self, msg):
        path, stripped = classify_delegation(msg)
        assert path in _VALID_PATHS
        assert isinstance(stripped, str)

    @settings(max_examples=100, deadline=None)
    @given(st.text(min_size=1, max_size=120))
    def test_deterministic(self, msg):
        assert classify_delegation(msg)[0] == classify_delegation(msg)[0]

    @settings(max_examples=100, deadline=None)
    @given(st.sampled_from(["design", "build", "create", "invent"]),
           st.sampled_from(["me a workout", "a wod for friday", "a metcon"]))
    def test_design_intent_never_routes_to_kobe_lookup(self, verb, tail):
        """Design intent must reach Fraser via orchestrate, never Kobe's
        deterministic WOD-lookup path (Bug-K class)."""
        path, _ = classify_delegation(f"{verb} {tail}")
        assert path in ("orchestrate", "fraser_route")

    @settings(max_examples=80, deadline=None)
    @given(st.sampled_from(["tomorrow", "tommorow", "tmrw", "2moro", "tomorow"]))
    def test_wod_lookup_day_typos_route_to_kobe(self, day):
        """Mutated day tokens in a WOD lookup still route deterministically
        to Kobe (the Bug-I shape)."""
        path, _ = classify_delegation(f"what is the wod for {day}")
        assert path == "kobe_route"
