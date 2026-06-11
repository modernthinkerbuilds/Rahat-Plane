"""Pin: 2026-06-09 — WOD lookup paraphrased to 'not synced' + pace fact
hallucinated as 'ahead of plan' when user was behind.

SYMPTOM (production, RahatBadeMiya_bot 23:45):
    User: "What is tommorows WOD"
    Bot:  "Tomorrow's WOD hasn't been synced from the gym yet. With
           your goal date tomorrow, June 10th, Kobe's recalibration
           shows you're 1,433 kcal ahead of plan for the week. This
           gives you a solid buffer."

    Two defects in one response:

    (1) WOD lookup paraphrased an absence into a sync-status claim.
        Kobe's get_gym_wod_on() for Wed returned an empty/None value.
        The new-plane orchestrator went through Gemini synthesis, which
        paraphrased that absence as a confident claim about gym
        integration state ("hasn't been synced"). The bot never queried
        gym-integration health — it invented that explanation.

    (2) Unprompted pace fact mixed in, also wrong.
        User had burned 1,433 / 6,000 kcal by Tue evening. Prorated
        pace was ~1,714 (6,000 × 2/7). User was ~280 kcal BEHIND, not
        1,433 ahead. Same Bug-H-class hallucination on a different
        intent.

ROOT CAUSE:
    `new_plane/miya_runner/delegate_classifier.py:classify_delegation()`
    had no WOD-lookup pattern. WOD queries containing an interrogative
    + workout noun ("what is X WOD") fell through every check and went
    to the orchestrate path. In orchestrate, the synth layer (1)
    paraphrased the empty gym_wod fact and (2) included pace facts in
    the prompt that weren't relevant to the user's question — so
    Gemini freelanced.

    Additionally, even if the orchestrate WOD path had been correct,
    the day-token regex didn't tolerate "tommorow" (typo), so the
    day would have defaulted to "today" rather than Wed.

FIX:
    new_plane/miya_runner/delegate_classifier.py:
      - Added _WOD_LOOKUP_RE. Catches:
          "what is/show/tell ... WOD/workout/session/programming"
          "(today's|tomorrow's|<typo>|<weekday>'s) <noun>"
          "<noun> for|on|this <day>"
        Typo-tolerant: "tommorow", "tomorow", weekday names.
      - Added _WOD_DESIGN_GUARD_RE. Prevents the lookup pattern from
        swallowing design intent ("design me a workout" → orchestrate
        → Fraser).
      - New check #8 in classify_delegation: WOD lookup → kobe_route.

    Tests added to the routing layer:
      tests/new_plane/test_runner_delegate_classifier.py — +29
        WOD-lookup positives (including the exact "tommorows" typo
        from the transcript), +16 design-intent negatives.
      tests/new_plane/test_regression_equivalents.py — new
        TestWodLookupDoesNotParaphrase + TestWodDesignIntentStillReachesFraser
        classes.
      Six existing tests updated to the new contract
        (kobe_route vs kobe_gym_wod_on).

THIS PIN ASSERTS:
    For each historical phrasing from the 2026-06-09 transcript and
    its close variants, classify_delegation returns "kobe_route" —
    NOT "orchestrate". If a future refactor regresses the routing,
    the WOD-lookup query goes back through synth and the paraphrase
    bug returns.

    Negative cases assert that explicit design intent ("design me a
    workout", "build me a session") still routes to "orchestrate" so
    Fraser is reached. The fix MUST NOT over-route to Kobe.

The complementary tests at the new-plane orchestrator level (response
text does not contain "hasn't been synced", "ahead of plan") live in
tests/new_plane/test_regression_equivalents.py. This file pins the
*routing decision* — the necessary condition for the response-level
behavior.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation


# ─── Exact transcript + canonical variants must route to Kobe ──────

WOD_LOOKUP_PHRASINGS = [
    # The verbatim user message from the 23:45 transcript
    "What is tommorows WOD",
    # Canonical forms
    "What is tomorrow's WOD",
    "what is tomorrow's workout",
    "what's today's WOD",
    "whats todays workout",
    "what's the workout for tomorrow",
    "what's the WOD",
    "what's the workout",
    # Weekday lookups (Kobe owns the day → WOD resolution)
    "what's Monday's WOD",
    "what's Wednesdays workout",
    "WOD for Friday",
    "workout for Saturday",
    "tell me Friday's workout",
    "show me tomorrow's WOD",
    "show me the WOD",
    "show me the workout",
    # Typo tolerance — the failure surface the live bug exposed
    "What is tommorow WOD",
    "tommorows workout",
    "tomorows WOD",
    "what is tomorow's session",
    # Other lookup verbs
    "where's tomorrow's workout",
    "where is the WOD",
    "see tomorrow's workout",
    # Short / colloquial
    "WOD for tomorrow",
    "session for Wednesday",
    "got the WOD for tomorrow",
    "got any WOD for Wednesday",
    "programming for this week",
    "gym programming for tomorrow",
]


@pytest.mark.parametrize("msg", WOD_LOOKUP_PHRASINGS)
def test_wod_lookup_routes_to_kobe_not_orchestrate(msg):
    """If this goes red, the Bug 2026-06-09 paraphrase is back.

    Routing this phrasing to "orchestrate" puts it on the synth path,
    where Gemini gets license to (1) paraphrase empty tool results as
    sync-status claims and (2) merge unrelated facts into the response.
    """
    path, _stripped = classify_delegation(msg)
    assert path == "kobe_route", (
        f"WOD lookup {msg!r} routed to {path!r}; "
        f"Bug 2026-06-09 paraphrase will recur if this stays on the "
        f"orchestrate path."
    )


# ─── Design intent must NOT be swallowed by the fix ────────────────

WOD_DESIGN_PHRASINGS = [
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
]


@pytest.mark.parametrize("msg", WOD_DESIGN_PHRASINGS)
def test_design_intent_does_not_get_swallowed(msg):
    """Negative case for Bug 2026-06-09 fix: don't over-route to Kobe.

    The WOD-lookup classifier MUST NOT swallow design intent. If "design
    me a workout" routes to Kobe, Kobe answers with "I don't have
    anything to look up" and Fraser never gets called.

    The delegate_classifier's _WOD_DESIGN_GUARD_RE is the structural
    guard. If a future refactor weakens or removes the guard, this
    test surfaces it before the bug ships.
    """
    path, _stripped = classify_delegation(msg)
    assert path != "kobe_route", (
        f"Design intent {msg!r} routed to {path!r}; "
        f"Fraser-design path is blocked when Kobe owns this."
    )


# ─── Explicit @-address must still win over the new WOD pattern ────

def test_at_fraser_wod_lookup_still_goes_to_fraser():
    """The @fraser prefix is the user's explicit override. It must
    win over the WOD-lookup heuristic. If this goes red, the @-address
    routing has been broken by the fix."""
    path, stripped = classify_delegation("@fraser what's tomorrow's workout")
    assert path == "fraser_route"
    assert stripped == "what's tomorrow's workout"


def test_at_miya_wod_lookup_orchestrates():
    """The @miya prefix forces the orchestrate path so the user can
    explicitly invoke Miya's synth flow. This is the *only* way a
    WOD-lookup phrasing reaches orchestrate after the Bug 2026-06-09
    fix."""
    path, stripped = classify_delegation("@miya what's tomorrow's workout")
    assert path == "orchestrate"
    assert stripped == "what's tomorrow's workout"
