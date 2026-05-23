"""Regression: /pain and /profile slash commands (2026-05-23).

What was missing
----------------
The Fraser composer already READ active pain (core.pain_state) and the
athlete profile (core.athlete_profile) on every design, but there was no
INPUT path: the user could not report a tweak or correct a 1RM from
Telegram. So pain adaptation never fired (the active-pain list was always
empty) and the 1RMs were frozen in code.

This adds two args-bearing slash commands to Kobe's single slash table
(core.dispatcher → handler._try_slash_command):

    /pain <where> [mild|moderate|sharp|severe]   → pain_state.report
    /pain                                         → pain_state.list_active
    /pain clear <where>                           → pain_state.clear
    /profile                                      → render profile
    /profile set <lift> <kg>                      → athlete_profile.set_one_rm

These tests pin the dispatch wiring AND that the write actually reaches
the read path the composer depends on (has_pain_at / merged 1RMs).
"""
from __future__ import annotations

import pytest

from agents.the_scientist import handler as h
from core import pain_state, athlete_profile


@pytest.fixture(autouse=True)
def _reset_profile_cache():
    """athlete_profile caches the merged profile in a module global.
    Reset around each test so a /profile set in one test can't leak
    into the next (each test already gets a fresh temp DB)."""
    athlete_profile.reset()
    yield
    athlete_profile.reset()


# ─────────────────────────── /pain ──────────────────────────────────
class TestPainCommand:
    def test_report_then_active_then_clear(self, bootstrap_substrate):
        # No pain yet.
        out = h._try_slash_command("/pain")
        assert "No active pain" in out

        # Report with explicit severity.
        out = h._try_slash_command("/pain left shoulder sharp")
        assert "left shoulder" in out and "sharp" in out
        assert pain_state.has_pain_at("shoulder"), (
            "/pain must persist to the SAME store the composer reads — "
            "otherwise the pain adaptation never fires.")

        # List shows it.
        out = h._try_slash_command("/pain")
        assert "left shoulder" in out

        # Clear it.
        out = h._try_slash_command("/pain clear left shoulder")
        assert "Cleared" in out
        assert not pain_state.has_pain_at("shoulder")

    def test_report_defaults_to_mild_when_no_severity(self, bootstrap_substrate):
        h._try_slash_command("/pain lower back")
        active = pain_state.list_active()
        assert any(p.location == "lower back" and p.severity == "mild"
                   for p in active)

    def test_multiword_location_preserved(self, bootstrap_substrate):
        h._try_slash_command("/pain right outer knee moderate")
        active = pain_state.list_active()
        assert any(p.location == "right outer knee" for p in active)

    def test_painful_does_not_trigger_pain(self, bootstrap_substrate):
        """First-token exact match: '/painful' is NOT '/pain'. It must
        fall through (return None) so it isn't logged as pain."""
        assert h._try_slash_command("/painful") is None


# ─────────────────────────── /profile ───────────────────────────────
class TestProfileCommand:
    def test_view_shows_known_1rms(self, bootstrap_substrate):
        out = h._try_slash_command("/profile")
        assert "deadlift" in out
        assert "155" in out, "default deadlift 1RM (155 kg) must surface"

    def test_set_persists_and_merges_into_profile(self, bootstrap_substrate):
        out = h._try_slash_command("/profile set deadlift 170")
        assert "170" in out
        merged = athlete_profile.get(refresh=True)
        assert merged.one_rms["deadlift"] == 170.0, (
            "/profile set must persist a 1RM override that get() merges, "
            "so every weight Fraser computes uses the corrected max.")

    def test_slash_caps_weight_at_three_digits(self, bootstrap_substrate):
        # The slash regex accepts at most a 3-digit kg value, so a 4-digit
        # fat-finger ("5000") never reaches set_one_rm — it returns the
        # usage hint and leaves the 1RM untouched.
        out = h._try_slash_command("/profile set deadlift 5000")
        assert "Updated" not in out
        assert athlete_profile.get(refresh=True).one_rms["deadlift"] == 155.0

    def test_set_one_rm_api_validates_range(self, bootstrap_substrate):
        # Belt-and-suspenders: the underlying API rejects out-of-range
        # weights even when called directly.
        with pytest.raises(ValueError):
            athlete_profile.set_one_rm("deadlift", 5000)
        with pytest.raises(ValueError):
            athlete_profile.set_one_rm("deadlift", 0)

    def test_set_then_view_reflects_new_value(self, bootstrap_substrate):
        h._try_slash_command("/profile set back_squat 110")
        out = h._try_slash_command("/profile")
        assert "110" in out

    def test_profiler_does_not_trigger_profile(self, bootstrap_substrate):
        assert h._try_slash_command("/profiler") is None
