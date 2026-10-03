"""Regression pin (2026-10-03) — the temporary-members pins must not
depend on the wall clock.

INCIDENT: `test_2026_08_10_genie_temporary_members.py` fixes the
in-laws' visit window at `present_until: 2026-10-01` and let the
handler read the real date. On 2026-10-02 the window closed, the
in-laws dropped out of every plan, and three pins went red with no
code change. The fix freezes the clock inside the window (the repo's
date-determinism convention, tests/datefreeze.py).

This pin guards both halves: the presence rule itself with explicit
dates, and the convention in the 08-10 file.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from agents.genie.protocols import FamilySubject


def _senior(until: str | None) -> FamilySubject:
    return FamilySubject(role="senior", subject_id="visitor-1", display="Visitor",
                         present_until=until or "")


def test_presence_rule_with_explicit_dates():
    s = _senior("2026-10-01")
    assert s.is_present_on(date(2026, 9, 20))
    assert s.is_present_on(date(2026, 10, 1))
    assert not s.is_present_on(date(2026, 10, 2))
    assert _senior(None).is_present_on(date(2030, 1, 1))
    assert _senior("not a date").is_present_on(date(2030, 1, 1))   # fails open


def test_temporary_members_pins_freeze_the_clock():
    src = (Path(__file__).parent / "test_2026_08_10_genie_temporary_members.py").read_text()
    assert "freeze(" in src and "agents.genie.protocols" in src, (
        "the 08-10 pins must freeze the clock inside the fixture's visit window")
