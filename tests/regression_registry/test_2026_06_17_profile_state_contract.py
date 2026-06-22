"""Round-2 P1-3 — profile-state contract: missing → caveat (never fabricate),
stale weight → marked.

The synth prompt is built from `to_facts_block`. The contract:
  * a MISSING fact must render as an explicit caveat, never an invented
    number — so the model can't quote something that isn't on file;
  * a current weight older than _WEIGHT_STALE_WEEKS must be marked STALE so
    the model re-asks instead of quoting a drifted number.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.user_profile import (
    UserProfile, to_facts_block, _weight_staleness_note, _WEIGHT_STALE_WEEKS,
)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")


# ── missing → caveat, never fabricate ──
def test_missing_weight_caveats_not_fabricates():
    p = UserProfile(name="Alex", current_weight_lbs=None)
    block = to_facts_block(p)
    assert "Current weight: unknown" in block
    assert "ask before quoting" in block


def test_missing_1rms_render_nothing_no_invention():
    p = UserProfile(name="Alex", one_rep_maxes_kg={})
    block = to_facts_block(p)
    # No 1RM section at all — and definitely no fabricated number.
    assert "1RMs" not in block


def test_missing_goal_renders_no_goal_line():
    p = UserProfile(name="Alex", active_goal_target_lbs=None)
    block = to_facts_block(p)
    assert "Active sprint" not in block


# ── stale weight → marked ──
def test_stale_weight_is_marked():
    p = UserProfile(name="Alex", current_weight_lbs=199.0,
                    current_weight_at=_iso(days_ago=7 * (_WEIGHT_STALE_WEEKS + 2)))
    block = to_facts_block(p)
    assert "STALE" in block, "a weigh-in > 5 weeks old must be marked STALE"


def test_fresh_weight_not_marked():
    p = UserProfile(name="Alex", current_weight_lbs=199.0,
                    current_weight_at=_iso(days_ago=3))
    block = to_facts_block(p)
    assert "STALE" not in block


# ── helper edge cases (never raise) ──
def test_staleness_note_handles_garbage():
    assert _weight_staleness_note(None) == ""
    assert _weight_staleness_note("not-a-date") == ""
    assert _weight_staleness_note("") == ""


def test_staleness_boundary():
    # Exactly at the threshold is NOT stale; clearly past it is.
    fresh = _weight_staleness_note(_iso(days_ago=7 * _WEIGHT_STALE_WEEKS - 1))
    stale = _weight_staleness_note(_iso(days_ago=7 * _WEIGHT_STALE_WEEKS + 8))
    assert fresh == ""
    assert "STALE" in stale
