"""Regression: Fraser's parse_request must not flag avoided moves as focus (2026-05-25).

Symptom: `parse_request` used plain substring matching, so a message
listing movements the user wanted to AVOID flagged them as the FOCUS:
  "I've already done deadlifts and back squats, don't have those"
  → preferences = ['deadlift_focus', 'squat_focus']   (exactly backwards)
And overlapping needles double-counted: "no running" matched both
("no run", …) and ("no running", …) → ['no_running', 'no_running'].

Fix: drop positive "focus" inference entirely (the LLM reads the
verbatim message and infers focus far better than substring regex), and
keep only self-contained "no X" avoid-flags, word-boundary matched and
de-duplicated by construction.

Pins:
  1. A message listing already-done movements yields NO *_focus flags.
  2. "no running … no run" yields exactly one no_running (no dupes).
  3. A bare "bench focus" no longer fabricates a focus flag (the design
     decision: focus is the LLM's job).
  4. A genuine avoid ("no rowing") is still captured.
"""
from __future__ import annotations

from agents.fraser import composer


def test_negated_movements_are_not_flagged_as_focus():
    req = composer.parse_request(
        "I've already done deadlifts and back squats, don't have those, "
        "not even running")
    assert all(not p.endswith("_focus") for p in req.preferences), (
        f"avoided movements were flagged as focus: {req.preferences}")
    assert "deadlift_focus" not in req.preferences
    assert "squat_focus" not in req.preferences


def test_no_duplicate_avoid_flags():
    req = composer.parse_request("no running please, and no run today either")
    assert req.preferences.count("no_running") == 1, req.preferences


def test_focus_inference_removed():
    """Positive focus is no longer inferred from bare movement words —
    it's the LLM's job (it receives the raw text)."""
    req = composer.parse_request("bench focus, heavy squats today")
    assert not any(p.endswith("_focus") for p in req.preferences), \
        req.preferences


def test_genuine_avoid_still_captured():
    assert "no_rowing" in composer.parse_request("no rowing today").preferences
    assert "no_running" in composer.parse_request("no running").preferences
    # And minutes/kcal extraction is untouched.
    req = composer.parse_request("45 min session, burn 800 kcal, no biking")
    assert req.minutes == 45
    assert req.kcal_target == 800
    assert "no_biking" in req.preferences
