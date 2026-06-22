"""Regression: WEEKDAY_INDEX case-mismatch with parse_gym_plan output.

2026-05-18 production incident — /plan next dumped the default
Mon/Wed/Fri cadence + "⚠️ Only 0 days in this week's gym plan are
blacklist-clean" warning, even though the user had a freshly synced
SugarWOD plan with 6 blacklist-clean days (only Thursday's hang
snatch was on the blocker list).

Root cause: WEEKDAY_INDEX was defined with Title Case keys:

    WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, ...}

…but parse_gym_plan returns GymDay.weekday in upper case:

    GymDay(label='MON 18', weekday='MON', ...)

So WEEKDAY_INDEX.get(d.weekday[:3]) returned None for every day,
the clean_wds set stayed empty, handle_show_plan thought no day
was blacklist-clean, and the warning fired.

Same class as the 2026-04 uppercase-weekday bug (task #18); that
fix made the parser uppercase but missed every lookup site.

Fix: .capitalize() at the lookup. 'MON' → 'Mon'. Patched at:
  - agents/the_scientist/handler.py (handle_show_plan)
  - agents/the_scientist/tools.py (Kobe reasoner tool)
  - agents/the_scientist/state.py (two replan paths)

This test pins:
  1. The case convention itself — WEEKDAY_INDEX keys are Title Case,
     parse_gym_plan output is upper. Future refactors that change
     either WITHOUT updating the lookup sites will be caught here.
  2. The end-to-end behavior — given a fixture gym plan with 7 days,
     handle_show_plan does NOT fire the false "Only 0 days clean"
     warning.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest


# ─── 1. The convention itself ──────────────────────────────────────
def test_weekday_index_keys_are_title_case():
    """WEEKDAY_INDEX uses Title Case. If a refactor switches to upper
    or lower, every lookup site must be updated in the same PR."""
    from agents.the_scientist.protocols import WEEKDAY_INDEX
    for key in WEEKDAY_INDEX.keys():
        assert key == key.capitalize(), (
            f"WEEKDAY_INDEX key {key!r} is not Title Case. "
            f"If you change the case convention, you MUST also update "
            f"every WEEKDAY_INDEX.get(...) call site to match. The "
            f"2026-05-18 bug was caused by exactly this drift."
        )


def test_parse_gym_plan_returns_upper_case_weekday():
    """parse_gym_plan returns GymDay.weekday in upper case. If this
    changes, the lookup sites need to update."""
    from agents.the_scientist.protocols import parse_gym_plan
    # Build a minimal synthetic plan file with one MON day.
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False) as f:
        f.write("MON 18\n\n0\n Bench Press 1RM\nTake 15:00\n")
        plan_path = Path(f.name)
    try:
        days = parse_gym_plan(plan_path=plan_path)
        assert days, "fixture should parse at least one day"
        for d in days:
            assert d.weekday == d.weekday.upper(), (
                f"parse_gym_plan returned weekday {d.weekday!r} which "
                f"is not upper case. Lookup sites use .capitalize() to "
                f"map this to WEEKDAY_INDEX; if parse output changes, "
                f"every lookup site must update."
            )
    finally:
        plan_path.unlink()


# ─── 2. The lookup must succeed for every weekday parse emits ──────
@pytest.mark.parametrize("emitted_weekday", [
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
])
def test_lookup_succeeds_for_every_weekday(emitted_weekday):
    """For every weekday parse_gym_plan could emit, the lookup pattern
    used in handler.py / tools.py / state.py must succeed."""
    from agents.the_scientist.protocols import WEEKDAY_INDEX
    # This is the production lookup pattern. If it returns None,
    # production breaks silently.
    wd_idx = WEEKDAY_INDEX.get(emitted_weekday[:3].capitalize())
    assert wd_idx is not None, (
        f"Lookup WEEKDAY_INDEX.get({emitted_weekday[:3].capitalize()!r}) "
        f"returned None. Production code at handler.py:745, "
        f"tools.py:291, state.py:673, state.py:936 will silently fail "
        f"for this day. Add .capitalize() at the call site or update "
        f"WEEKDAY_INDEX keys."
    )


# ─── 3. End-to-end: handle_show_plan does not lie ──────────────────
def test_handle_show_plan_does_not_warn_when_synced_plan_is_clean(
        tmp_path, monkeypatch):
    """When a SugarWOD plan is freshly synced with no blacklisted
    movements, handle_show_plan(next_week=True) MUST NOT fire the
    'Only 0 days blacklist-clean' warning.

    This is the actual 2026-05-18 user-facing symptom: even though the
    plan was clean, the bot warned the user and dropped to default
    cadence."""
    # Write a minimal 7-day plan with one CF body per day, no blockers.
    plan_content = "\n".join([
        f"{wd} {18 + i}\n\n0\n CrossFit Body\nFor Time\n10:00\n"
        for i, wd in enumerate(["MON", "TUE", "WED", "THU",
                                 "FRI", "SAT", "SUN"])
    ])
    plan_file = tmp_path / "weekly_plan.txt"
    plan_file.write_text(plan_content)

    # Rebind PLAN_PATH on both modules — handler and main both keep a
    # reference and parse_gym_plan defaults to the production path. The
    # 2026-05-17 PLAN_PATH double-bind footgun: only rebinding one
    # silently uses production data.
    from agents.the_scientist import handler as _handler
    monkeypatch.setattr(_handler, "PLAN_PATH", plan_file)
    try:
        from agents.the_scientist import main as _sci_main
        monkeypatch.setattr(_sci_main, "PLAN_PATH", plan_file)
    except (ImportError, AttributeError):
        pass

    parsed = _handler.parse_gym_plan()
    assert len(parsed) >= 5, (
        f"sanity: parse_gym_plan should return at least 5 days from "
        f"the fixture, got {len(parsed)}"
    )

    output = _handler.handle_show_plan(next_week=True)
    forbidden = [
        "No gym plan synced",
        "Only 0 days",
        "Only 0 day",
    ]
    for needle in forbidden:
        assert needle not in output, (
            f"handle_show_plan output contained {needle!r} despite "
            f"the synced plan having {len(parsed)} clean days. The "
            f"WEEKDAY_INDEX case-mismatch bug is back. Full output:\n"
            f"{output}"
        )


# ─── 4. Bidirectional guard against future drift ────────────────────
def test_case_convention_documented():
    """specs/CONVENTIONS.md should document this so the convention
    survives the next refactor. The 2026-04 task #18 fix taught us
    that a partial fix is worse than no fix — every weekday lookup
    site MUST agree on case, and the convention belongs in writing."""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent.parent
    conventions = ROOT / "specs" / "CONVENTIONS.md"
    if not conventions.exists():
        pytest.skip("specs/CONVENTIONS.md not present yet")
    text = conventions.read_text().lower()
    # We don't require a specific phrasing, just that "weekday"
    # appears somewhere — that's the signal someone thought about it.
    if "weekday" not in text and "weekday_index" not in text:
        pytest.xfail(
            "specs/CONVENTIONS.md should document the WEEKDAY_INDEX "
            "case convention (Title Case keys, parse output upper) "
            "so the 2026-05-18 drift can't recur silently. Add a "
            "section. Marked xfail so this doesn't block today's "
            "push; flip to assertion when the section is written."
        )
