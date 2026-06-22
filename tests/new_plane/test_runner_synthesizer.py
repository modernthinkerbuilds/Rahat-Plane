"""Synthesizer tests — prompt assembly + fallback contract.

Real Gemini is mocked. Fallback path is the offline equivalent that
matches the simulator output so a sandbox CI without API keys still
exercises real behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from new_plane.miya_runner import synthesizer
from new_plane.miya_runner.synthesizer import (
    SynthesisResult, _build_prompt, _structured_fallback, synthesize,
)


# ─── prompt assembly ───────────────────────────────────────────────────

def test_build_prompt_includes_system_and_user():
    p = _build_prompt(user_message="hi", facts={}, arbitration=None,
                      fraser_text=None, recent_signals=None)
    assert "Miya" in p
    # Voice-leak fix 2026-06-13: prompt's leading rule is "ONE voice"
    # (replaces the old "single coherent voice over a team of
    # specialists" phrasing which named the specialists).
    assert "ONE voice" in p or "one voice" in p.lower()
    assert 'User said: "hi"' in p


def test_build_prompt_includes_arbitration_when_set():
    p = _build_prompt(
        user_message="x", facts={},
        arbitration={"rule": "behind_pace", "guidance": "Be honest."},
        fraser_text=None, recent_signals=None,
    )
    assert "ARBITRATION VERDICT: behind_pace" in p
    assert "Be honest." in p


def test_build_prompt_includes_facts_with_summary():
    facts = {
        "active_goal": {"result": {"active": True, "target_lbs": 196,
                                   "summary": "196 lbs by Sep 1"}},
        "recalibration": {"result": {"behind_pace": True,
                                     "summary": "Behind by 5500 kcal"}},
    }
    p = _build_prompt(user_message="x", facts=facts, arbitration=None,
                      fraser_text=None, recent_signals=None)
    assert "196 lbs by Sep 1" in p
    assert "Behind by 5500 kcal" in p


def test_build_prompt_includes_facts_without_summary():
    """Facts that have no `summary` key fall back to dumping the dict."""
    facts = {"active_goal": {"result": {"active": False, "reason": "none"}}}
    p = _build_prompt(user_message="x", facts=facts, arbitration=None,
                      fraser_text=None, recent_signals=None)
    assert "active_goal" in p
    assert "active" in p


def test_build_prompt_includes_workout_draft():
    """Voice-leak fix 2026-06-13: label is now 'WORKOUT DRAFT' not
    'FRASER'S DRAFT' so Gemini can't parrot the specialist name."""
    p = _build_prompt(user_message="design me a wod",
                      facts={}, arbitration=None,
                      fraser_text="5 rounds for time",
                      recent_signals=None)
    assert "WORKOUT DRAFT" in p
    assert "FRASER'S DRAFT" not in p, (
        "label leaked 'FRASER' — Gemini will parrot 'Fraser's design...'"
    )
    assert "5 rounds for time" in p


def test_build_prompt_includes_recent_signals_capped_at_5():
    sigs = [{"agent": f"a{i}", "type": "t", "payload": {}} for i in range(10)]
    p = _build_prompt(user_message="x", facts={}, arbitration=None,
                      fraser_text=None, recent_signals=sigs)
    assert "RECENT CROSS-AGENT SIGNALS" in p
    assert "a0.t" in p
    assert "a4.t" in p
    assert "a5.t" not in p  # capped at 5


# ─── fallback ──────────────────────────────────────────────────────────

def test_structured_fallback_returns_user_message():
    out = _structured_fallback("hi", {}, None, None)
    assert "[new_miya]" in out
    assert "hi" in out


def test_structured_fallback_includes_arbitration():
    out = _structured_fallback("x", {}, {"rule": "r", "guidance": "g"}, None)
    assert "arbitration: r — g" in out


def test_structured_fallback_skips_none_facts():
    out = _structured_fallback("x",
        {"active_goal": None, "recalibration": {"result": {"summary": "abc"}}},
        None, None)
    assert "active_goal" not in out
    assert "abc" in out


# ─── synthesize() with no API key uses fallback ───────────────────────

def test_synthesize_without_api_key_uses_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(synthesizer, "_GEMINI_CLIENT", None)
    r = synthesize(user_message="hi", facts={}, arbitration=None)
    assert r.fallback is True
    assert r.model == "fallback-structured"
    assert "[new_miya]" in r.text


# ─── synthesize() with mocked client returns model output ─────────────

def test_synthesize_with_mock_client_returns_text():
    mock_resp = MagicMock()
    mock_resp.text = "Miya's synthesized response."
    mock_resp.usage_metadata = MagicMock(
        prompt_token_count=120, candidates_token_count=40,
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch.object(synthesizer, "_client", return_value=mock_client):
        r = synthesize(user_message="x", facts={}, arbitration=None,
                       model="gemini-2.5-flash")
        assert r.fallback is False
        assert r.text == "Miya's synthesized response."
        assert r.prompt_tokens == 120
        assert r.output_tokens == 40
        # Verify the prompt was passed to the model
        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == "gemini-2.5-flash"
        assert "User said:" in call_kwargs.kwargs["contents"]


def test_synthesize_empty_response_falls_back():
    mock_resp = MagicMock()
    mock_resp.text = ""
    mock_resp.usage_metadata = None
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch.object(synthesizer, "_client", return_value=mock_client):
        r = synthesize(user_message="hi", facts={}, arbitration=None)
        assert r.fallback is True
        assert r.error == "empty-response"


def test_synthesize_exception_falls_back():
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("rate-limit")

    with patch.object(synthesizer, "_client", return_value=mock_client):
        r = synthesize(user_message="hi", facts={}, arbitration=None)
        assert r.fallback is True
        assert "RuntimeError" in (r.error or "")
        assert "rate-limit" in (r.error or "")
