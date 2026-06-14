"""Cross-validation layer tests.

Catches LLM contradictions vs known facts: wrong 1RMs, pace flips,
goal mismatches. Designed to never raise.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")


@dataclass
class _FakeProfile:
    one_rep_maxes_kg: dict
    active_goal_target_lbs: float | None = None
    limitations: list = None
    recovery_tier: str | None = None


# ─── 1RM detection ─────────────────────────────────────────────────────

class Test1RMDetection:

    def test_detects_wrong_deadlift_in_lbs(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        text = "Your deadlift is 405 lbs."
        issues = validate(text, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_detects_wrong_deadlift_in_kg(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        text = "Your deadlift is 200 kg."
        issues = validate(text, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_correct_1rm_within_tolerance_passes(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        # 341 lbs ≈ 155 kg; should NOT flag
        text = "Your deadlift is 341 lbs."
        issues = validate(text, profile=p)
        assert not any(i.kind == "1rm" for i in issues)

    def test_alias_back_squat_detected(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={"back_squat": 102.0})
        text = "Back squat at 315 lbs is doable."
        issues = validate(text, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_no_1rms_in_profile_no_flag(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={})
        text = "Your deadlift is 9999 lbs."
        issues = validate(text, profile=p)
        assert not issues


# ─── Pace detection ────────────────────────────────────────────────────

class TestPaceDetection:

    def test_behind_pace_arbitration_flags_ahead_claim(self):
        from new_plane.miya_runner.validator import validate
        text = "You're ahead of plan this week. Keep it up."
        issues = validate(text, arbitration={"rule": "behind_pace"})
        assert any(i.kind == "pace" for i in issues)

    def test_behind_pace_arbitration_no_flag_when_text_agrees(self):
        from new_plane.miya_runner.validator import validate
        text = "You're behind pace by 500 kcal — let's plan a session."
        issues = validate(text, arbitration={"rule": "behind_pace"})
        assert not any(i.kind == "pace" for i in issues)

    def test_no_arbitration_no_pace_flag(self):
        from new_plane.miya_runner.validator import validate
        text = "You're ahead. You're behind. Whatever."
        issues = validate(text, arbitration=None)
        assert not any(i.kind == "pace" for i in issues)


# ─── Goal target detection ─────────────────────────────────────────────

class TestGoalTargetDetection:

    def test_flags_wrong_target_lbs(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, active_goal_target_lbs=196)
        text = "Your target of 180 lbs is achievable."
        issues = validate(text, profile=p)
        assert any(i.kind == "goal_target" for i in issues)

    def test_correct_target_does_not_flag(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, active_goal_target_lbs=196)
        text = "Your target of 196 lbs is on track."
        issues = validate(text, profile=p)
        assert not any(i.kind == "goal_target" for i in issues)

    def test_within_tolerance_does_not_flag(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, active_goal_target_lbs=196)
        text = "Your target of 197 lbs is on track."
        issues = validate(text, profile=p)
        assert not any(i.kind == "goal_target" for i in issues)


# ─── Rest target hallucination ────────────────────────────────────────

class TestRestTargetDetection:

    def test_flags_active_rest_ideal_zero(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, recovery_tier="hammer")
        text = "Mon: Active rest → ideal 0 kcal — burned 429 kcal"
        issues = validate(text, profile=p)
        assert any(i.kind == "rest_target" for i in issues)

    def test_flags_rest_at_zero(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, recovery_tier="performance")
        text = "rest @ 0 kcal"
        issues = validate(text, profile=p)
        assert any(i.kind == "rest_target" for i in issues)

    def test_correct_rest_target_passes(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, recovery_tier="hammer")
        text = "Mon: Active rest → ideal 600 kcal"
        issues = validate(text, profile=p)
        assert not any(i.kind == "rest_target" for i in issues)

    def test_no_tier_no_flag(self):
        from new_plane.miya_runner.validator import validate
        p = _FakeProfile(one_rep_maxes_kg={}, recovery_tier=None)
        text = "Active rest → ideal 0 kcal"
        issues = validate(text, profile=p)
        assert not any(i.kind == "rest_target" for i in issues)


# ─── enforce() rewriting ───────────────────────────────────────────────

class TestEnforce:

    def test_rewrites_single_wrong_1rm(self):
        from new_plane.miya_runner.validator import validate, enforce
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        text = "Your deadlift is 405 lbs."
        issues = validate(text, profile=p)
        corrected = enforce(text, issues)
        assert "155" in corrected or "342" in corrected
        assert "405 lbs" not in corrected

    def test_pace_correction_prepends(self):
        from new_plane.miya_runner.validator import validate, enforce
        text = "You're ahead of plan this week."
        issues = validate(text, arbitration={"rule": "behind_pace"})
        corrected = enforce(text, issues)
        assert "Correction" in corrected
        assert "behind pace" in corrected.lower()

    def test_no_issues_no_change(self):
        from new_plane.miya_runner.validator import enforce
        text = "Hi, you're doing great."
        corrected = enforce(text, [])
        assert corrected == text

    def test_multiple_occurrences_skip_rewrite(self):
        """If the quoted substring appears more than once, don't rewrite —
        we can't be sure which instance is wrong."""
        from new_plane.miya_runner.validator import enforce, Contradiction
        text = "405 lbs everywhere. 405 lbs all over."
        issues = [Contradiction(
            kind="1rm",
            detail="x",
            quoted="405 lbs",
            expected="342 lbs",
        )]
        corrected = enforce(text, issues)
        assert corrected == text  # untouched


# ─── validate_and_enforce convenience ──────────────────────────────────

class TestValidateAndEnforce:

    def test_returns_corrected_text_and_issues(self):
        from new_plane.miya_runner.validator import validate_and_enforce
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        text = "Your deadlift is 405 lbs."
        corrected, issues = validate_and_enforce(text, profile=p)
        assert issues
        assert "405 lbs" not in corrected

    def test_returns_unchanged_when_clean(self):
        from new_plane.miya_runner.validator import validate_and_enforce
        p = _FakeProfile(one_rep_maxes_kg={"deadlift": 155.0})
        text = "Hello."
        corrected, issues = validate_and_enforce(text, profile=p)
        assert corrected == text
        assert not issues


# ─── Safety: never raises ──────────────────────────────────────────────

class TestSafety:

    def test_empty_text_returns_empty(self):
        from new_plane.miya_runner.validator import validate
        assert validate("") == []

    def test_none_profile_returns_empty(self):
        from new_plane.miya_runner.validator import validate
        assert validate("hi", profile=None) == []

    def test_bad_profile_does_not_crash(self):
        from new_plane.miya_runner.validator import validate
        class BadProfile:
            @property
            def one_rep_maxes_kg(self):
                raise RuntimeError("kaboom")
        # Should NOT raise
        out = validate("hi", profile=BadProfile())
        assert isinstance(out, list)
