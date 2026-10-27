"""Regression pin (2026-10-27) — "How many steps do I average in the
last 4 months?" came back as the question itself.

LIVE (Oct 27, 16:21, twice): Miya replied "new_miya How many steps do I
average in the last 4 months ?". The Gemini project had hit its monthly
spending cap (429 RESOURCE_EXHAUSTED since Oct 25), the synthesis call
failed, and the synthesizer shipped its offline debug shape,
"[new_miya] <the user's message>", to the phone. Two faults under it:

  1. A steps average needs no model: it is a sum over the Health records.
     The question went to the synthesizer, which has no step history.
  2. The live floor echoed the user instead of saying what was wrong.
     The same floor also replaced a specialist's answer whenever
     re-voicing it failed.

THE PINS:
  * a steps question goes to Kobe, whose dispatcher answers it from
    raw_vitals (daily totals, today left out, the coverage stated when
    the records start later than asked); what-ifs, statements of his own
    numbers and "step-ups" stay where they were;
  * when the model call fails, the reply says so in plain words (the
    spend cap by name), keeps the turn's facts, and never echoes the
    message; offline (tests, CI) keeps the comparable structured shape;
  * re-voicing a specialist's answer that fails keeps the answer.
Invented step counts.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

_NOW = datetime(2026, 10, 27, 16, 21)
_Q = "How many steps do I average in the last 4 months ?"


def _seed_steps(db, first=date(2026, 8, 9), last=date(2026, 10, 27), per_day=8000.0, today_so_far=400.0):
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT)")
    d = first
    while d <= last:
        if d == last:
            con.execute("INSERT INTO raw_vitals VALUES ('steps', ?, ?)", (today_so_far, f"{d} 07:19:00"))
        else:
            for h in (8, 12, 16, 20):
                con.execute("INSERT INTO raw_vitals VALUES ('steps', ?, ?)", (per_day / 4, f"{d} {h:02d}:00:00"))
        d += timedelta(days=1)
    con.commit()
    con.close()


def test_a_steps_question_goes_to_kobe_and_his_dispatcher_owns_it():
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    from core import dispatcher
    for q in (_Q, "how many steps today", "average steps last week", "what's my step count this month"):
        assert classify_delegation(q)[0] == "kobe_route", q
        assert dispatcher.match_route(q) == "steps", q
    for q in ("If I need 10000 steps a day this year, how many steps a day should I do",
              "My average so far is 800 calories a day and 9601 steps a day",
              "How about box step ups and ball slams",
              "Give me the WOD step by step"):
        assert dispatcher.match_route(q) != "steps", q
        assert classify_delegation(q)[0] != "kobe_route" or dispatcher.match_route(q) != "steps", q


def test_the_average_comes_from_the_records(_hermetic_env):
    _seed_steps(_hermetic_env)
    from agents.the_scientist import steps
    out = steps.answer(_Q, now=_NOW, db_path=str(_hermetic_env))
    assert out.startswith("*Steps — the last 4 months:* 8,000 a day on average.")
    assert "start Aug 9" in out and "not the full 4 months" in out          # says what it covers
    assert "By month: Aug 8,000 · Sep 8,000 · Oct 8,000 so far." in out
    assert "Today isn't counted" in out                                      # 400 so far is not a day
    today = steps.answer("how many steps today", now=_NOW, db_path=str(_hermetic_env))
    assert today == "*Steps today:* 400 so far (last synced at 07:19)."
    assert steps.answer("steps yesterday?", now=_NOW, db_path=str(_hermetic_env)) == "*Steps yesterday:* 8,000."
    sep = steps.answer("How many steps did I do last month", now=_NOW, db_path=str(_hermetic_env))
    assert sep.startswith("*Steps — September:* 8,000") and "Today isn't counted" not in sep


def test_no_records_says_so(_hermetic_env):
    from agents.the_scientist import steps
    con = sqlite3.connect(str(_hermetic_env))
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, value REAL, timestamp TEXT)")
    con.close()
    assert steps.answer(_Q, now=_NOW, db_path=str(_hermetic_env)) == "I have no step records for the last 4 months."


def test_kobes_route_answers_it_without_a_model(_hermetic_env, monkeypatch):
    _seed_steps(_hermetic_env, last=date.today())
    from agents.the_scientist import handler
    out = handler.route(_Q)
    assert "a day on average" in out and "8,000" in out


_CAP = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Your project has exceeded its "
        "monthly spending cap. Please go to AI Studio at https://ai.studio/spend to manage your project "
        "spend cap.', 'status': 'RESOURCE_EXHAUSTED'}}")


def test_over_the_spend_cap_the_reply_says_so_and_never_echoes():
    from new_plane.miya_runner import synthesizer
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError(_CAP)
    facts = {"pace": {"result": {"summary": "120 of 600 kcal by 2 pm"}}}
    with patch.object(synthesizer, "_client", return_value=client):
        r = synthesizer.synthesize(user_message="What should I eat after the WOD?", facts=facts,
                                   arbitration=None, user_profile_block="")
    assert r.fallback is True
    assert "monthly spending cap" in r.text and "AI Studio" in r.text
    assert "What should I eat" not in r.text and "new_miya" not in r.text
    assert "Pace: 120 of 600 kcal by 2 pm" in r.text
    other = synthesizer.model_unavailable_line("RuntimeError: deadline exceeded")
    assert "Try again" in other and "cap" not in other


def test_offline_keeps_the_structured_shape(monkeypatch):
    from new_plane.miya_runner import synthesizer
    monkeypatch.setattr(synthesizer, "_GEMINI_CLIENT", None)
    r = synthesizer.synthesize(user_message="hi", facts={}, arbitration=None, user_profile_block="")
    assert "[new_miya]" in r.text                                            # RAHAT_TEST_MODE=1
    monkeypatch.setenv("RAHAT_TEST_MODE", "0")
    r = synthesizer.synthesize(user_message="hi", facts={}, arbitration=None, user_profile_block="")
    assert "[new_miya]" not in r.text and "hi" != r.text and "no API key" in r.text


def test_a_failed_revoice_keeps_the_specialists_answer(monkeypatch):
    from new_plane.miya_runner import orchestrator, synthesizer
    monkeypatch.setattr(synthesizer, "synthesize", lambda **kw: synthesizer.SynthesisResult(
        text="I can't reach my AI model right now", model="gemini", fallback=True, error=_CAP))
    text, meta = orchestrator._revoice_through_synth(
        raw_text="Rest tomorrow; Thursday is the long session.", user_message="what is next", delegation_path="kobe_route",
        trace_id="t", chat_id=None)
    assert text == "Rest tomorrow; Thursday is the long session." and meta["revoice"] == "fallback-kept-raw"
