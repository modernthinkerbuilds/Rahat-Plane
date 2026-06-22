"""Regression: natural-language 1RM set silently no-op'd + fabricated reply.

Bug (2026-06-14, live transcript)
---------------------------------
User: "My back squat max is 120 kg"
Bot:  "Noted. Your back squat max is now 150.0 kg / 331 lbs (264.6 lbs),
       up from 150.0 kg."

Three defects in one reply:
  1. The update never persisted (stayed 102) — the NL phrasing never
     reached Kobe's `/profile set` path.
  2. The synth FABRICATED a "Noted ... up from" confirmation.
  3. Double lbs conversion ("331 lbs (264.6 lbs)" — old kg's lbs mixed
     with new kg's lbs).

Root cause: `core.dispatcher` had no route for NL 1RM sets, and
`new_plane.miya_runner.delegate_classifier` didn't route them to Kobe, so
the message fell to the orchestrate/synth path which invented an answer.

Fix: a `one_rm_set` dispatcher route + classifier recognition that
normalize NL forms into the tested `handle_profile("set <lift> <kg>")`
path, which persists and confirms correctly.
"""
from __future__ import annotations

import pytest

from core import dispatcher
from new_plane.miya_runner.delegate_classifier import classify_delegation


# ─── 1. Classifier routes NL 1RM sets to Kobe (not orchestrate/synth) ──
@pytest.mark.parametrize("msg", [
    "My back squat max is 120 kg",
    "back squat max is 120",
    "set my squat to 120",
    "my deadlift is now 160",
    "my 1rm for bench is 95",
])
def test_classifier_routes_nl_1rm_set_to_kobe(msg):
    path, _ = classify_delegation(msg)
    assert path == "kobe_route", f"{msg!r} routed to {path!r}, expected kobe_route"


# ─── 2. Dispatcher matches the one_rm_set route ────────────────────────
@pytest.mark.parametrize("msg", [
    "My back squat max is 120 kg",
    "set my squat to 120",
    "my deadlift is now 160",
])
def test_dispatcher_matches_one_rm_set(msg):
    assert dispatcher.match_route(msg) == "one_rm_set"


# ─── 3. Non-set phrasings do NOT trigger a set (no false positives) ────
@pytest.mark.parametrize("msg", [
    "I'll squat at 120 today",
    "what is my back squat max",
    "5 deadlifts at 100 kg",
])
def test_non_set_phrasings_fall_through(msg):
    # Either the coarse gate doesn't match, or the handler returns None.
    name = dispatcher.match_route(msg)
    if name == "one_rm_set":
        # Coarse gate matched — handler must decline (None) so it falls
        # through to the reasoner rather than persisting a bogus 1RM.
        result = dispatcher.dispatch(msg)
        assert result is None, f"{msg!r} wrongly handled as a 1RM set: {result!r}"


# ─── 4. The set actually persists + confirmation is correct ────────────
def test_nl_1rm_set_persists_and_confirms(sandbox_db):
    from core import athlete_profile

    reply = dispatcher.dispatch("My back squat max is 120 kg")

    assert reply is not None, "NL 1RM set should be handled, not fall through"
    # Persisted to the canonical lift key.
    athlete_profile.reset()
    assert athlete_profile.get().get_1rm("back_squat") == 120.0
    # Confirmation reflects the NEW value, not the old one.
    assert "120" in reply
    assert "up from" not in reply.lower()      # no fabricated "up from 102"
    assert "150" not in reply                  # no stale old value
    # Single lbs conversion: 120 kg → 265 lbs (round). Never a doubled pair.
    assert "265 lbs" in reply
    assert reply.count("lbs") == 1


# ─── 5. Imperial input converts once ───────────────────────────────────
def test_nl_1rm_set_lbs_converts_to_kg(sandbox_db):
    from core import athlete_profile

    reply = dispatcher.dispatch("set my bench to 220 lbs")
    assert reply is not None
    athlete_profile.reset()
    kg = athlete_profile.get().get_1rm("bench_press")
    # 220 lbs ≈ 99.8 kg
    assert kg == pytest.approx(99.8, abs=0.3)
