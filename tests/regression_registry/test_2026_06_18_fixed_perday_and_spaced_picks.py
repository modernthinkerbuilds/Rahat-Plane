"""Regression: fixed per-day kcal (B model) + recovery-spaced clean-day picks
(2026-06-18, owner).

Two coupled changes:
  1. The hammer template is the FIXED per-day truth — CF/Z2 = 1,075, rest =
     600. The goal drives pace FEEDBACK via weekly_target(), it no longer
     RESCALES the per-day ideals (that produced the 1,225/1,350/550 nonsense).
     The goal-rescale now fires ONLY for an explicit weekly commitment.
  2. When more blacklist-clean gym days exist than the cadence needs, the
     tie-breaker is RECOVERY SPACING (max the smallest gap), not "earliest 3".
"""
from __future__ import annotations

from agents.the_scientist.protocols import DAY_TYPE_BY_TIER
from agents.the_scientist.state import _spaced_pick


def test_hammer_perday_is_fixed_calibration():
    h = DAY_TYPE_BY_TIER["hammer"]
    assert h == {"cf": 1075, "z2": 1075, "rest": 600}, (
        "hammer per-day must be the fixed calibration the owner set "
        "(CF/Z2 1,075, rest 600); a 3 CF + 1 Z2 + 3 rest week = 6,100"
    )
    # The canonical 3 CF + 1 Z2 + 3 rest week sums to 6,100.
    assert 3 * h["cf"] + 1 * h["z2"] + 3 * h["rest"] == 6100


def test_spaced_pick_maximizes_recovery_gap():
    # 5 clean weekdays Mon..Fri, pick 3 → Mon/Wed/Fri (gaps of 2), not Mon/Tue/Wed.
    assert _spaced_pick([0, 1, 2, 3, 4], 3) == [0, 2, 4]
    # Whole week clean → Mon/Thu/Sun (max spread).
    assert _spaced_pick([0, 1, 2, 3, 4, 5, 6], 3) == [0, 3, 6]
    # Never stacks the earliest three back-to-back when spacing is possible.
    assert _spaced_pick([0, 1, 2, 3], 3) != [0, 1, 2]


def test_spaced_pick_degenerate_cases():
    assert _spaced_pick([1, 3, 5], 3) == [1, 3, 5]   # exactly n
    assert _spaced_pick([3, 4], 3) == [3, 4]          # fewer than n → all
    assert _spaced_pick([], 3) == []
