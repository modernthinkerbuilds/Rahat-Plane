"""Regression: voice time-greeting + profile render (2026-05-24, ADR-011 P0).

Two live bugs from 2026-05-23:

1. A workout designed at 2:30 PM opened with "🌙 9pm check / raat hai".
   core/voice._classify tagged ANY message containing "recovery"/"sleep"/🌙
   as the night check-in — and every Fraser session has a cool-down /
   "recovery" section. Fix: the recovery pattern now matches only the real
   "9pm check" marker, AND a content-GUESSED time-of-day greeting must agree
   with the real local clock (explicit kind= from a scheduler stays trusted).

2. /profile rendered "backsquat" / "snatch in strength" wrong because Markdown
   italicizes a lone underscore in "back_squat". Fix: render with spaces.
"""
from __future__ import annotations

import pytest

from core import voice
from agents.the_scientist import handler as h


_SESSION = ("## Part 1: Warm-up\n...\n## Part 4: Cool-down (15 min)\n"
            "- Legs up the wall, recovery breathing\n### Coach's Note\nGo.")


class TestVoiceTimeGreeting:
    @pytest.fixture(autouse=True)
    def _voice_on(self, monkeypatch):
        # The suite runs voice in 'neutral' by default; force the
        # Hyderabadi voice so dress() actually applies greetings here.
        monkeypatch.setenv("RAHAT_VOICE", "hyderabadi")

    def test_workout_cooldown_is_not_classified_recovery(self):
        # The narrowed pattern: a cool-down/"recovery" body is NOT the 9pm
        # check-in, so it must not classify as the night greeting kind.
        assert voice._classify(_SESSION) != "recovery"

    def test_workout_session_gets_no_night_greeting(self):
        out = voice.dress(_SESSION)
        assert "9pm" not in out and "raat" not in out and "🌙" not in out

    def test_explicit_recovery_kind_is_trusted(self):
        # A scheduler that KNOWS it's the 9pm check passes kind explicitly;
        # that bypasses the clock gate and always gets the night greeting.
        out = voice.dress("Active rest. 324 / 600 kcal.", kind="recovery")
        assert "9pm" in out or "raat" in out or "🌙" in out

    def test_auto_time_greeting_downgraded_when_clock_disagrees(self, monkeypatch):
        # Content guesses "recovery" (body has '9pm check'), but the clock
        # says it's not night → downgrade to a neutral opener.
        monkeypatch.setattr(voice, "_clock_matches", lambda kind: False)
        out = voice.dress("9pm check\nActive rest. 324 / 600 kcal.")
        assert "raat" not in out  # no night closer/opener

    def test_auto_time_greeting_kept_when_clock_agrees(self, monkeypatch):
        monkeypatch.setattr(voice, "_clock_matches", lambda kind: True)
        out = voice.dress("9pm check\nActive rest. 324 / 600 kcal.")
        assert out.splitlines()[0].startswith("🌙")

    @pytest.mark.parametrize("kind,hour,ok", [
        ("morning", 6, True), ("morning", 15, False),
        ("recovery", 22, True), ("recovery", 14, False),
        ("status", 14, True),  # non-time kinds always ok
    ])
    def test_clock_matches_windows(self, kind, hour, ok, monkeypatch):
        import datetime as _dt
        class _FixedNow(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 5, 24, hour, 0, 0)
        monkeypatch.setattr(voice.datetime, "datetime", _FixedNow)
        assert voice._clock_matches(kind) is ok


class TestProfileRender:
    @pytest.fixture(autouse=True)
    def _reset_profile(self):
        from core import athlete_profile
        athlete_profile.reset()
        yield
        athlete_profile.reset()

    def test_render_uses_spaces_not_underscores(self, bootstrap_substrate):
        out = h._render_profile_summary()
        assert "back squat" in out
        assert "back_squat" not in out
        # Blacklist movements too.
        assert "snatch in strength" in out
        assert "snatch_in_strength" not in out
