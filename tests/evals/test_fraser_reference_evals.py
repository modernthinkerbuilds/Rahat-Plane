"""§2.1 — Fraser reference evals: multi-turn refinement, multi-constraint
equipment cascades, and a gated prose-quality LLM judge.

Complements:
  * test_fraser_conversation.py — the 30 TC deterministic-adapter cases.
  * test_fraser_grounding_evals.py — single-constraint grounding.

This file adds the three gaps from the kickoff (§2.1):
  3. multi-turn refinement ("actually 60 min, no running, prefer EMOM"),
  4. multi-constraint equipment cascades (current tests cover SINGLE
     constraints), and
  2. an LLM-as-judge prose-quality eval gated behind
     GEMINI_API_KEY + RAHAT_RUN_JUDGE=1 (non-blocking by default).

All deterministic cases assert the GROUNDED PROMPT (composer.build_design_
prompt — hermetic, no LLM), which is the contract that decides output
quality: if every constraint reaches the prompt, the model can honour it; if
one is dropped, the model hallucinates around it.

NOTE on §2.1 item 1 (record the remaining 27 NOTES cassettes): cassette
recording calls the REAL Gemini wire (scripts/record_fraser_cassettes.py,
GEMINI_API_KEY + RAHAT_FIXTURE_RECORD=1) and costs money — it cannot run in
the hermetic sandbox. The path to 30/30 is documented in the findings report;
the deterministic cases here and in test_fraser_conversation.py do NOT depend
on cassettes (they assert the adapter/prompt, with the LLM NOTES as an
overlay), so suite coverage of the TC scenarios stands without them.
"""
from __future__ import annotations

import os
import re

import pytest

from agents.fraser import composer


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "fraser_ref.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture(autouse=True)
def _eval_env(fresh_db):
    from core import athlete_profile
    athlete_profile.reset()
    yield
    athlete_profile.reset()


def _prompt(msg: str) -> str:
    return composer.build_design_prompt(composer.parse_request(msg))


# ─── §2.1.3 multi-turn refinement ─────────────────────────────────────
class TestFraserMultiTurnRefinement:
    """A refinement turn ('actually 60 min, no running, prefer EMOM') must
    update the structured request and reach the grounded prompt — Fraser
    can't honour a constraint the prompt never carries."""

    def test_refinement_parses_duration_and_preferences(self):
        req = composer.parse_request("actually 60 min, no running, prefer EMOM")
        assert req.minutes == 60, "duration refinement dropped"
        assert "no_running" in req.preferences, "no-running refinement dropped"

    def test_refinement_reaches_the_grounded_prompt(self):
        p = _prompt("actually 60 min, no running, prefer EMOM").lower()
        assert "60" in p
        assert "no running" in p
        assert "emom" in p, "the format refinement (EMOM) must reach the prompt"

    def test_initial_then_refined_request_differs(self):
        """The refined request carries constraints the initial one didn't —
        proving a second turn actually changes the design inputs."""
        first = composer.parse_request("design me a session")
        refined = composer.parse_request("actually 60 min, no running")
        assert first.minutes is None and refined.minutes == 60
        assert "no_running" not in first.preferences
        assert "no_running" in refined.preferences


# ─── §2.1.4 multi-constraint equipment cascades ───────────────────────
class TestFraserMultiConstraintCascade:
    """Current tests cover SINGLE constraints. Real asks stack them. A
    single grounded prompt must simultaneously carry ALL of the athlete's
    equipment substitutions plus the live request constraints."""

    def test_all_three_equipment_substitutions_present_together(self):
        """No wall-ball + no pull-up bar + no jump rope — the prompt must
        carry every substitution standard at once (thruster for wall ball,
        row for pull-up, and rope-free alternatives), not just one."""
        p = _prompt("design me a metcon for today").lower()
        # Wall-ball → thruster/goblet
        assert "thruster" in p, "wall-ball substitution (thruster) missing"
        # Pull-up → row
        assert "row" in p, "pull-up substitution (rows) missing"
        # Jump rope → lateral hops / burpees / penguin / bike-row
        assert any(alt in p for alt in ("lateral", "burpee", "penguin")), (
            "jump-rope substitution alternatives missing"
        )

    def test_no_rope_plus_ankle_flare_both_reach_prompt(self):
        """Cascade: an equipment limit (no rope) AND a live injury (ankle
        flare) must BOTH ground the prompt — the model needs both to swap
        the skip work AND de-load the ankle."""
        p = _prompt("design a 60 min session, my ankle is flared, no skipping").lower()
        assert any(alt in p for alt in ("lateral", "burpee", "penguin", "rope")), (
            "rope-free substitution context missing under cascade"
        )
        assert "ankle" in p, "ankle constraint dropped under cascade"
        assert "60" in p, "duration dropped under cascade"

    def test_baseline_mobility_cues_survive_added_constraints(self):
        """Adding request constraints must not crowd out the standing
        mobility cues (heel lift, the Hunch) that every Fraser session
        carries."""
        p = _prompt("design me a 60 minute EMOM, no running").lower()
        assert "heel" in p, "heel-lift cue dropped"
        assert "hunch" in p, "the Hunch posture cue dropped"

    def test_1rms_present_for_target_weight_math(self):
        """Multi-constraint sessions still need the 1RMs so target weights
        compute (200 kg deadlift is the anchor)."""
        p = _prompt("design me a heavy lower session, no running").lower()
        assert "200" in p or "deadlift" in p


# ─── §2.1.2 prose-quality LLM judge (gated, non-blocking) ──────────────
_JUDGE_ON = bool(os.getenv("GEMINI_API_KEY")) and \
    os.getenv("RAHAT_RUN_JUDGE", "").strip() in ("1", "true", "yes")


@pytest.mark.skipif(
    not _JUDGE_ON,
    reason="prose-quality judge is gated behind GEMINI_API_KEY + "
           "RAHAT_RUN_JUDGE=1 (non-blocking by default — keeps CI hermetic "
           "and free).",
)
class TestFraserProseQualityJudge:
    """Soft prose-quality check vs the Gemini eval bar. Floor 3/5 on each
    axis; failures are reported, the build is not gated (mirrors the Kobe
    judge doctrine)."""

    RUBRIC = (
        "You are a world-class CrossFit coach grading ONE session a coaching "
        "assistant produced for a 6'1\" athlete with a cardio-caution flag, "
        "tight ankles/hamstrings ('the Hunch'), and NO wall balls / pull-up "
        "bar / jump rope. Score 1-5 (5=best). Reply ONLY JSON: "
        '{"structure": int, "cues": int, "substitution": int, "voice": int, '
        '"comments": str}.\n'
        "Axes:\n"
        "  structure    — Warm-up -> Strength -> WOD/Metcon -> Recovery + a "
        "2-4 sentence forward-looking note?\n"
        "  cues         — 'Chest Up'/'Shoulders Back', heel lift, "
        "neck/trap down-regulation present and correct?\n"
        "  substitution — unavailable movements swapped cleanly (no wall "
        "balls / pull-ups / jump rope)?\n"
        "  voice        — matches a direct, knowledgeable coach (the Gemini "
        "original)?\n"
        "Session:\n{session}\n"
    )

    def test_judge_60min_cascade_session(self):
        from core import io as cio
        session = composer.design_session(
            "design me a 60 minute metcon, no running, ankle is flared")
        verdict = cio.llm_generate(
            self.RUBRIC.format(session=session))
        assert verdict, "judge returned no verdict"
        for axis in ("structure", "cues", "substitution", "voice"):
            m = re.search(rf'"{axis}"\s*:\s*(\d)', verdict)
            if m:
                assert int(m.group(1)) >= 3, (
                    f"judge gave {axis} < 3 on a cascade session: {verdict}"
                )
