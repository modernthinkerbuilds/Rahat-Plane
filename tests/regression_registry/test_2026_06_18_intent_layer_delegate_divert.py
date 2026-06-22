"""Regression: the intent layer must divert read-intent PARAPHRASES at the
delegate level, not just inside Kobe's route() (2026-06-18, owner).

THE BUG (live, 2026-06-18 22:11): "what does my week look like" returned a
short PROSE answer while "/plan" returned the structured render — two
different answers for the same question. Root cause: the new_plane
orchestrator runs `delegate_classifier` FIRST; a paraphrase classified as
"orchestrate" went to the synth/paraphrase path and never reached Kobe's
route() where the intent layer was wired. So the layer never fired on the
live path.

THE FIX: `classify_delegation` consults the intent layer (flag-gated) as
its last step, before the "orchestrate" default, and routes a confident
read-intent paraphrase to "kobe_route" — the SAME path `/plan` uses, so
the answer is the deterministic `handle_show_plan` render, consistent.

Pins:
  1. Flag OFF (default) → paraphrases still classify "orchestrate"
     (byte-identical to pre-fix routing).
  2. Flag ON → read paraphrases classify "kobe_route".
  3. Flag ON → DESIGN requests still "orchestrate" (Fraser's territory —
     the design guard is honored).
  4. Flag ON → open-ended / mutation phrasings unchanged.
"""
from __future__ import annotations

import pytest

from new_plane.miya_runner import delegate_classifier as dc
from core import intent_layer as il


# Paraphrases that the existing deterministic patterns MISS (flag-off →
# orchestrate) but the intent layer catches (flag-on → kobe_route). Many
# plan phrasings are already owned by _PLAN_QUERY_RE; these two are real
# gaps the layer closes.
_PARAPHRASES = [
    "what does my week look like",   # show_plan_this_week
    "what movements am i avoiding",  # list_dislikes
]
# Must stay on the orchestrate/synth path even with the flag ON.
_STAY_ORCHESTRATE = [
    "design me a workout for today",   # Fraser authoring — design guard
    "build me a workout",
    "scale todays wod for me",
    "should i cut carbs",
    "tell me a joke",
    "i feel sore today",
    # `workout_today` is delegate-excluded: these are statements / desires,
    # not "what's my workout" questions, and must not divert to Kobe.
    "I did crossfit today",
    "I want to do CrossFit today",
    # `current_weight` is delegate-excluded: "weigh in" is the EVENT/timing,
    # not the quantity — must not divert to a current-weight readout.
    "when should I weigh in",
]


@pytest.fixture(autouse=True)
def _fresh_registry():
    il._clear_registry()
    il._REGISTERED = False
    yield
    il._clear_registry()
    il._REGISTERED = False


@pytest.mark.parametrize("msg", _PARAPHRASES)
def test_flag_off_paraphrase_stays_orchestrate(monkeypatch, msg):
    monkeypatch.delenv("RAHAT_INTENT_LAYER", raising=False)
    path, _ = dc.classify_delegation(msg)
    assert path == "orchestrate", (
        f"with the flag OFF, {msg!r} must route exactly as before (orchestrate)")


@pytest.mark.parametrize("msg", _PARAPHRASES)
def test_flag_on_paraphrase_diverts_to_kobe(monkeypatch, msg):
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    path, _ = dc.classify_delegation(msg)
    assert path == "kobe_route", (
        f"with the flag ON, {msg!r} must divert to kobe_route so it lands on "
        "handle_show_plan (same path /plan uses), not the synth paraphrase")


@pytest.mark.parametrize("msg", _STAY_ORCHESTRATE)
def test_flag_on_design_and_openended_stay_orchestrate(monkeypatch, msg):
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    path, _ = dc.classify_delegation(msg)
    assert path == "orchestrate", (
        f"{msg!r} must NOT be diverted to Kobe — design requests are Fraser's "
        "and open-ended messages belong on the synth path")


@pytest.mark.parametrize("msg", [
    "log my weight as 198", "hrv was 42 this morning",
])
def test_flag_on_mutations_not_diverted_by_layer(monkeypatch, msg):
    """Mutations are read-only-excluded from the layer; whatever path the
    deterministic patterns choose, the layer never adds a divert for them."""
    monkeypatch.setenv("RAHAT_INTENT_LAYER", "1")
    # The layer abstains on these, so classify() is None and no extra divert
    # happens. (Routing itself is owned by the earlier deterministic rules.)
    il._REGISTERED = False
    il.ensure_registered()
    assert il.classify(msg) is None
