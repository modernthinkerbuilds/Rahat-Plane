"""Feature pin (2026-08-10) — PRD J5 raw list + J1-step-5 swap iteration.

J5 ("just give me the raw list"): the discovery inventory exposed
directly — de-duplicated, scope stated up front, NOT a plan. Success
criteria from the PRD: no duplicates across sources/days, transparent
scoping, graceful degradation.

Swap (generate-then-iterate, PRD's step 5): the humans pick from the
offered alternates ("swap in Happy Hollow") and Genie refines the SAVED
plan deterministically — charter-gated re-commit, slot ordering
preserved, displaced outing returned to the pool so swaps are
reversible. The value concentrates in steps 2 and 5; step 3 (the human
decision) stays human.
"""
from __future__ import annotations

import importlib
import json

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


_DISCOVERY = {
    "weather": {"saturday": "sunny", "sunday": "cloudy"},
    "options": {
        "saturday": [
            {"time": "morning", "activity": "Farmers market",
             "place": "Main St", "why": "strollable", "source": "cm.org"},
            {"time": "afternoon", "activity": "Happy Hollow Zoo",
             "place": "Happy Hollow", "why": "toddler rides",
             "source": "hh.org"},
            {"time": "afternoon", "activity": "Discovery Museum",
             "place": "CDM", "why": "indoor", "source": "cdm.org"},
        ],
        "sunday": [
            {"time": "morning", "activity": "Lake stroll",
             "place": "Lake Park", "why": "flat loop", "source": "parks"},
            # Duplicate of a Saturday find — J5 must de-dup across days.
            {"time": "morning", "activity": "Farmers market",
             "place": "Main St", "why": "also sunday", "source": "cm.org"},
        ],
    },
}


def _llm(prompt: str) -> str:
    return json.dumps(_DISCOVERY)


# ─────────────────────────── J5: raw list ───────────────────────────
def test_whats_on_lists_deduped_with_scope(genie):
    out = genie.handle_whats_on(llm=_llm)
    assert "What's on" in out
    assert out.count("Farmers market") == 1        # de-dup across days
    assert "Happy Hollow Zoo" in out               # NOT capped — raw list
    assert "Discovery Museum" in out
    assert "Scope:" in out                         # transparent scoping
    assert "naps protected" not in out             # a list, not a plan


def test_whats_on_without_location_explains(genie, monkeypatch):
    monkeypatch.delenv("RAHAT_GENIE_LOCATION", raising=False)
    out = genie.handle_whats_on(llm=_llm)
    assert "RAHAT_GENIE_LOCATION" in out


def test_whats_on_discovery_failure_degrades(genie):
    out = genie.handle_whats_on(llm=lambda p: "garbage")
    assert "try again" in out.lower()


def test_whats_on_routes_from_nl_and_slash(genie):
    for msg in ("/whatson", "what's on this weekend", "any events this week"):
        out = genie.route(msg)
        # No location→live in route()-level calls without seam is fine —
        # the point is the ROUTE, not the payload.
        assert "What's on" in out or "RAHAT_GENIE_LOCATION" in out \
            or "try again" in out.lower(), f"{msg!r} mis-routed: {out[:80]}"


# ─────────────────────────── swap iteration ───────────────────────────
def _make_plan(genie):
    return genie.handle_weekend_plan(llm=_llm)


def test_swap_replaces_and_reorders(genie):
    _make_plan(genie)
    out = genie.route("swap in happy hollow")
    assert "Swapped in" in out and "Happy Hollow Zoo" in out
    body = out.splitlines()
    day_lines = [l for l in body if l.strip().startswith("•")]
    # Slot order must hold: the afternoon swap-in renders AFTER the nap.
    nap_idx = next(i for i, l in enumerate(day_lines) if "naps" in l)
    zoo_idx = next(i for i, l in enumerate(day_lines) if "Happy Hollow" in l)
    assert zoo_idx > nap_idx
    assert "✅ Plan saved." in out                 # charter-gated re-commit


def test_swap_is_reversible(genie):
    _make_plan(genie)
    genie.route("swap in happy hollow")
    # The displaced Farmers market went back to the pool.
    out = genie.route("swap in farmers")
    assert "Swapped in" in out and "Farmers market" in out


def test_swap_without_plan(genie):
    out = genie.route("swap in anything")
    assert "/weekend_plan" in out


def test_swap_no_match_lists_choices(genie):
    _make_plan(genie)
    out = genie.route("swap in the opera")
    assert "Couldn't find" in out
    assert "Happy Hollow Zoo" in out               # tells you what IS there


def test_swap_updates_latest_saved_plan(genie):
    _make_plan(genie)
    genie.route("swap in discovery museum")
    from agents.genie import state
    plan = state.latest_weekend_plan()
    assert any("Discovery Museum" in l for l in plan.saturday)
    assert "swapped in: Discovery Museum" in plan.notes


# ─────────────────── miya-side classifier routing ───────────────────
@pytest.mark.parametrize("msg", [
    "what's on this weekend",
    "any events this week",
    "swap in Happy Hollow",
])
def test_classifier_routes_new_intents_to_genie(msg):
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    path, _ = classify_delegation(msg)
    assert path == "genie_route", f"{msg!r} → {path!r}"


def test_classifier_kobe_keeps_day_swaps():
    """Kobe's plan mutation 'swap Mon with Tue' must NOT go to Genie."""
    from new_plane.miya_runner.delegate_classifier import classify_delegation
    path, _ = classify_delegation("swap Mon with Tue")
    assert path == "kobe_route"
