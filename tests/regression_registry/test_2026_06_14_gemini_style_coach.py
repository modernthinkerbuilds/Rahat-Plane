"""Gemini-style coach: full scaled session in the athlete's house style.

The default composer returns a tight 4-section session. With
RAHAT_COACH_GEMINI_STYLE=1, composer.build_design_prompt swaps in the
richer COACH_SYSTEM directive + COACH_SCHEMA (Cash-In → Warm-up →
Strength w/ exact weights → Metcon → Recovery), with the athlete's
cardio-caution / neck / ankle / hunch guardrails and equipment subs baked in.

Default behavior (flag off) must be unchanged. No new LLM call path is
introduced — only the prompt changes.
"""
from __future__ import annotations

from agents.fraser import coach
from agents.fraser import composer


# ── Directive content ─────────────────────────────────────────────────
def test_coach_directive_encodes_guardrails_and_subs():
    sys = coach.COACH_SYSTEM
    assert "Valsalva" in sys                 # cardio-caution: no breath-holding
    assert "EYE LEVEL" in sys                # neck-safe KB swings
    assert "heel lift" in sys.lower()        # tight ankles
    assert "DISLIKES lateral" in sys or "lateral line hops" in sys  # his stated dislike
    assert "Goblet Thrusters" in sys         # no-med-ball sub
    assert "kg and lbs" in sys.lower() or "kg AND lbs" in sys       # weight math


def test_coach_schema_has_house_section_structure():
    s = coach.COACH_SCHEMA
    for header in ("Thermal Cash-In", "Dynamic Warm-Up", "Strength",
                   "Metcon", "Recovery & CNS"):
        assert header in s, f"missing house-style section: {header}"


def test_flag_default_off():
    import os
    os.environ.pop(coach.FLAG, None)
    assert coach.gemini_style_enabled() is False


def test_flag_on_off(monkeypatch):
    monkeypatch.setenv(coach.FLAG, "1")
    assert coach.gemini_style_enabled() is True
    monkeypatch.setenv(coach.FLAG, "0")
    assert coach.gemini_style_enabled() is False


# ── Composer wiring (same grounding, swapped style) ───────────────────
def test_prompt_uses_coach_style_when_flag_on(monkeypatch, sandbox_db):
    monkeypatch.setenv(coach.FLAG, "1")
    req = composer.parse_request("design me a 75 minute session, burn 800 cal")
    prompt = composer.build_design_prompt(req, db_path=str(sandbox_db))
    assert "You are Miya" in prompt              # coach voice
    assert "NON-NEGOTIABLE SAFETY GUARDRAILS" in prompt
    assert "Thermal Cash-In" in prompt           # house schema present
    assert "You are Fraser" not in prompt        # old directive swapped out


def test_prompt_unchanged_when_flag_off(monkeypatch, sandbox_db):
    monkeypatch.setenv(coach.FLAG, "0")
    req = composer.parse_request("design me a session")
    prompt = composer.build_design_prompt(req, db_path=str(sandbox_db))
    assert "You are Fraser" in prompt            # original directive intact
    assert "NON-NEGOTIABLE SAFETY GUARDRAILS" not in prompt


def test_design_session_uses_one_llm_call_with_coach_prompt(monkeypatch, sandbox_db, fake_llm):
    """End-to-end wiring: flag on, design_session still makes exactly one
    LLM call and returns its text. Proves no new call path was added."""
    monkeypatch.setenv(coach.FLAG, "1")

    seen = {}
    from core import io as cio

    def _capture(prompt, *, model=None):
        seen["prompt"] = prompt
        seen["calls"] = seen.get("calls", 0) + 1
        return "## Part 1: The Thermal Cash-In (10 min)\n1-Mile easy run."

    monkeypatch.setattr(cio, "llm_generate", _capture)

    out = composer.design_session("design me a 75 min session burn 800 cal",
                                  db_path=str(sandbox_db))
    assert seen["calls"] == 1                              # exactly one call
    assert "NON-NEGOTIABLE SAFETY GUARDRAILS" in seen["prompt"]  # coach prompt used
    assert "Thermal Cash-In" in out
