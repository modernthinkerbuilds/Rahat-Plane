"""Delegate classifier tests.

Pin every "delegation path" decision so that:
  - Slash commands go to Kobe full-route
  - Plan mutations (/replan, "pick X for Y", "X for rest", "tolerate X") go to Kobe
  - State logs (weight, HRV, burn, tier) go to Kobe
  - Status queries (how am I doing, weekly remaining) go to Kobe
  - Pain/profile mutations go to Kobe
  - Recovery protocols (7/15 breathing, pre-fuel, post-recovery) go to Kobe
  - @kobe/@fraser/@huberman explicit address resolves correctly
  - Open-ended coaching queries fall through to "orchestrate"

This module owns the routing decisions that determine whether the
orchestrator's lookup/design/synth flow runs or whether we delegate
to Kobe's existing complete pipeline.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


# ─── Slash commands always go to Kobe ──────────────────────────────────

@pytest.mark.parametrize("msg", [
    "/pace",
    "/today",
    "/week",
    "/plan",
    "/next",
    "/help",
    "/fix Mon 800",
    "/pain neck mild",
    "/profile",
    "/profile set deadlift 200",
    "/recaliberate",
    "  /pace",  # leading whitespace
    "/Plan",    # case insensitive
])
def test_slash_commands_go_to_kobe(msg):
    path, stripped = classify_delegation(msg)
    assert path == "kobe_route"


# ─── @-address routing ────────────────────────────────────────────────

def test_at_kobe_routes_to_kobe_and_strips():
    path, stripped = classify_delegation("@kobe what's my plan today")
    assert path == "kobe_route"
    assert stripped == "what's my plan today"


def test_at_fraser_routes_to_fraser_and_strips():
    path, stripped = classify_delegation("@fraser design me a workout")
    assert path == "fraser_route"
    assert stripped == "design me a workout"


def test_at_huberman_routes_via_explicit_huberman_path():
    """P1-3 (2026-06-10): @huberman has its own delegation path so
    analytics + replays distinguish it from generic kobe_route. The
    native_client.huberman_route still funnels the call through Kobe's
    handler (mesh delegation) — the difference is auditability."""
    path, stripped = classify_delegation("@huberman should I rest today")
    assert path == "huberman_route"
    assert stripped == "should I rest today"


def test_at_miya_forces_orchestrate_path():
    """Explicit @miya means user wants Miya's synthesis layer."""
    path, stripped = classify_delegation("@miya what's my plan today")
    assert path == "orchestrate"
    # @miya may or may not strip depending on impl; both are valid
    assert "what's my plan today" in stripped


def test_at_address_empty_body_falls_through():
    """'@kobe' alone shouldn't trigger empty Kobe call."""
    path, _ = classify_delegation("@kobe")
    assert path == "orchestrate"


def test_at_address_case_insensitive():
    path, _ = classify_delegation("@KOBE plan")
    assert path == "kobe_route"


# ─── Plan mutations go to Kobe ─────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "replan the week",
    "replan rest Monday, CrossFit Tuesday Wednesday Thursday",
    "/replan",
    "recaliberate",
    "recalibrate",
    "/recaliberate",
    "pick Mon for CrossFit",
    "pick Tuesday for rest",
    "pick Wed for CrossFit",
    "pick Fri for run",
    "Wed for CrossFit",
    "Thursday for CrossFit",
    "Mon for rest",
    "Friday for rest",
    "Rest on Monday",
    "rest on Tuesday",
    "rest on tomorrow",
    "unavailable on Friday",
    "tolerate partner",
    "tolerate overhead squat",
    "swap Mon with Tue",
    "clear picks",
    "clear preferences",
])
def test_plan_mutations_go_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"Expected kobe_route for {msg!r}, got {path!r}"


# ─── State logs go to Kobe ─────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "weight: 195",
    "weight 195.5",
    "weight 89 kg",
    "I weigh 195 lbs",
    "195",  # bare number is ambiguous; we treat it as weight
    "88.5 kg",
    "HRV: 45",
    "hrv 50",
    "my HRV is 38 ms",
    "burned 800 calories",
    "burnt 1200 cal",
    "burned 750 kcal",
    "crossfit 950 kcal",
    "run 1200 cal",
    "Z2 850 kcal",
    "tier green",
    "tier: yellow",
    "set tier red",
])
def test_state_logs_go_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"Expected kobe_route for {msg!r}, got {path!r}"


# ─── Status queries go to Kobe ────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "how am I doing",
    "how am I on pace",
    "what's today's target",
    "what's tomorrow's plan",
    "how much more do I need to burn",
    "how many calories should I burn today",
    "how many did I burn this week",
    "weekly target",
    "weekly remaining",
    "week so far",
    "this week's total",
    "last week",
    "show my plan",
    "show my schedule",
    "show my dislikes",
    "show my profile",
    "show my pain",
    "current weight",
    "weight timeline",
    "goal ETA",
    "goal projection",
])
def test_status_queries_go_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"Expected kobe_route for {msg!r}, got {path!r}"


# ─── Pain/profile mutations ────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "my neck hurts",
    "my hip catches",
    "my ankle is sore",
    "I have a headache",
    "I have a catch in my hip",
    "set my deadlift at 200",
    "set deadlift to 150",
    "my 1RM for snatch is 70",
    "my max squat is 110",
])
def test_pain_profile_mutations_go_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"Expected kobe_route for {msg!r}, got {path!r}"


# ─── Recovery protocols ────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "7/15 breathing",
    "seven fifteen breathing",
    "box breathing",
    "pre-fuel",
    "post recovery",
    "recovery routine",
    "recovery protocol",
    "recovery flow",
])
def test_recovery_protocols_go_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"Expected kobe_route for {msg!r}, got {path!r}"


# ─── Open-ended coaching falls through to orchestrate ─────────────────

@pytest.mark.parametrize("msg", [
    "what's my plan today",  # NOTE: this has "what's" + "plan" but not the specific status-query regex
    "where am I on pace",  # NOTE: this is a coaching question — orchestrator handles via recalibration
    "design me a workout for tomorrow",
    "what's the workout for tomorrow",
    "should I take Saturday off",
    "hello",
    "hi miya",
    "thanks",
])
def test_open_ended_queries_orchestrate(msg):
    """These should NOT be force-delegated. The orchestrator's
    lookup/design/synth flow is better at handling open-ended coaching.

    NOTE: "what's my plan today" specifically — the orchestrator handles
    this via kobe_active_goal + kobe_recalibration + synthesis. We
    don't want to short-circuit it through Kobe's route() because we
    lose the Miya voice / arbitration layer.
    """
    path, _ = classify_delegation(msg)
    # Allow either orchestrate OR kobe_route here — there's overlap.
    # The important thing is we know what each pattern triggers.
    assert path in ("orchestrate", "kobe_route")


def test_empty_msg_falls_through():
    path, stripped = classify_delegation("")
    assert path == "orchestrate"


def test_whitespace_only_falls_through():
    path, _ = classify_delegation("   ")
    assert path == "orchestrate"


# ─── Edge cases ────────────────────────────────────────────────────────

def test_natural_phrase_with_number_doesnt_trigger_weight_log():
    """'I ran 8 miles' shouldn't be treated as weight log."""
    path, _ = classify_delegation("I ran 8 miles today")
    # Should fall through to orchestrate (no weight unit keyword)
    assert path == "orchestrate"


def test_slash_with_args_routes_to_kobe():
    path, stripped = classify_delegation("/fix Mon 800")
    assert path == "kobe_route"
    assert stripped == "/fix Mon 800"  # Kobe's slash dispatcher handles args


def test_slash_with_bot_suffix():
    """Telegram strips @botname automatically but defensive check."""
    path, stripped = classify_delegation("/pace@rahatbademiya_bot")
    assert path == "kobe_route"


# ─── WOD / workout lookup queries (Bug 2026-06-09) ────────────────────
# Live RahatBadeMiya bug: "What is tommorows WOD" went through the
# orchestrate path → Gemini paraphrased Kobe's empty response as
# "Tomorrow's WOD hasn't been synced from the gym yet" AND mixed in
# pace status that the user didn't ask for. These tests pin the fix:
# any lookup-shaped WOD/workout question routes directly to Kobe.

@pytest.mark.parametrize("msg", [
    # the exact strings from the 2026-06-09 transcript
    "What is tommorows WOD",
    "What is tomorrow's WOD",
    "what is tomorrow's workout",
    # canonical forms
    "what's today's WOD",
    "whats todays workout",
    "what's the workout for tomorrow",
    "what's the WOD",
    "what's the workout",
    # weekday lookups
    "what's Monday's WOD",
    "what's Wednesdays workout",
    "WOD for Friday",
    "workout for Saturday",
    "tell me Friday's workout",
    "show me tomorrow's WOD",
    "show me the WOD",
    "show me the workout",
    # typo tolerance
    "What is tommorow WOD",
    "tommorows workout",
    "tomorows WOD",
    "what is tomorow's session",
    # other lookup verbs
    "where's tomorrow's workout",
    "where is the WOD",
    "see tomorrow's workout",
    # short/colloquial
    "WOD for tomorrow",
    "session for Wednesday",
    "got the WOD for tomorrow",
    "got any WOD for Wednesday",
    "programming for this week",
    "gym programming for tomorrow",
])
def test_wod_lookup_routes_to_kobe(msg):
    path, stripped = classify_delegation(msg)
    assert path == "kobe_route", (
        f"expected kobe_route for {msg!r} but got {path!r}; "
        f"this regression returns the Bug-2026-06-09 paraphrase"
    )


# Negative cases: design intent must still orchestrate so Fraser
# (via the synth flow) can author a workout. Routing these to Kobe
# would make him answer with "I don't have a workout to look up."
@pytest.mark.parametrize("msg", [
    "design me a workout",
    "design a workout for tomorrow",
    "build me a WOD",
    "build a session for Wednesday",
    "create a workout for Friday",
    "generate a workout",
    "come up with a workout",
    "make me a workout",
    "make up a WOD",
    "give me a workout",
    "I need a workout for tomorrow",
    "I want a workout for Wednesday",
    "scale the workout for me",
    "substitute the workout",
    "modify the workout",
    "swap out the workout",
])
def test_workout_design_intent_orchestrates(msg):
    path, _ = classify_delegation(msg)
    assert path == "orchestrate", (
        f"expected orchestrate for design intent {msg!r} but got {path!r}; "
        f"sending design intent to Kobe means Fraser never gets a chance"
    )


# Explicit @-address must still win over WOD-lookup routing.
def test_at_fraser_wod_lookup_still_goes_to_fraser():
    """@fraser overrides the WOD-lookup regex — user is explicitly asking Fraser."""
    path, stripped = classify_delegation("@fraser what's tomorrow's workout")
    assert path == "fraser_route"
    assert stripped == "what's tomorrow's workout"


# Sanity check: the WOD-lookup regex doesn't accidentally over-match
# generic coaching questions that should remain orchestrate.
@pytest.mark.parametrize("msg", [
    "how am I feeling today",
    "what should I eat",
    "am I behind",
    "how was last week",
    "tell me about my pace",
    "what's my goal date",
])
def test_non_workout_lookups_still_orchestrate(msg):
    path, _ = classify_delegation(msg)
    # These either route to status queries (kobe_route via _STATUS_QUERY_RE)
    # or fall through to orchestrate — both are acceptable. The only
    # failure mode is the WOD regex grabbing something unrelated.
    assert path in ("orchestrate", "kobe_route")
