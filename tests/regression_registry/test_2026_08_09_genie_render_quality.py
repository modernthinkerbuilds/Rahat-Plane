"""Regression (2026-08-09, live) — the weekend-plan card must read as a
plan with choices, not a verdict with clutter.

THE INCIDENT (second live /weekend_plan, owner verdict: "pretty bad
quality"). Three rendering defects, all predicted by the PRD's own
self-critique ("conflict-ledger rendering: clarity or clutter is
unknown — prototype early"):

  1. TEN over-cap finds rendered as ten IDENTICAL "over the low-energy
     budget" ruled-out lines. Those aren't rule-outs — they're the
     choices the PRD's J1 loop says the humans get to make.
  2. `place` shipped a full street address + parking trivia:
     "357 E. Taylor Street, San Jose, CA 95112 (behind Gordon Biersch)".
  3. A low-energy day ended dead at the nap line — one item, then
     nothing.

THE PINS (all at the handler-render level, driving the real
handle_weekend_plan with a fake discovery seam):

  * over-cap finds appear under "Also good this weekend" (capped list +
    overflow count) and the phrase "over the .*-energy budget" never
    renders;
  * "Ruled out" carries ONLY genuine constraint violations, each with
    its concrete reason;
  * place is venue-name-only (no commas / zips survive coercion);
  * a low-energy day closes with the home wind-down block.
"""
from __future__ import annotations

import importlib
import json
import re

import pytest


@pytest.fixture
def genie(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    return handler


# Mirrors the live incident shape: many good finds, one nap collision,
# one address-stuffed place field.
_INCIDENT_DISCOVERY = {
    "weather": {"saturday": "mostly sunny, low 80s",
                "sunday": "partly sunny, low 80s"},
    "options": {
        "saturday": [
            {"time": "morning", "activity": "Music and Movement",
             "place": "357 E. Taylor Street, San Jose, CA 95112 (behind Gordon Biersch)",
             "why": "free and air-conditioned", "source": "SJ Library"},
            {"time": "morning", "activity": "Willow Glen Farmers' Market",
             "place": "Willow Glen", "why": "stroller stroll", "source": "WGBA"},
            {"time": "afternoon", "activity": "Happy Hollow Park & Zoo",
             "place": "Happy Hollow", "why": "toddler rides", "source": "hh.org"},
            {"time": "midday", "activity": "Water Spray Pad",
             "place": "Hellyer Park", "why": "cool-down", "source": "SCC Parks"},
        ],
        "sunday": [
            {"time": "morning", "activity": "Japantown Farmer's Market",
             "place": "Japantown", "why": "easy stroll", "source": "JBA"},
            {"time": "morning", "activity": "River Park Trail",
             "place": "Guadalupe", "why": "flat loop", "source": "grpg"},
        ],
    },
}


def _plan(genie):
    return genie.handle_weekend_plan(
        llm=lambda p: json.dumps(_INCIDENT_DISCOVERY), commit=False,
        audience_text="protect the naps")


def test_over_cap_finds_are_offered_not_rejected(genie):
    out = _plan(genie)
    assert "Also good this weekend" in out, (
        "over-cap finds must be offered as choices (PRD J1)")
    assert "Willow Glen Farmers' Market" in out
    assert not re.search(r"over the \w+-energy budget", out), (
        "the incident clutter is back: over-cap finds rendered as "
        "identical budget ruled-out lines")


def test_ruled_out_is_violations_only_with_reasons(genie):
    out = _plan(genie)
    ruled = out.split("*Ruled out*", 1)
    assert len(ruled) == 2, "nap collision must produce a Ruled out section"
    tail = ruled[1].split("_Sized")[0]
    assert "Water Spray Pad" in tail
    assert "nap window" in tail                   # concrete reason present
    assert "Happy Hollow" not in tail             # over-cap ≠ ruled out


def test_place_renders_venue_name_only(genie):
    out = _plan(genie)
    assert "95112" not in out
    assert "E. Taylor Street" not in out
    assert "Music and Movement at 357 E" not in out


def test_low_energy_day_ends_with_wind_down(genie):
    out = _plan(genie)
    sat = out.split("*Saturday*", 1)[1].split("*Sunday*", 1)[0]
    assert "wind-down" in sat, "low-energy day must not end at the nap line"


def test_alternates_overflow_is_counted_not_spammed(genie):
    out = _plan(genie)
    also = out.split("Also good this weekend", 1)[1].split("*Ruled out*")[0]
    bullets = [l for l in also.splitlines() if l.strip().startswith("•")]
    assert len(bullets) <= 5, (
        f"alternates list must stay compact (≤4 + overflow line), "
        f"got {len(bullets)} bullets")
