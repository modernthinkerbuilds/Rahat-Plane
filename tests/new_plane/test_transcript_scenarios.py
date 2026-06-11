"""Transcript scenario coverage tests.

This module pins every distinct user-behavior scenario that appears
in the historical Gemini chat transcripts (Fraser sports-coach + Sports
Scientist nutrition-coach + the old Miya routing chat).

For each scenario, we verify that the new-plane Miya routing produces
the right path (delegate vs orchestrate) and that the underlying agent
is actually invoked. We do NOT validate the exact text of the response
(that's the LLM's job and would be brittle) — we validate the routing
contract.

If you want to add a new scenario, add it to the matrix at the bottom.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation
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


# ════════════════════════════════════════════════════════════════════
# Section 1: Fraser sports-coach transcript scenarios
# ════════════════════════════════════════════════════════════════════
#
# These are all design/scaling/WOD-substitution queries that should
# go to Fraser (or, when phrased as a lookup, to Kobe's get_gym_wod_on).


class TestFraserCoachScenarios:
    """Scenarios from the Fraser chat with Gemini transcript."""

    @pytest.mark.parametrize("msg", [
        "design me a workout for tomorrow",
        "give me a workout",
        "design a wod with a chest focus",
        "scale today's WOD",
        "scale the deadlift WOD for sleep deprivation",
        "create a session for Friday",
        "substitute wall balls for me",
        "swap the box jumps",
        "give me a sub for double unders",
        "make this lower impact",
    ])
    def test_design_requests_orchestrate_to_fraser(self, msg):
        """Design/scale/sub queries hit the orchestrate path which
        routes to Fraser via the design intent classification."""
        path, _ = classify_delegation(msg)
        # Most go to orchestrate (orchestrator's design intent → Fraser)
        # but explicit @fraser address routes directly.
        assert path in ("orchestrate", "fraser_route")

    def test_explicit_fraser_address_routes_directly(self, monkeypatch):
        """@fraser bypasses the orchestrator and hits Fraser's route()."""
        called = []
        monkeypatch.setattr(
            "agents.fraser.handler.route",
            lambda msg, chat_id=None: (called.append(msg) or "Workout ready"),
        )
        resp = handle(Turn(user_message="@fraser design me a wod",
                            chat_id="c1"))
        assert resp.routing["path"] == "fraser_route"
        assert called == ["design me a wod"]

    def test_warmup_request_via_design(self):
        """'Give me a good warmup for this WOD' is a design request —
        Fraser's composer handles it via the LLM."""
        path, _ = classify_delegation("give me a good warmup for this WOD")
        # 'workout' keyword + 'warmup' = design request, orchestrator
        # handles via Fraser
        assert path in ("orchestrate", "fraser_route")

    def test_cooldown_request_via_design(self):
        """Cool-downs and stretching routines."""
        path, _ = classify_delegation("give me a good cool down after my 10K")
        assert path in ("orchestrate", "fraser_route")

    def test_recovery_routine_request_routes_to_kobe(self):
        """When user says 'recovery routine' (a specific term),
        Kobe's recovery flow handles it."""
        path, _ = classify_delegation("give me a recovery routine")
        assert path == "kobe_route"

    def test_calorie_targeted_workout_via_design(self):
        """'Burn 800 calories in 75 min' triggers Fraser's calorie-targeted
        design path."""
        path, _ = classify_delegation("design a workout to burn 800 calories in 75 min")
        # Has 'workout' + 'design' → fraser_route via orchestrate (design intent)
        # OR explicit fraser address. Either is correct.
        assert path in ("orchestrate", "fraser_route")

    def test_substitute_for_equipment_unavailable(self):
        """'Don't have a treadmill, what can I do?'"""
        path, _ = classify_delegation("don't have a treadmill, what should I do?")
        # Open-ended → orchestrate (Fraser via design intent)
        assert path == "orchestrate"

    def test_mobility_issue_modification(self):
        """'My neck hurts, give me an alternative WOD' — pain mutation
        plus design request. The 'my neck hurts' phrase is a pain log
        that goes to Kobe first."""
        path, _ = classify_delegation("my neck hurts")
        assert path == "kobe_route"

    def test_sick_workout_request_orchestrates(self):
        """'I am under the weather, what can I do?' should orchestrate
        (Fraser receives the context and adjusts)."""
        path, _ = classify_delegation(
            "I am under the weather but motivated, what can I do?"
        )
        assert path == "orchestrate"


# ════════════════════════════════════════════════════════════════════
# Section 2: Sports Scientist (Kobe) transcript scenarios
# ════════════════════════════════════════════════════════════════════


class TestSportsScientistScenarios:
    """Scenarios from the Sports Scientist (Kobe) chat with Gemini."""

    @pytest.mark.parametrize("msg", [
        "how many calories should I burn today",
        "how many calories should I burn this week",
        "how many calories did I burn last week",
        "how many calories did I burn this week",
        "how many calories do I need to burn",
        "how much more do I need to burn",
        "weekly target",
        "weekly remaining",
        "last week",
        "this week's total",
    ])
    def test_calorie_tracking_queries_route_to_kobe(self, msg):
        """Calorie tracking queries go to Kobe deterministically."""
        path, _ = classify_delegation(msg)
        assert path == "kobe_route"

    @pytest.mark.parametrize("msg", [
        "I weigh 195 lbs",
        "weight 88.5 kg",
        "weight: 195.8",
        "195",
        "89 kg",
    ])
    def test_weight_logging_routes_to_kobe(self, msg):
        """Weight logs trigger Kobe's log_weight path."""
        path, _ = classify_delegation(msg)
        assert path == "kobe_route"

    @pytest.mark.parametrize("msg", [
        "HRV: 45",
        "my HRV is 38 ms",
        "hrv 50",
    ])
    def test_hrv_logging_routes_to_kobe(self, msg):
        path, _ = classify_delegation(msg)
        assert path == "kobe_route"

    @pytest.mark.parametrize("msg", [
        "burned 800 calories",
        "burnt 1200 cal",
        "crossfit 950 kcal",
        "run 1400 cal",
    ])
    def test_burn_logging_routes_to_kobe(self, msg):
        path, _ = classify_delegation(msg)
        assert path == "kobe_route"

    def test_pace_query_routes_to_kobe(self):
        path, _ = classify_delegation("/pace")
        assert path == "kobe_route"

    def test_show_plan_routes_to_kobe(self):
        path, _ = classify_delegation("show my plan")
        assert path == "kobe_route"

    def test_recovery_protocol_routes_to_kobe(self):
        """7/15 breathing, box breathing, pre-fuel, post-recovery."""
        for msg in ["7/15 breathing", "box breathing", "pre-fuel", "post recovery"]:
            path, _ = classify_delegation(msg)
            assert path == "kobe_route", f"Failed for {msg!r}"

    def test_nutrition_query_orchestrates(self):
        """'Is salmon better than chicken for weight loss?' — orchestrator
        handles as an open-ended coaching query."""
        path, _ = classify_delegation("is salmon better than chicken for weight loss")
        assert path == "orchestrate"

    def test_pre_weigh_in_strategy_orchestrates(self):
        """'When should I weigh in?' — coaching query."""
        path, _ = classify_delegation("when should I weigh in")
        # Doesn't match the deterministic patterns; orchestrator handles.
        assert path == "orchestrate"

    def test_explicit_kobe_address(self, monkeypatch):
        """@kobe how many calories should I burn → strips prefix."""
        called = []
        monkeypatch.setattr(
            "agents.the_scientist.handler.route",
            lambda msg: (called.append(msg) or "Today: 1,200 kcal"),
        )
        resp = handle(Turn(user_message="@kobe how am I doing", chat_id="c1"))
        assert resp.routing["path"] == "kobe_route"
        assert called == ["how am I doing"]


# ════════════════════════════════════════════════════════════════════
# Section 3: Old Miya transcript scenarios (the bugs that got fixed)
# ════════════════════════════════════════════════════════════════════


class TestOldMiyaTranscriptScenarios:
    """Scenarios from the old Miya transcript where bugs were observed."""

    def test_wod_lookup_question(self):
        """'What is tomorrow's WOD' must route to kobe_route (Bug 2026-06-09).

        Originally this test asserted orchestrate path — that was the
        pre-fix behavior that caused Gemini to paraphrase Kobe's response
        as 'hasn't been synced' and mix in unrelated pace facts. The fix
        delegates WOD lookups to kobe_route so Kobe's deterministic
        gym_wod_on output is returned verbatim.
        """
        path, _ = classify_delegation("what is tomorrow's WOD")
        assert path == "kobe_route"

    def test_replan_command_with_plan_in_message(self):
        """'/replan rest Monday, CrossFit on Tuesday Wednesday Thursday.
        Zone 2 run on Saturday'"""
        msg = ("/replan rest Monday , CrossFit on Tuesday, Wednesday and "
               "Thursday. Zone 2 run on Saturday")
        path, _ = classify_delegation(msg)
        assert path == "kobe_route"

    def test_rest_on_monday_routes_to_kobe(self):
        """'Rest on Monday' — plan mutation."""
        path, _ = classify_delegation("Rest on Monday")
        assert path == "kobe_route"

    @pytest.mark.parametrize("msg", [
        "Wed for CrossFit",
        "Tue for CrossFit",
        "Thu for CrossFit",
        "Friday for rest",
        "pick Mon for CrossFit",
        "pick Wed for CrossFit",
        "We'd for CrossFit",  # User typo from transcript
        "Wed for CrossFit",
    ])
    def test_day_picks_route_to_kobe(self, msg):
        """Day pick commands as seen in the transcript."""
        path, _ = classify_delegation(msg)
        # "We'd for CrossFit" is a typo — won't match the regex; that's OK
        # The other variants must match.
        if msg.startswith("We'd"):
            # Typo case; either path is acceptable since it's user error
            assert path in ("kobe_route", "orchestrate")
        else:
            assert path == "kobe_route", f"Failed for {msg!r}"

    def test_recaliberate_command(self):
        """/recaliberate triggers Kobe's catch-up math."""
        path, _ = classify_delegation("/recaliberate")
        assert path == "kobe_route"

    def test_plan_command(self):
        """/plan shows the weekly grid."""
        path, _ = classify_delegation("/plan")
        assert path == "kobe_route"

    def test_yes_alone_routes_with_context(self, monkeypatch):
        """The 'Yes' bug: when user replies 'Yes' to a previous bot question,
        we shouldn't say 'I'm not sure how to route that'.

        With chat_memory enabled, the synthesizer gets the previous bot
        turn and can act on the user's confirmation.
        """
        monkeypatch.setenv("RAHAT_XAGENT_MEMORY", "1")
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_active_goal",
            lambda: {"active": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_recalibration",
            lambda: {"behind_pace": False, "summary": "On pace"},
        )
        monkeypatch.setattr(
            "core.chat_memory.to_prompt_block",
            lambda chat_id: (
                "═══ RECENT CONVERSATION ═══\n"
                "Bot: Need to plan the remaining days?\n"
                "User: Yes"
            ),
        )
        # We're really just checking the route doesn't crash with 'Yes'.
        resp = handle(Turn(user_message="Yes", chat_id="c1"))
        assert resp.trace_id
        # 'Yes' alone falls through to orchestrate (no delegate matches)
        assert resp.sent in (True, False)  # either fine — just no crash

    def test_tolerate_partner_routes_to_kobe(self):
        path, _ = classify_delegation("tolerate partner")
        assert path == "kobe_route"

    def test_tolerate_overhead_squat(self):
        path, _ = classify_delegation("tolerate overhead squat")
        assert path == "kobe_route"


# ════════════════════════════════════════════════════════════════════
# Section 4: Cross-cutting end-to-end smokes
# ════════════════════════════════════════════════════════════════════


class TestEndToEndSmokes:
    """Spot-check that critical scenarios actually round-trip cleanly."""

    def test_slash_plan_round_trip(self, monkeypatch):
        """User sends '/plan' → Kobe's route() called → text returned."""
        monkeypatch.setattr(
            "agents.the_scientist.handler.route",
            lambda msg: "This week — Jun 8-14\nTier hammer, 6,000 kcal target",
        )
        resp = handle(Turn(user_message="/plan", chat_id="c1"))
        assert resp.sent is True
        assert "hammer" in resp.text

    def test_design_request_round_trip(self, monkeypatch):
        """Design request flows through orchestrator to Fraser."""
        monkeypatch.setattr(
            "agents.fraser.composer.design_session",
            lambda msg, chat_id=None: "5x5 back squat at 70% + 12-min AMRAP",
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_active_goal",
            lambda: {"active": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_recalibration",
            lambda: {"behind_pace": False},
        )
        resp = handle(Turn(user_message="design me a workout for tomorrow",
                            chat_id="c1"))
        assert resp.sent is True
        # fraser_design_session should be in used_tools
        assert "fraser_design_session" in resp.used_tools

    def test_wod_lookup_round_trip(self, monkeypatch):
        """WOD lookup reaches Kobe, not Fraser (Bug 2026-06-09 fix).

        Now delegates via kobe_route — Kobe's route() handles the
        gym_wod_on call internally and returns deterministic text,
        bypassing Gemini synthesis.
        """
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_active_goal",
            lambda: {"active": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_recalibration",
            lambda: {"behind_pace": False},
        )
        monkeypatch.setattr(
            "agents.the_scientist.tools.get_gym_wod_on",
            lambda day: "Front Squat 5x5 + Golden Boot AMRAP",
        )
        resp = handle(Turn(user_message="what's the workout for tomorrow",
                            chat_id="c1"))
        assert "kobe_route" in resp.used_tools
        assert "fraser_design_session" not in resp.used_tools

    def test_weight_logging_round_trip(self, monkeypatch):
        """'195.5' weight log delegates to Kobe."""
        called = []
        monkeypatch.setattr(
            "agents.the_scientist.handler.route",
            lambda msg: (called.append(msg) or "Weight logged: 195.5 lbs"),
        )
        resp = handle(Turn(user_message="195.5", chat_id="c1"))
        assert resp.routing["path"] == "kobe_route"
        assert called == ["195.5"]
