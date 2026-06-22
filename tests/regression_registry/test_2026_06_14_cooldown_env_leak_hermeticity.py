"""Pin: 2026-06-14 — prod `.env` (`RAHAT_COOLDOWN_LLM=1`) leaked into the
test process and broke the dispatcher route-matching tests.

SYMPTOM (test suite, sandbox + CI):
    tests/test_dispatcher.py::test_route_matches_intended_phrasings
      [pre-workout fuel-pre_fuel]      -> dispatched to None
      [what should I eat before-pre_fuel] -> dispatched to None
      [cool-down routine-post_recovery]   -> dispatched to None
    Three of the parametrized cases failed: the phrasings matched no route.

ROOT CAUSE (not a dispatcher bug):
    The repo `.env` ships `RAHAT_COOLDOWN_LLM=1` — a deliberate PRODUCTION
    setting so cool-down / pre-fuel asks go through the personalized LLM
    instead of the canned block (the 2026-05-25 yield feature). Several
    core modules (`core.io`, `core.gemini_reasoner_io`,
    `agents.the_scientist.handler`) call `load_dotenv()` at IMPORT time.
    Once any test imports one of them, `RAHAT_COOLDOWN_LLM=1` is in
    `os.environ` for the rest of the pytest process. `dispatcher.dispatch()`
    then *yields* the `pre_fuel` / `post_recovery` routes (returns None so
    the LLM answers), so the route-matching assertions — which pin the
    regex→route map, a flag-independent property — saw None.

FIX:
    `tests/test_dispatcher.py::stub_handlers` now forces
    `RAHAT_COOLDOWN_LLM=0`, making the route-matching tests hermetic w.r.t.
    the prod flag. The yield-on-flag behaviour stays pinned by
    test_2026_05_25_cooldown_yield_flag.py.

THIS PIN ASSERTS the dispatcher contract directly, independent of any
loaded `.env`, both ways:
    1. flag OFF  -> "pre-workout fuel" / "cool-down routine" MATCH their
       canned routes (pre_fuel / post_recovery).
    2. flag ON   -> those routes YIELD (match_route returns None) so the
       LLM path can answer.
It also documents the import-time `load_dotenv()` leak so a future entry
point doesn't silently re-break test isolation.
"""
from __future__ import annotations

import pytest

from core import dispatcher


# The three phrasings the prod-.env leak knocked out, plus their routes.
_COOLDOWN_PHRASINGS = [
    ("pre-workout fuel", "pre_fuel"),
    ("what should I eat before", "pre_fuel"),
    ("cool-down routine", "post_recovery"),
]


@pytest.mark.parametrize("msg,route", _COOLDOWN_PHRASINGS)
def test_cooldown_routes_match_when_flag_off(monkeypatch, msg, route):
    """Flag OFF: the canned cool-down / pre-fuel routes match their
    phrasings. This is the regex→route contract, independent of `.env`."""
    monkeypatch.setenv("RAHAT_COOLDOWN_LLM", "0")
    assert dispatcher.cooldown_llm_enabled() is False
    assert dispatcher.match_route(msg) == route


@pytest.mark.parametrize("msg,route", _COOLDOWN_PHRASINGS)
def test_cooldown_routes_yield_when_flag_on(monkeypatch, msg, route):
    """Flag ON (the prod `.env` value): the same routes YIELD inside
    dispatch() so the personalized LLM answers — dispatch returns None
    without invoking the canned handler. (match_route() reports the regex
    match regardless of the flag; the yield lives only in dispatch().)"""
    monkeypatch.setenv("RAHAT_COOLDOWN_LLM", "1")
    monkeypatch.delenv("RAHAT_USE_DISPATCHER", raising=False)  # default on
    assert dispatcher.cooldown_llm_enabled() is True
    # The regex still matches (route map is flag-independent) ...
    assert dispatcher.match_route(msg) == route
    # ... but dispatch yields it so the LLM path can answer.
    assert dispatcher.dispatch(msg) is None
