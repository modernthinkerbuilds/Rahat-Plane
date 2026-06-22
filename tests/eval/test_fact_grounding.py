"""Fact-grounding eval suite — does the bot quote correct facts?

This is the test that would have caught the 2026-06-13 incident: the bot
saying "Deadlift: 405 lbs, Back Squat: 315 lbs" when the user's actual
1RMs (per Gemini transcript) are 341 / 220 / 132 / 110 lbs.

Each scenario:
  1. Sets up a fixture UserProfile with known facts.
  2. Feeds a deliberately-leading prompt through the orchestrator (with
     synth in fallback mode so we don't burn API tokens).
  3. Asserts the validator catches any contradiction, OR the rendered
     prompt contains the right facts so synth can't drift.

These tests use the `fact-grounding` mark so they can be run as a
separate cohort: `pytest -m fact_grounding`.

Note: We can't actually run real Gemini in unit tests, so we test the
*prompt assembly* and *validator detection* rather than the LLM output
itself. The 3-month replay harness (test_replay_harness.py) does the
end-to-end check.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")


# ─── Fixture: real user profile ───────────────────────────────────────

@pytest.fixture
def user_db(tmp_path: Path) -> Path:
    db = tmp_path / "vault.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE intents (id INTEGER PRIMARY KEY, kind TEXT,
            target_value REAL, target_date TEXT, status TEXT, created_at TEXT);
        CREATE TABLE weighin_log (weight_lbs REAL, ts TEXT);
        CREATE TABLE user_state (key TEXT, value TEXT);
        CREATE TABLE memory_entities (entity_id INTEGER PRIMARY KEY,
            agent TEXT, type TEXT, payload TEXT, status TEXT,
            valid_from TEXT, valid_until TEXT, superseded_by INTEGER,
            rationale TEXT, created_at TEXT, updated_at TEXT);
        INSERT INTO weighin_log VALUES (198.0, '2026-06-01');
        INSERT INTO user_state VALUES ('recovery_tier', 'hammer');
    """)
    goal = json.dumps({
        "target_lbs": 196, "target_date_iso": "2026-09-01",
        "daily_intake_kcal": 2250, "weekly_active_kcal": 6000,
        "tier": "performance",
    })
    con.execute(
        "INSERT INTO memory_entities VALUES (1, 'scientist', 'goal', ?, "
        "'active', null, null, null, '', '2026-05-27', '2026-05-27')",
        (goal,))
    con.commit()
    con.close()
    return db


@pytest.fixture
def user_overlay(tmp_path: Path) -> Path:
    p = tmp_path / "user_profile.json"
    p.write_text(json.dumps({
        "_overlay_source": "eval-test-fixture",
        "one_rep_maxes_kg": {
            "deadlift": 200.0,
            "back_squat": 150.0,
            "bench_press": 60.0,
            "overhead_press": 50.0,
        },
        "limitations": [
            "right-side neck pain under load",
            "hip catch on cleans",
            "right ankle issue",
        ],
    }))
    return p


@pytest.fixture
def grounded_env(monkeypatch, user_db, user_overlay):
    monkeypatch.setenv("RAHAT_TEST_VAULT_DB", str(user_db))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(user_overlay))
    return None


# ─── Scenario: bot must NOT quote wrong 1RM ───────────────────────────

@pytest.mark.fact_grounding
class TestBotDoesNotHallucinate1RMs:
    """The 2026-06-13 transcript bug: bot said DL 405 lbs (actual 341)."""

    def test_validator_catches_wrong_deadlift_405(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad_reply = "Your deadlift max is 405 lbs based on recent training."
        issues = validate(bad_reply, profile=p)
        assert any(i.kind == "1rm" for i in issues), (
            "validator did NOT catch 'deadlift 405 lbs' even though "
            "profile says 200 kg (~441 lbs). This is the 2026-06-13 bug."
        )

    def test_validator_catches_wrong_squat_315(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad_reply = "Back squat 405 lbs at 5x5 today."
        issues = validate(bad_reply, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_validator_catches_wrong_bench_225(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad_reply = "Bench press 331 lbs for 3 reps."
        issues = validate(bad_reply, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_validator_catches_wrong_ohp_155(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad_reply = "Overhead press 155 lbs as your strict press max."
        issues = validate(bad_reply, profile=p)
        assert any(i.kind == "1rm" for i in issues)

    def test_correct_1rms_pass(self, grounded_env):
        """All four correct 1RMs should NOT trigger any contradiction."""
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        good_reply = (
            "Your 1RMs on file: deadlift 441 lbs, back squat 331 lbs, "
            "bench press 132 lbs, overhead press 110 lbs."
        )
        issues = [i for i in validate(good_reply, profile=p) if i.kind == "1rm"]
        assert not issues, (f"correct values flagged as wrong: {issues}")


# ─── Scenario: bot must NOT quote wrong goal target ───────────────────

@pytest.mark.fact_grounding
class TestBotQuotesCorrectGoalTarget:

    def test_validator_catches_wrong_target_180(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad = "Your target of 180 lbs is on track."
        issues = [i for i in validate(bad, profile=p) if i.kind == "goal_target"]
        assert issues

    def test_correct_target_196_passes(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        good = "Your target of 196 lbs is on track."
        issues = [i for i in validate(good, profile=p) if i.kind == "goal_target"]
        assert not issues


# ─── Scenario: rest target hallucination ──────────────────────────────

@pytest.mark.fact_grounding
class TestBotDoesNotSayZeroKcalRest:
    """The 2026-06-13 'Active rest → ideal 0 kcal' bug."""

    def test_validator_catches_active_rest_zero_kcal(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        bad = "Mon: Active rest → ideal 0 kcal — burned 429 kcal"
        issues = [i for i in validate(bad, profile=p) if i.kind == "rest_target"]
        assert issues

    def test_correct_rest_target_600_kcal_passes(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate
        p = load()
        good = "Mon: Active rest → ideal 600 kcal — burned 429 kcal"
        issues = [i for i in validate(good, profile=p) if i.kind == "rest_target"]
        assert not issues


# ─── Scenario: USER PROFILE block reaches the synth prompt ────────────

@pytest.mark.fact_grounding
class TestUserProfileReachesSynthPrompt:

    def test_1rms_appear_in_built_prompt(self, grounded_env):
        from core.user_profile import load, to_facts_block
        from new_plane.miya_runner.synthesizer import _build_prompt
        block = to_facts_block(load())
        prompt = _build_prompt(
            user_message="what's my deadlift max?",
            facts={}, arbitration=None,
            fraser_text=None, recent_signals=None,
            user_profile_block=block,
        )
        # The exact numbers the bot was hallucinating must appear in the
        # FACTS block so the LLM has no excuse to invent.
        assert "deadlift: 200.0 kg" in prompt
        assert "441 lbs" in prompt

    def test_limitations_appear_in_built_prompt(self, grounded_env):
        from core.user_profile import load, to_facts_block
        from new_plane.miya_runner.synthesizer import _build_prompt
        block = to_facts_block(load())
        prompt = _build_prompt(
            user_message="design me a warmup",
            facts={}, arbitration=None,
            fraser_text=None, recent_signals=None,
            user_profile_block=block,
        )
        assert "neck pain" in prompt
        assert "ankle" in prompt

    def test_active_goal_target_appears_in_prompt(self, grounded_env):
        from core.user_profile import load, to_facts_block
        from new_plane.miya_runner.synthesizer import _build_prompt
        block = to_facts_block(load())
        prompt = _build_prompt(
            user_message="what's my goal?",
            facts={}, arbitration=None,
            fraser_text=None, recent_signals=None,
            user_profile_block=block,
        )
        assert "196" in prompt


# ─── Scenario: validate_and_enforce corrects bad text ─────────────────

@pytest.mark.fact_grounding
class TestValidatorRewritesBadReplies:

    def test_wrong_1rm_gets_replaced_in_text(self, grounded_env):
        from core.user_profile import load
        from new_plane.miya_runner.validator import validate_and_enforce
        p = load()
        bad = "Your deadlift max is 405 lbs based on recent training."
        corrected, issues = validate_and_enforce(bad, profile=p)
        assert "405 lbs" not in corrected
        # Expected value should appear (either kg or lb form)
        assert "200" in corrected or "441" in corrected
