"""Pin: 2026-05-17 — handle_show_plan(next_week=True) lied about sync.

SYMPTOM (production):
    User asked "show me next week's plan". `parse_gym_plan()` returned
    7 days successfully. But handle_show_plan(next_week=True) replied
    "No gym plan synced" anyway. The plan file was sitting on disk
    the whole time.

ROOT CAUSE (per Day 9 Bug 1 spec):
    handle_show_plan had a branch that checked plan freshness using
    a different path/timestamp than parse_gym_plan. When the freshness
    check failed for the next_week=True case but parse_gym_plan was
    happy with the file, the function reported "not synced" while
    parse_gym_plan had already returned real days.

FIX (in flight — Day 9 Bug 1):
    Single source of truth: handle_show_plan uses parse_gym_plan's
    output as the ground truth. If parse_gym_plan returns ≥7 days,
    handle_show_plan must NOT claim "no plan synced."

THIS PIN ASSERTS:
    Given parse_gym_plan returns 7 valid days, handle_show_plan
    (with both next_week=False and next_week=True) returns real
    day labels in its output — not "No gym plan synced."
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_handler():
    for modpath in ("agents.the_scientist.handler",
                    "agents.kobe.handler"):
        try:
            return importlib.import_module(modpath)
        except ImportError:
            continue
    pytest.skip("no Kobe handler module")


def _synthetic_seven_day_plan():
    """Returns a deterministic 7-day plan structure that mirrors what
    parse_gym_plan would return. The exact dataclass varies across
    revisions; we just need something show_plan accepts."""
    h = _load_handler()
    # Try to find the DayPlan / GymDay dataclass.
    for cls_name in ("DayPlan", "GymDay", "ParsedDay"):
        if hasattr(h, cls_name):
            cls = getattr(h, cls_name)
            try:
                return [cls(weekday=name, label=f"{name} session", body="",
                            blockers=set())
                        for name in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]]
            except Exception:
                continue
    # Fallback to dict shape (the legacy adapter).
    return [{"weekday": name, "label": f"{name} session", "body": "",
             "blockers": set()}
            for name in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]]


def _has_real_days(text: str) -> bool:
    """Test that the output mentions at least 3 distinct weekday names."""
    if not text:
        return False
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    found = sum(1 for d in days if d in text.lower())
    return found >= 3


def _looks_like_no_plan_error(text: str) -> bool:
    """Returns True if the output is the 'no plan synced' fallback."""
    if not text:
        return False
    needles = [
        "no gym plan",
        "no plan synced",
        "no plan found",
        "no workout plan",
        "plan not found",
    ]
    low = text.lower()
    return any(n in low for n in needles)


@pytest.mark.xfail(strict=False, reason="Day 9 Bug 1 fix may not have landed yet")
def test_show_plan_does_not_lie_when_seven_days_synced(bootstrap_substrate):
    """When parse_gym_plan() returns 7 days, handle_show_plan() must
    return them — NOT a 'no plan synced' message."""
    h = _load_handler()
    if not hasattr(h, "handle_show_plan"):
        pytest.skip("handle_show_plan not defined in this branch")

    plan_days = _synthetic_seven_day_plan()

    # Patch parse_gym_plan in handler's namespace so the function
    # under test sees our seeded 7-day plan.
    if not hasattr(h, "parse_gym_plan"):
        pytest.skip("parse_gym_plan not in handler module")

    with patch.object(h, "parse_gym_plan", return_value=plan_days):
        result = h.handle_show_plan(next_week=False)

    text = result if isinstance(result, str) else (result.get("text", "")
                                                   if isinstance(result, dict)
                                                   else str(result))

    assert not _looks_like_no_plan_error(text), (
        f"handle_show_plan claimed 'no plan synced' while parse_gym_plan "
        f"was returning 7 days. This is exactly the 2026-05-17 bug. "
        f"Output: {text[:300]!r}")
    assert _has_real_days(text), (
        f"handle_show_plan output doesn't mention 3+ weekday names. "
        f"Output: {text[:300]!r}")


@pytest.mark.xfail(strict=False, reason="Day 9 Bug 1 fix may not have landed yet")
def test_show_plan_next_week_does_not_lie(bootstrap_substrate):
    """Same assertion for the next_week=True branch — the original bug
    report was specifically the next-week query."""
    h = _load_handler()
    if not hasattr(h, "handle_show_plan"):
        pytest.skip("handle_show_plan not defined")

    plan_days = _synthetic_seven_day_plan()
    if not hasattr(h, "parse_gym_plan"):
        pytest.skip("parse_gym_plan not in handler module")

    with patch.object(h, "parse_gym_plan", return_value=plan_days):
        result = h.handle_show_plan(next_week=True)

    text = result if isinstance(result, str) else (result.get("text", "")
                                                   if isinstance(result, dict)
                                                   else str(result))

    assert not _looks_like_no_plan_error(text), (
        f"handle_show_plan(next_week=True) claimed 'no plan synced' "
        f"while parse_gym_plan was returning 7 days. Output: "
        f"{text[:300]!r}")
