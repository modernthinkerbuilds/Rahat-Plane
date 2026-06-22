"""Regression (2026-06-21): the live bot called the user 'Alex' and a typo'd
'tommorows WOD' rambled through the reasoner.

ROOT CAUSES:
1. core/user_profile.load() read 1RMs / limitations / training_context from
   the gitignored vault overlay but NEVER the 'name' field — so the facts
   block injected into the synthesizer said "Name: Alex" (the committed
   default) and the model quoted it for "what's my name", even though the
   coach persona was already correct.
2. The relative-day WOD routes matched only the correct spelling "tomorrow",
   so a common typo ("tommorow"/"tomorow") matched no route and fell to the
   reasoner, which answered inconsistently.
"""
from __future__ import annotations

import json

import pytest

from core import dispatcher


# ── 1. user_profile name overlay ──
def test_overlay_name_is_applied(monkeypatch, tmp_path):
    overlay = tmp_path / "user_profile.json"
    overlay.write_text(json.dumps(
        {"name": "Venkat", "one_rep_maxes_kg": {"deadlift": 200.0}}))
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(overlay))
    from core import user_profile as up
    p = up.load()
    assert p.name == "Venkat", "load() must read display name from the overlay"
    assert "Name: Venkat" in up.to_facts_block(p)


def test_missing_overlay_keeps_committed_default(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_USER_PROFILE_JSON", str(tmp_path / "nope.json"))
    from core import user_profile as up
    assert up.load().name == "Alex"      # public/default identity unchanged


# ── 2. typo-tolerant relative-WOD routing ──
@pytest.mark.parametrize("msg", [
    "what is tommorows WOD",     # the exact phrasing that broke (double-m typo)
    "tommorows workout",
    "tomorows wod",
    "what is tomorrows wod",
    "what's tomorrow's wod",
    "tomorrow's session",
])
def test_tomorrow_wod_typos_still_route(msg):
    assert dispatcher.match_route(msg) == "rel_day_workout", (
        f"{msg!r} must route deterministically to the gym WOD, not the reasoner")
