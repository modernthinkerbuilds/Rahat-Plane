"""Pin: 2026-06-13 — live RahatBadeMiya bugs after restart.

SYMPTOMS (live Telegram transcript, 21:05–21:08 PDT):

  Turn 1 (user): "On which day per my plan"
  Bot: long multi-paragraph reply about an aggressive timeline change
       it invented. Hallucinated a "conflict" the user didn't raise.

  Turn 2 (user): "Assume I need to hit 196 by 06/22- create a plan for me"
  Bot: "Kobe flags a conflict here. ... Fraser has designed a high-volume
        workout for today ... If this is the intensity you want to commit
        to, say 'confirm the new goal' and I will officially update your
        plan."

  Then a SECOND reply to the same message:
  Bot: "Venkat, to move you toward that 196 lb target, Fraser has designed
        a high-volume session ... a quick clarification from Kobe: the
        system shows your target date is June 10, 2026, not June 22nd."

Four bugs in one exchange:
  (1) VOICE LEAK — bot named internal specialists ("Kobe flags",
      "Fraser has designed", "a clarification from Kobe").
  (2) OVERPRODUCTION — terse goal-math question got a multi-section
      workout plan dump (Part 1 Warm-up, Part 2 Strength, Part 3 WOD,
      Part 4 Cool-down).
  (3) HYPOTHETICAL MISHANDLED — "Assume I need to hit 196 by 06/22"
      was treated as a real goal change and the bot offered to
      "officially update your plan".
  (4) DUPLICATE REPLIES — the same user message received two distinct
      bot turns (operational: two runner instances polling the same
      Telegram token; "telegram getUpdates failed: HTTP Error 409:
      Conflict" all over the runner log).

ROOT CAUSES:
  Voice leak + overproduction: SYSTEM_PROMPT in
    new_plane/miya_runner/synthesizer.py explicitly told Gemini to
    'name the conflict' and 'Cite the source when it matters. "Kobe
    says…" or "the gym WOD…" or "Fraser's design…"'. The prompt also
    said "be brief" but didn't enforce length-matching, and didn't
    prohibit dumping workout templates outside of design intent. And
    the prompt builder labels the design draft "FRASER'S DRAFT:" so
    Gemini parrots that label back.

  Hypothetical mishandled: prompt had no rule for conditional /
    "assume" framings. The orchestrator routed to design_request +
    invoked fraser_design_session.

  Duplicate replies: two processes were polling the same bot token
    (Telegram 409 — operational, not a code bug).

FIXES (this commit):
  - SYSTEM_PROMPT rewritten: "ONE voice", forbids naming Kobe/Fraser,
    enforces length-matching, adds hypothetical-handling rule, blocks
    workout dumps unless explicitly requested.
  - Prompt builder label "FRASER'S DRAFT:" → "WORKOUT DRAFT (internal,
    re-voice as Miya):".
  - Structured fallback "fraser:" → "workout:".

THIS PIN ASSERTS:
  - SYSTEM_PROMPT contains the ONE-voice rule and forbids the leak
    phrases.
  - _build_prompt's draft label does not include the word "FRASER".
  - The structured fallback output does not include the word "fraser".
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import synthesizer
from new_plane.miya_runner.synthesizer import (
    SYSTEM_PROMPT,
    _build_prompt,
    _structured_fallback,
)


# ─── Bug 1: VOICE LEAK ─────────────────────────────────────────────────

class TestSystemPromptForbidsSpecialistNames:
    """The system prompt itself must not instruct Gemini to name
    internal specialists. If the prompt asks for it, the model will
    do it — which is exactly what shipped in the 21:05 live transcript.
    """

    def test_prompt_has_one_voice_rule(self):
        assert "ONE voice" in SYSTEM_PROMPT or "one voice" in SYSTEM_PROMPT.lower()

    def test_prompt_forbids_attributing_to_kobe(self):
        # Specifically the leak phrase the live bot shipped.
        forbidden_in_prompt = [
            'Cite the source',
            '"Kobe says',
            "'Kobe says",
            "Fraser's design",
        ]
        for phrase in forbidden_in_prompt:
            assert phrase not in SYSTEM_PROMPT, (
                f"SYSTEM_PROMPT still contains {phrase!r}; this is what "
                f"made the 21:05 live bot say 'Kobe flags a conflict'."
            )

    def test_prompt_names_kobe_only_in_no_attribution_rule(self):
        # The prompt may mention 'Kobe' in the "do NOT say" rule, but
        # nowhere as a positive instruction. We sanity-check by ensuring
        # the word appears at most once and inside the negative rule.
        kobe_count = SYSTEM_PROMPT.count("Kobe")
        # Allow up to one mention (in the negative example).
        assert kobe_count <= 1, (
            f"SYSTEM_PROMPT mentions 'Kobe' {kobe_count} times. After the "
            f"voice-leak fix it should appear at most once, inside the "
            f"'never attribute to' negative example."
        )


class TestPromptDraftLabelHidesSpecialist:
    """The Fraser draft label leaks into Gemini's reply if we call it
    'FRASER'S DRAFT'. After 2026-06-13 we use a neutral label."""

    def test_label_does_not_say_fraser(self):
        prompt = _build_prompt(
            user_message="design me a workout for tomorrow",
            facts={}, arbitration=None,
            fraser_text="3 rounds: 10 squats, 10 burpees",
            recent_signals=None,
        )
        # If the label contains "FRASER", Gemini will parrot it.
        assert "FRASER'S DRAFT" not in prompt, (
            "Prompt builder still labels the workout draft 'FRASER'S DRAFT:'. "
            "Live bot at 21:08 said 'Fraser has designed a high-volume session' "
            "because Gemini saw that label and parroted it."
        )

    def test_label_uses_neutral_workout_term(self):
        prompt = _build_prompt(
            user_message="design me a workout for tomorrow",
            facts={}, arbitration=None,
            fraser_text="3 rounds: 10 squats, 10 burpees",
            recent_signals=None,
        )
        # Some neutral phrasing should appear so Gemini has a label that
        # doesn't leak.
        assert "WORKOUT DRAFT" in prompt or "workout draft" in prompt.lower()


class TestStructuredFallbackDoesNotLeakName:
    """The fallback is what ships to the user when Gemini is down. It
    must use the same neutral label as the prompt."""

    def test_fallback_does_not_label_as_fraser(self):
        out = _structured_fallback(
            user_message="design me a workout",
            facts={},
            arbitration=None,
            fraser_text="3 rounds: 10 squats, 10 burpees",
        )
        assert "fraser:" not in out.lower(), (
            "Structured fallback leaks 'fraser:' label. When Gemini is "
            "unavailable this string is exactly what the user sees."
        )

    def test_fallback_uses_neutral_workout_label(self):
        out = _structured_fallback(
            user_message="design me a workout",
            facts={},
            arbitration=None,
            fraser_text="3 rounds: 10 squats, 10 burpees",
        )
        assert "workout:" in out.lower()


# ─── Bug 2: OVERPRODUCTION ─────────────────────────────────────────────

class TestPromptEnforcesLengthMatching:
    """Terse questions should not get multi-section workout dumps."""

    def test_prompt_has_length_matching_rule(self):
        # The prompt must explicitly tell Gemini to match length.
        cues = [
            "Match length",
            "match length",
            "one-line answer",
            "one line answer",
        ]
        assert any(c in SYSTEM_PROMPT for c in cues), (
            "SYSTEM_PROMPT does not enforce length-matching; this is why "
            "the live bot at 21:08 dumped a 4-section workout template "
            "(Part 1/2/3/4) in response to a goal-math question."
        )

    def test_prompt_blocks_unsolicited_workout_dumps(self):
        # The rule should explicitly say not to dump a workout unless
        # the user asked for one.
        cues = [
            "unless the user explicitly asked",
            "No bullet-list workouts unless",
            "Do NOT dump",
            "do not dump",
        ]
        assert any(c in SYSTEM_PROMPT for c in cues), (
            "SYSTEM_PROMPT does not block unsolicited workout dumps."
        )


# ─── Bug 3: HYPOTHETICAL MISHANDLING ───────────────────────────────────

class TestPromptHandlesHypotheticals:
    """'Assume X' / 'What if Y' should not trigger plan changes."""

    def test_prompt_has_hypothetical_rule(self):
        cues = [
            "Hypothetical",
            "hypothetical",
            "Conditional",
            "conditional",
            "assume",
        ]
        assert any(c in SYSTEM_PROMPT for c in cues), (
            "SYSTEM_PROMPT has no rule for hypothetical / conditional "
            "asks. The 21:08 live bot took 'Assume I need to hit 196' "
            "literally and offered to 'officially update your plan'."
        )

    def test_prompt_forbids_officially_updating_plans(self):
        # Match either capitalization; the prompt's phrasing may shift
        # but the intent must be there.
        prompt_lower = SYSTEM_PROMPT.lower()
        cues = [
            "do not invent conflicts",
            'do not "officially update"',
            "do not officially update",
            "never say \"officially update",
        ]
        assert any(c in prompt_lower for c in cues), (
            "SYSTEM_PROMPT does not block 'officially update the plan' "
            "phrasing — the live bot shipped that exact phrase."
        )


# ─── Operational: 409 duplicate-bot symptom (documented, not enforced) ─

class TestKnownOperationalIssues:
    """Document the 409 Telegram conflicts for the runbook. Not a code
    bug — it's two processes polling the same token. The fix is
    operational: `pkill -f new_plane.miya_runner` then a single restart.
    """

    @pytest.mark.skip(reason="operational, not a code bug; see "
                            "specs/test_lead/findings/LIVE_BUG_TRIAGE_2026-06-13.md")
    def test_409_conflict_documented(self):
        pass
