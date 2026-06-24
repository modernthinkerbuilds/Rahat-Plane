"""Scaling guard (2026-06-22, Test-Lead) — the date-sensitive Kobe render
handlers must be DETERMINISTIC across the calendar.

WHY. Today (real date Tue 2026-06-23) the committed F6 hard pin
`test_aligned_cf_day_does_not_get_duplicate_sub_line` went RED because it
reads the real system clock with no freeze, and Monday's CrossFit line
renders struck-through ("⚠️ missed") on any day after Monday. A date-matrix
sweep showed it fails 6 of 7 weekdays. The repo has **no date-freezing
convention** — every date test reads the wall clock — so the next
date-dependent render will silently break CI on some weekday.

This guard freezes "today" to each of the 7 weekdays (stdlib only — no
freezegun) and asserts the public render handlers are exception-free,
non-empty, and structurally invariant (always 7 day rows, always the right
week header). It does NOT pin wording — only the date-robustness contract —
so it stays green as copy evolves but fails the instant a handler's STRUCTURE
depends on which weekday the suite happens to run.
"""
from __future__ import annotations

import datetime as _dt

import pytest

# Modules whose module-level `datetime` name the render path reads.
_PATCH_TARGETS = [
    "agents.the_scientist.handler",
    "agents.the_scientist.state",
    "agents.the_scientist.protocols",
]

# A Monday → Sunday week, so every weekday is exercised.
_WEEK = [_dt.date(2026, 6, 22) + _dt.timedelta(days=i) for i in range(7)]
_IDS = [d.strftime("%a_%Y%m%d") for d in _WEEK]


def _frozen_datetime(fixed: _dt.date):
    noon = _dt.datetime(fixed.year, fixed.month, fixed.day, 12, 0, 0)

    class _Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return noon if tz is None else noon.replace(tzinfo=tz)

        @classmethod
        def today(cls):
            return noon

    return _Frozen


def _freeze(monkeypatch, fixed: _dt.date):
    fake = _frozen_datetime(fixed)
    import importlib
    for modname in _PATCH_TARGETS:
        mod = importlib.import_module(modname)
        if hasattr(mod, "datetime"):
            monkeypatch.setattr(mod, "datetime", fake, raising=False)


def _synced_blob():
    """A dateless-label gym week so the date-aware grid uses its weekday
    fallback — keeps this guard about RENDER determinism, not sync freshness."""
    from agents.the_scientist.protocols import GymDay
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    return [GymDay(label=d, weekday=d, body="Back squat 5x5\nMetcon",
                   strength="Back squat", blockers=[]) for d in days]


def _count_day_rows(out: str) -> int:
    import re
    return len(re.findall(r"(?m)^[ ▶]*\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b:", out))


@pytest.mark.parametrize("fixed", _WEEK, ids=_IDS)
class TestRenderIsDateDeterministic:
    def test_show_plan_this_week_stable(self, fixed, monkeypatch):
        _freeze(monkeypatch, fixed)
        from agents.the_scientist import handler as k
        monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _synced_blob())
        out = k.handle_show_plan(next_week=False)
        assert out and "week" in out.lower(), f"empty/garbled on {fixed:%a}"
        assert _count_day_rows(out) == 7, (
            f"{fixed:%a}: plan rendered {_count_day_rows(out)} day rows, "
            f"expected 7 — structure depends on the weekday. Output:\n{out}")

    def test_show_plan_next_week_stable(self, fixed, monkeypatch):
        _freeze(monkeypatch, fixed)
        from agents.the_scientist import handler as k
        monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _synced_blob())
        out = k.handle_show_plan(next_week=True)
        assert out and _count_day_rows(out) == 7, (
            f"{fixed:%a}: next-week plan not 7 rows. Output:\n{out}")

    def test_daily_burn_breakdown_never_crashes(self, fixed, monkeypatch):
        _freeze(monkeypatch, fixed)
        from agents.the_scientist import handler as k
        monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _synced_blob())
        out = k.handle_daily_burn_breakdown()
        assert isinstance(out, str) and out, f"breakdown empty on {fixed:%a}"

    def test_gym_wod_on_date_is_pure_in_its_arg(self, fixed, monkeypatch):
        # The date-aware lookup must depend ONLY on its target arg, never on
        # 'today' — same arg ⇒ same answer on every weekday.
        _freeze(monkeypatch, fixed)
        from agents.the_scientist import handler as k
        monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _synced_blob())
        out = k.handle_gym_wod_on_date(_dt.datetime(2026, 6, 24))  # a Wed
        assert isinstance(out, str) and out


def test_render_identical_string_across_all_weekdays(monkeypatch):
    """The strongest form: with a FIXED plan + dateless gym blob, next-week's
    render (which has no 'missed/today' cursor) must be byte-identical on every
    weekday. Catches any hidden wall-clock dependency in the next-week path."""
    from agents.the_scientist import handler as k
    outs = set()
    for fixed in _WEEK:
        mp = pytest.MonkeyPatch()
        try:
            _freeze(mp, fixed)
            mp.setattr(k, "parse_gym_plan", lambda *a, **kw: _synced_blob())
            outs.add(k.handle_show_plan(next_week=True))
        finally:
            mp.undo()
    assert len(outs) == 1, (
        f"next-week render differs across weekdays ({len(outs)} variants) — a "
        "wall-clock dependency leaked into a should-be-deterministic path")
