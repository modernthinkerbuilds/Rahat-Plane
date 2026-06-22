"""Fraser/Kobe behavior evals — grounding + routing fidelity (2026-05-23).

Complements test_fraser_conversation.py (which evals the deterministic
adapter output). These evals assert two things that determine output
quality WITHOUT needing a live LLM:

  1. GROUNDING — the composer hands the LLM the right context. A session
     can only be good if the prompt carries the athlete's real 1RMs, the
     baseline constraints (BP, the Hunch, heel lift), Kobe's target, and
     any active pain. If the prompt is missing context, the model
     hallucinates (the exact failure these pin against).

  2. ROUTING FIDELITY — the right message reaches the right agent/handler.
     Design → Fraser; day-specified lookup → Kobe; slash/plan-edit →
     deterministic dispatcher routes.

All hermetic: the google.genai stub (tests/conftest.py) + a temp DB.
"""
from __future__ import annotations

import pytest

from agents.fraser import composer
from agents.fraser import handler as fraser_handler
from core import dispatcher


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "eval.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


@pytest.fixture(autouse=True)
def _eval_env(fresh_db):
    """Reset the profile cache (module global) around each eval and
    bootstrap the substrate schema so pain/profile writes land."""
    from core import athlete_profile
    athlete_profile.reset()
    try:
        from core import memory as mem
        mem.stats("scientist")
    except Exception:
        pass
    yield
    athlete_profile.reset()


# ───────────────────── 1. Composer grounding ────────────────────────
class TestComposerDesignGrounding:
    def _prompt(self, msg="design me a 60 minute session for today",
                chat_id=None):
        return composer.build_design_prompt(
            composer.parse_request(msg), chat_id=chat_id)

    def test_includes_athlete_profile_block(self):
        assert "ATHLETE PROFILE" in self._prompt()

    def test_includes_real_1rms(self):
        p = self._prompt().lower()
        # The athlete's actual recorded maxes must be in the prompt, or
        # Fraser invents working weights.
        assert "deadlift" in p and "200" in p
        assert "back_squat" in p and "150" in p

    def test_includes_bp_breathing_rule(self):
        p = self._prompt().lower()
        assert "blood pressure" in p or "exhale" in p

    def test_includes_the_hunch_cue(self):
        p = self._prompt().lower()
        assert "hunch" in p or "shoulders back" in p or "chest up" in p

    def test_includes_heel_lift_rule(self):
        assert "heel" in self._prompt().lower()

    def test_includes_default_4_section_output_schema(self):
        p = self._prompt()
        assert "═══ OUTPUT ═══" in p
        for part in ("Part 1", "Part 2", "Part 3", "Part 4"):
            assert part in p
        # The schema is a DEFAULT, not a mandate (ADR-011).
        assert "MANDATORY" not in p

    def test_includes_precedence_and_real_clock(self):
        p = self._prompt()
        assert "OVERRIDE" in p              # request overrides gym WOD + profile
        assert "Current local time" in p    # real clock, not a guess

    def test_includes_never_assume_rule(self):
        # The "don't assume unreported HRV/sleep" guardrail must be present.
        assert "NEVER ASSUME" in self._prompt()

    def test_active_pain_is_injected_and_demands_adaptation(self):
        from core import pain_state
        pain_state.report("left shoulder", severity="sharp")
        p = self._prompt().lower()
        assert "left shoulder" in p
        # The directive must tell Fraser to adapt around pain.
        assert "adapt" in p or "substitute" in p

    def test_set_1rm_override_flows_into_prompt(self):
        from core import athlete_profile
        athlete_profile.set_one_rm("deadlift", 170)
        assert "170" in self._prompt()


# ───────────────────── 2. Conversation prompt shape (unified) ───────
class TestConversationPromptShape:
    """ADR-011 unified path: there is ONE prompt. When a chat_id has prior
    turns, the SAME build_design_prompt injects the conversation + the
    refine directive, so the model answers/refines against it — no
    separate follow-up prompt."""

    def _seed_and_prompt(self, chat_id="EVAL-FU"):
        from core import chat_memory
        chat_memory.append(chat_id, chat_memory.ROLE_USER, "design a session")
        chat_memory.append(chat_id, chat_memory.ROLE_BOT,
                           "## Part 2: Strength\nBack Squat 60 kg")
        return composer.build_design_prompt(
            composer.parse_request("what weights should I follow?"),
            chat_id=chat_id)

    def test_includes_recent_conversation(self):
        assert "RECENT CONVERSATION" in self._seed_and_prompt()

    def test_includes_refine_vs_answer_directive(self):
        p = self._seed_and_prompt()
        assert "REFINES" in p and "ASKS about the prior session" in p

    def test_still_grounds_in_profile(self):
        # Consistency: a refinement/answer must use the same 1RMs.
        assert "ATHLETE PROFILE" in self._seed_and_prompt()


# ───────────────────── 3. Output validation ─────────────────────────
class TestOutputValidation:
    def test_accepts_well_formed_4_section(self):
        good = "## Part 1\n..\n## Part 2\n..\n## Part 3\n..\n## Part 4\n.."
        assert composer._looks_like_4_section(good)

    def test_rejects_partial_session(self):
        bad = "## Part 1\n..\n## Part 2\n.."
        assert not composer._looks_like_4_section(bad)

    def test_fallback_is_a_real_outline_not_a_stub(self):
        req = composer.parse_request("design me a session")
        out = composer._fallback_no_llm(req, "LLM unavailable")
        assert "[Fraser] mode=" not in out      # never the old stub
        assert "warm-up" in out.lower() and "cool-down" in out.lower()

    def test_loose_response_is_wrapped_not_dropped(self):
        req = composer.parse_request("design me a session")
        wrapped = composer._wrap_loose_response("freeform text", req)
        assert "freeform text" in wrapped


# ───────────────────── 4. Routing fidelity (Kobe dispatcher) ─────────
@pytest.mark.parametrize("msg,expected_route", [
    ("/pace", "slash"),
    ("/pain left knee sharp", "slash"),
    ("/profile set deadlift 160", "slash"),
    ("what is the WOD for Tuesday", "gym_wod_on_day"),
    ("show me Friday's workout", "show_day_workout"),
    ("what's the wod tomorrow", "gym_wod_relative"),
    ("gym wod yesterday", "gym_wod_relative"),
    ("show me my plan", "show_plan_this_week"),
    ("what's my plan for next week", "show_plan_next_week"),
    ("hrv 55", "hrv_log"),
    ("weight 92", "weight_log"),
    ("tier hammer", "tier_set"),
    ("pick Sun for crossfit", "plan_mutation"),
    ("Mon for crossfit", "plan_mutation"),
    ("Wed rest", "plan_mutation"),
    ("replan", "plan_mutation"),
    ("can't make Thursday", "plan_mutation"),
    ("box breathing", "breathing_box"),
    ("how much is left this week", "weekly_remaining"),
    ("what's my workout today", "workout_today"),
])
def test_kobe_dispatcher_routing_fidelity(msg, expected_route):
    assert dispatcher.match_route(msg) == expected_route, (
        f"{msg!r} should route to {expected_route!r}, got "
        f"{dispatcher.match_route(msg)!r}")


# ───────────────────── 5. Routing fidelity (Fraser delegation) ──────
@pytest.mark.parametrize("msg,target", [
    # Day-specified lookups → Kobe.
    ("what is my workout for Tuesday", "kobe"),
    ("show me Friday's session", "kobe"),
    ("what's the wod tomorrow", "kobe"),
    # Cross-domain → Kobe / Huberman.
    ("what's my weight target by May 23", "kobe"),
    ("how did I sleep last night", "huberman"),
    # Design + day-less → stay Fraser (None).
    ("what's my WOD", None),
    ("design me a 60-min WOD", None),
    ("give me today's workout", None),
    ("scale today's WOD for my ankle", None),
    ("give me a 75-minute session that burns 800 kcal", None),
    ("can I substitute pull-ups for ring rows", None),
])
def test_fraser_delegation_fidelity(msg, target):
    assert fraser_handler._should_delegate(msg) == target, (
        f"{msg!r} should delegate to {target!r}, got "
        f"{fraser_handler._should_delegate(msg)!r}")
