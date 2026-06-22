"""Regression: live smoke-test phrasings (2026-06-14) fell through.

From the live Bade Miya smoke test:
  - "Whats the plan for next week ?"  (apostrophe-less) → dispatch was None,
    so it fell to the reasoner/synth and got reworded inconsistently.
  - "Whats the workout for Tuesday"   → not matched by the gym-WOD-day route
    (only "wod"/"gym" were accepted, not "workout"), so it answered
    "no workout on file".

Fix: the dispatcher's _PLAN_*/_GYM_WOD_DAY_RE regexes now accept the
apostrophe-less "whats" and the noun "workout"/"session"/"programming".
(Separately, the orchestrator now ships deterministic dispatcher answers
verbatim instead of re-voicing them through the synth.)
"""
from __future__ import annotations

import pytest

from core import dispatcher
from new_plane.miya_runner.delegate_classifier import classify_delegation


@pytest.mark.parametrize("msg,route", [
    ("Whats the plan for next week ?", "show_plan_next_week"),
    ("what's the plan for next week", "show_plan_next_week"),
    ("whats my plan for next week", "show_plan_next_week"),
    ("Whats the plan for the week", "show_plan_this_week"),
    ("whats the workout for Tuesday", "gym_wod_on_day"),
    ("what's the workout for friday", "gym_wod_on_day"),
])
def test_apostrophe_less_phrasings_now_dispatch(msg, route):
    assert dispatcher.match_route(msg) == route


@pytest.mark.parametrize("msg", [
    "Whats the plan for next week ?",
    "whats the workout for Tuesday",
])
def test_live_phrasings_route_to_kobe_not_synth(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route"


def test_deterministic_routes_are_recognized_for_revoice_skip():
    # The orchestrator skips synth re-voicing when match_route is not None.
    # These three (the live failures) must all be recognized as deterministic.
    assert dispatcher.match_route("my back squat max is 120 kg") == "one_rm_set"
    assert dispatcher.match_route("Whats the plan for next week ?") == "show_plan_next_week"
    assert dispatcher.match_route("whats the workout for Tuesday") == "gym_wod_on_day"


def test_deterministic_kobe_output_ships_verbatim_not_revoiced(monkeypatch):
    """The live bug: a deterministic /profile-set confirmation got re-voiced
    by the synth into a hallucination ("up from 102"). Now deterministic
    dispatcher answers must ship verbatim — synth must NOT be called."""
    import os
    os.environ["RAHAT_TEST_MODE"] = "1"
    from new_plane.miya_runner.orchestrator import Turn, handle

    monkeypatch.setenv("NEW_MIYA_REVOICE", "1")
    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.classify_delegation",
        lambda msg: ("kobe_route", msg),
    )

    class _R:
        ok = True; transport_error = None; error = None
        result = {"text": "✅ Updated *back squat* 1RM → *120 kg* (265 lbs)."}

    monkeypatch.setattr(
        "new_plane.miya_runner.orchestrator.adapter.kobe_route",
        lambda msg, chat_id=None, trace_id=None: _R(),
    )

    called = {"n": 0}
    from new_plane.miya_runner import synthesizer as _synth
    def _spy(**kw):
        called["n"] += 1
        class _Res:
            text = "REVOICED — should not appear"; model = "spy"
            fallback = False; error = None; prompt_tokens = 0; output_tokens = 0
        return _Res()
    monkeypatch.setattr(_synth, "synthesize", _spy)

    # "my back squat max is 120 kg" is a deterministic route (one_rm_set).
    resp = handle(Turn(user_message="my back squat max is 120 kg", chat_id="t1"))
    assert called["n"] == 0, "synth was called on a deterministic answer (should ship verbatim)"
    assert "120 kg" in resp.text and "REVOICED" not in resp.text
