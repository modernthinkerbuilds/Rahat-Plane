"""Regression pin (2026-09-02) — a cooldown REQUEST beats design-preempt
and pain routing.

LIVE BUG (2026-09-02 6:20 PM, owner screenshot): "I did today's WOD
and my right hip feels sore at the catch , give me a good Streching
routine" → Bade Miya dumped the day's WOD programming. Two misses in
one message:
  1. "Streching" (phone keyboard) missed COOLDOWN_RE's `stretch`.
  2. Even spelled right, "give me a" + "WOD" fired the step-4b
     design-preempt → orchestrate → the WOD lookup answered. The
     cooldown rung (6b) sat too far down the ladder to ever see it.
The athlete asked for a stretch; the WOD mention was context and the
soreness was steering.

THE PINS.
  * COOLDOWN_REQUEST_RE (agents.huberman.handler): request verb →
    ≤5 non-workout words → cooldown noun. The classifier consults it
    at 4a½, AHEAD of design-preempt (4b) and pain (6).
  * Neighbors keep their routes: "design me a workout with stretching
    at the end" still orchestrates (workout noun between verb and
    stretch); "my hip hurts when stretching" stays a Kobe pain report
    (no request verb); WOD lookups, breathing, breakdowns untouched.
  * Typo tolerance: stre?t?ch — "streching" / "strech" count.
  * End to end: native_client.huberman_route answers the live message
    with a cooldown whose hip work honors the GTPS avoid-tag.
"""
from __future__ import annotations

import json

import pytest

from new_plane.miya_runner.delegate_classifier import classify_delegation

_LIVE = ("I did today's WOD and my right hip feels sore at the catch , "
         "give me a good Streching routine")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    from agents.huberman import state
    state.profile_path().write_text(json.dumps({
        "default_minutes": 15,
        "hotspots": [{"area_tag": "neck", "label": "neck"},
                     {"area_tag": "glutes", "label": "glute crease"},
                     {"area_tag": "foot", "label": "foot arch"}],
        "equipment": ["peanut ball", "lacrosse ball", "foam roller",
                      "green Rogue band", "door hip anchor strap"],
        "issues": [{"label": "right-hip GTPS", "status": "~99% resolved",
                    "rule": "no direct compression on the trochanter",
                    "avoid_tags": ["trochanter_compression"]}],
    }))
    return tmp_path


# ── the live message, verbatim ────────────────────────────────────────
def test_live_message_routes_to_huberman_not_the_wod_lookup():
    assert classify_delegation(_LIVE)[0] == "huberman_route"


@pytest.mark.parametrize("msg", [
    "need a cooldown after the wod",
    "can you give me a 10 min stretch",
    "I did the wod, give me a good strech routine",         # typo
    "suggest some mobility work for my hips after today's session",
])
def test_cooldown_requests_win_even_with_workout_words_around(msg):
    assert classify_delegation(msg)[0] == "huberman_route"


# ── neighbors keep their routes ───────────────────────────────────────
@pytest.mark.parametrize("msg,path", [
    ("design me a workout with stretching at the end", "orchestrate"),
    ("my hip hurts when stretching", "kobe_route"),         # pain report
    ("what is tomorrows WOD", "kobe_route"),
    ("box breathing please", "kobe_route"),
    ("give me a day-by-day burn breakdown", "kobe_route"),
    ("design tomorrows workout around last week", "orchestrate"),
])
def test_neighboring_rungs_are_untouched(msg, path):
    assert classify_delegation(msg)[0] == path


def test_request_regex_shape():
    from agents.huberman.handler import COOLDOWN_REQUEST_RE, COOLDOWN_RE
    assert COOLDOWN_REQUEST_RE.search("give me a good Streching routine")
    assert COOLDOWN_RE.search("streching")                  # typo tolerated
    # Workout noun between verb and stretch → not a cooldown request.
    assert not COOLDOWN_REQUEST_RE.search(
        "design me a workout with stretching at the end")
    # No request verb → not a request (pain report territory).
    assert not COOLDOWN_REQUEST_RE.search("my hip hurts when stretching")


# ── end to end: Huberman answers, hip-aware, GTPS-safe ────────────────
def test_native_client_answers_the_live_ask_with_a_stretch(env):
    from new_plane.miya_runner import native_client
    res = native_client.huberman_route(_LIVE)
    assert res.ok and res.result["path"] == "huberman_route"
    text = res.result["text"]
    assert "cooldown" in text.lower() and "min —" in text
    assert "WOD:" not in text and "EMOM" not in text        # not the WOD dump
    assert "glute/deep rotator" not in text                 # trochanter rule
    assert any(s in text for s in ("Couch stretch", "Pigeon", "90/90",
                                   "hip distraction", "hip abduction"))
