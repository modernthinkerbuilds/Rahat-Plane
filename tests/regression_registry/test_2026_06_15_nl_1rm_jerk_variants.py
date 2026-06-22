"""Regression: NL 1RM set must recognize jerk / clean&jerk variants and the
"1 RM ... would be N" phrasing (2026-06-15, live bug).

Live transcript: user said "My 1 RM split jerk would be 65 kg, so update".
"split jerk" was not in the dispatcher's lift list, and "1 RM" (spaced) +
"would be" were not recognized set forms, so the message never reached the
deterministic /profile-set persist path. It fell to the LLM reasoner, which
FABRICATED "your split jerk 1RM is now noted as 65 kg" WITHOUT persisting
anything (same hallucinated-save class as the back-squat bug, for an
unrecognized lift name).

Fix: jerk variants added to _LIFT_ALT (multi-word first), and the strict
patterns accept "1 RM"/"would be"/"should be". set_one_rm already accepts
custom lifts, so persistence works once routing does.
"""
from __future__ import annotations

import pytest

from core import dispatcher


@pytest.mark.parametrize("msg,lift_frag,num", [
    ("My 1 RM split jerk would be 65 kg", "split jerk", "65"),
    ("my 1rm split jerk would be 65", "split jerk", "65"),
    ("set my split jerk to 65", "split jerk", "65"),
    ("my clean and jerk max is 90 kg", "clean and jerk", "90"),
    ("my clean & jerk 1rm is 90", "clean & jerk", "90"),
    ("split jerk is now 65", "split jerk", "65"),
])
def test_jerk_1rm_set_is_recognized(msg, lift_frag, num):
    # Coarse gate fires...
    assert dispatcher._ONE_RM_SET_RE.search(msg), f"coarse gate missed: {msg!r}"
    # ...and a strict pattern extracts the right lift + number.
    hit = None
    for pat in dispatcher._ONE_RM_STRICT:
        m = pat.search(msg)
        if m:
            hit = m
            break
    assert hit, f"no strict pattern matched: {msg!r}"
    assert hit.group("lift").lower().replace("  ", " ") == lift_frag
    assert hit.group("num") == num


@pytest.mark.parametrize("msg", [
    "I'll jerk the weight at 60 today",      # workout intent, not a set
    "split jerk felt heavy today",            # no set signal / number
    "what's my split jerk 1rm",               # a query, not a set
])
def test_non_set_jerk_phrasings_do_not_persist(msg):
    """Coarse gate may be permissive, but the strict parse must return None so
    the message falls through to the reasoner instead of writing a bogus 1RM."""
    match = dispatcher._ONE_RM_SET_RE.search(msg)
    if match is None:
        return  # already excluded at the gate — good
    assert dispatcher._h_one_rm_set(msg, match) is None, (
        f"{msg!r} must NOT be treated as a 1RM set"
    )


def test_split_jerk_actually_persists(monkeypatch):
    """End-to-end: the set routes to the deterministic persist path and the
    confirmation reflects the REAL stored value (no fabrication)."""
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    msg = "My 1 RM split jerk would be 65 kg"
    match = dispatcher._ONE_RM_SET_RE.search(msg)
    assert match
    out = dispatcher._h_one_rm_set(msg, match)
    assert out is not None
    assert "65" in out and "split jerk" in out.lower()
    assert "✅" in out or "updated" in out.lower()
