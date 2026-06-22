"""Regression: gym WODs were shown un-scaled to the athlete's setup.

Bug (2026-06-14)
----------------
"What is the WOD for tomorrow" returned the gym's programming verbatim (or
paraphrased), but never translated it to the athlete's actual setup — no
pull-up rig, no med ball, no jump rope — so he had to do the substitution
math in his head every time.

Fix: handler._scale_wod_for_profile() appends a deterministic
'Scaled for you' block derived from core.athlete_profile's fixed
equipment/blacklist substitutions. No LLM; pure profile data.
"""
from __future__ import annotations

from agents.the_scientist import handler as kobe


def test_scale_block_translates_unavailable_movements():
    body = (
        "21-15-9 Wall Balls and Pull-Ups, then 100 Double Unders, "
        "finish with Bar Muscle-Ups."
    )
    out = kobe._scale_wod_for_profile(body)
    assert "Scaled for you" in out
    assert "Wall Balls" in out and "Thrusters" in out          # no med ball
    assert "Pull-Ups" in out and "Row" in out                  # no rig
    assert "Double Unders" in out and ("Bike" in out or "Penguin" in out)  # no rope
    # Standing cues always present.
    assert "Heel lift" in out


def test_scale_block_present_even_when_no_subs_needed():
    body = "5x5 Back Squat then 3 rounds: 15 cal Row, 10 KB Swings."
    out = kobe._scale_wod_for_profile(body)
    assert "Scaled for you" in out
    assert "Heel lift" in out          # standing cues still surface


def test_scale_block_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RAHAT_WOD_SCALE", "0")
    assert kobe._scale_wod_for_profile("21 Wall Balls") == ""


def test_scale_block_never_raises_on_empty():
    assert kobe._scale_wod_for_profile("") == ""
