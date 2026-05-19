"""Regression: Kobe's _should_delegate intercepted gym-WOD-day queries
before _legacy_route could route them to handle_gym_wod_on.

2026-05-18 production incident, surfaced ~21:24 Pacific.

User sent: "What is the WOD for Tuesday"

Expected: handle_gym_wod_on(1) returns the real synced Tuesday WOD
from parse_gym_plan() — Back Squat 1RM + "Furiosa".

Actual: Bot replied "fraser says: [Fraser] mode=default · hrv=55 ·
tier=zone2 · injuries=0 · w...". Fraser's default-mode snapshot
stub, wrapped in Kobe's delegation attribution.

Root cause: Kobe's route() in handler.py runs:
  1. _try_slash_command(msg)
  2. _should_delegate(msg) → checks _FRASER_DELEGATION_PATTERNS
  3. RAHAT_LEGACY_DISPATCH branch → _legacy_route(msg)
  4. reasoner.reason(msg)

The Day-10 _is_gym_wod_on_day_query detector was wired INSIDE
_legacy_route — step 3. But _should_delegate matched the generic
'\bwhat(?:'s|\s+is)\s+(?:my\s+|the\s+|today'?s\s+)?wod\b' pattern
in step 2 and forwarded the query to Fraser. Fraser doesn't read
parse_gym_plan(); it ran design_workout() which returned the
mode=default stub.

Fix: priority guard at the top of _should_delegate. If
_is_gym_wod_on_day_query returns a weekday index, return None
(Kobe owns the lookup, do NOT delegate). Generic 'what is the WOD'
without a day still delegates to Fraser correctly.

What this test pins:

1. _should_delegate returns None for every "WOD for [weekday]"
   phrasing (Kobe handles).
2. _should_delegate STILL returns 'fraser' for generic "what is
   the WOD" without a day anchor (so Fraser-design queries are
   not accidentally captured).
3. End-to-end via Kobe's route() — when called with a gym-WOD-day
   query, the result is the real handle_gym_wod_on output, not a
   Fraser stub.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest


# ─── 1. The guard itself ───────────────────────────────────────────
@pytest.mark.parametrize("query", [
    "What is the WOD for Tuesday",
    "what is the WOD for Saturday",
    "What is the WOD for Monday",
    "what is the WOD for friday",
    "gym workout for Monday",
    "gym workout for Sunday",
    "whats at the gym on Wednesday",
    "what's at the gym on Thursday",
])
def test_should_delegate_returns_none_for_gym_wod_day_lookups(query):
    """Day-specific gym-WOD lookups must NOT delegate — Kobe owns
    the lookup via handle_gym_wod_on. If a future refactor removes
    the priority guard, this test catches it before production."""
    from agents.the_scientist.handler import _should_delegate
    assert _should_delegate(query) is None, (
        f"_should_delegate({query!r}) returned non-None — this is "
        f"the 2026-05-18 production bug. Kobe's gym-WOD-day lookup "
        f"got delegated to Fraser, who returned a default-mode stub. "
        f"Restore the priority guard at the top of _should_delegate."
    )


# ─── 2. Generic Fraser-design queries still delegate ──────────────
# Only queries that match existing _FRASER_DELEGATION_PATTERNS — the
# point is the guard doesn't accidentally swallow what was already
# delegating.
@pytest.mark.parametrize("query", [
    "what is the WOD",
    "what's my WOD",
    "give me today's workout",
    "show me the workout",
])
def test_should_delegate_still_routes_generic_wod_to_fraser(query):
    """Without a day anchor, 'what is the WOD' / 'give me the
    workout' is a DESIGN request — Fraser's territory. Make sure
    the priority guard doesn't accidentally swallow these."""
    from agents.the_scientist.handler import (
        _should_delegate, _is_gym_wod_on_day_query,
    )
    # First: the gym-day detector must NOT match.
    assert _is_gym_wod_on_day_query(query) is None, (
        f"Sanity: gym-day detector should NOT match {query!r} (no day "
        f"anchor). If it does, the regex got broader than intended."
    )
    # Then: _should_delegate should still route to Fraser.
    assert _should_delegate(query) == "fraser", (
        f"_should_delegate({query!r}) lost its Fraser routing. The "
        f"priority guard for gym-WOD-day was too aggressive."
    )


# ─── 3. End-to-end via Kobe's route() ─────────────────────────────
def test_kobe_route_returns_real_gym_wod_not_fraser_stub(
        tmp_path, monkeypatch):
    """The production-bug shape: send "What is the WOD for Tuesday"
    through Kobe's full route() and assert the reply contains real
    gym programming, NOT the Fraser default-mode stub."""
    # Fixture gym plan with a Tuesday body the test can recognize.
    plan_content = (
        "MON 18\n\n0\n Bench Press 1RM\nTake 15:00.\n\n"
        "TUE 19\n\n0\n Back Squat 1RM\nTake 15:00.\n\n"
        "0 results\n \"Furiosa\"\nFor Load\n"
        "Every 3:00 x 5 Sets:\n"
        "25/20 Calorie Row\n"
        "Barbell Complex: Clean + Hang Clean + Clean\n\n"
        "WED 20\n\n0\n Katniss\n"
    )
    plan_file = tmp_path / "weekly_plan.txt"
    plan_file.write_text(plan_content)
    from agents.the_scientist import handler as _handler
    monkeypatch.setattr(_handler, "PLAN_PATH", plan_file)
    try:
        from agents.the_scientist import main as _sci_main
        monkeypatch.setattr(_sci_main, "PLAN_PATH", plan_file)
    except (ImportError, AttributeError):
        pass

    # Force legacy-dispatch path so the test doesn't depend on
    # reasoner LLM availability.
    monkeypatch.setenv("RAHAT_LEGACY_DISPATCH", "1")

    reply = _handler.route("What is the WOD for Tuesday")

    # The bug shape: reply contains "[Fraser] mode=default" because
    # _should_delegate forwarded to Fraser's design_workout stub.
    assert "[Fraser] mode=default" not in reply, (
        f"Kobe.route() returned Fraser's default-mode stub for a "
        f"gym-WOD-day lookup. The priority guard in _should_delegate "
        f"is broken. Reply:\n{reply}"
    )
    assert "fraser says:" not in reply, (
        f"Kobe.route() returned a Fraser delegation wrapper for a "
        f"gym-WOD-day lookup. The priority guard in _should_delegate "
        f"is broken. Reply:\n{reply}"
    )
    # The real WOD content should surface. Either the strength name
    # or the WOD title is a sufficient signal — both come from
    # parse_gym_plan() and prove the lookup ran.
    real_content = any(
        marker.lower() in reply.lower()
        for marker in ["Back Squat", "Furiosa", "Tue 19"]
    )
    assert real_content, (
        f"Kobe.route() did not surface real Tuesday WOD content from "
        f"parse_gym_plan(). handle_gym_wod_on was not invoked. "
        f"Reply:\n{reply}"
    )


# ─── 4. Order-of-checks invariant ──────────────────────────────────
def test_should_delegate_checks_gym_wod_guard_before_fraser_patterns():
    """Doubles as documentation: the priority guard MUST run before
    iterating _FRASER_DELEGATION_PATTERNS. If a refactor reorders
    these or removes the guard, this test fires."""
    import inspect, re
    from agents.the_scientist import handler as _handler
    src = inspect.getsource(_handler._should_delegate)
    # Strip the docstring before searching — the docstring mentions
    # both names by design (for documentation) and would make the
    # naive str.find() check ambiguous.
    src_no_doc = re.sub(r'""".*?"""', '', src, count=1, flags=re.DOTALL)
    # The guard CALL must appear before the FRASER pattern loop.
    guard_idx = src_no_doc.find("_is_gym_wod_on_day_query(")
    fraser_idx = src_no_doc.find("_FRASER_DELEGATION_PATTERNS")
    assert guard_idx != -1, (
        "_should_delegate is missing the gym-WOD-day priority guard. "
        "Add it back; this is the 2026-05-18 production fix."
    )
    assert fraser_idx != -1, (
        "_should_delegate doesn't iterate _FRASER_DELEGATION_PATTERNS "
        "— shape changed. Update this test to match the new shape "
        "but keep the priority invariant."
    )
    assert guard_idx < fraser_idx, (
        f"_is_gym_wod_on_day_query call (at char {guard_idx}) must "
        f"run BEFORE the _FRASER_DELEGATION_PATTERNS loop (at char "
        f"{fraser_idx}). Otherwise the generic WOD pattern matches "
        f"first and the day-anchored lookup gets routed to Fraser. "
        f"This is the 2026-05-18 production bug."
    )
