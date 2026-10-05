"""Regression (2026-10-05): "tomorrow" and "yesterday" are days on Kobe's clock.

The dispatcher's relative-day routes ("WOD for tomorrow", "calories I burned
yesterday") read the machine's clock, while Kobe's own answers read Kobe's. A
test that froze Kobe's clock still got the machine's tomorrow, so the quality
gate's "WOD for tomorrow" checked a different day on each run and failed only
on a Sunday (when tomorrow's real WOD had a stray asterisk).
Fix: core.dispatcher._kobe_now().
"""
from __future__ import annotations

from datetime import datetime as _real

import pytest

from core import dispatcher


class _SundayEvening(_real):
    @classmethod
    def now(cls, tz=None):
        return _real(2026, 10, 4, 20, 0)


@pytest.fixture
def asked(monkeypatch):
    from agents.the_scientist import handler
    monkeypatch.setattr(handler, "datetime", _SundayEvening)
    days: list[_real] = []
    monkeypatch.setattr(handler, "handle_gym_wod_on_date", lambda d: days.append(d) or "wod")
    monkeypatch.setattr(handler, "handle_daily_burn", lambda d: days.append(d) or "burn")
    return days


@pytest.mark.parametrize("msg,day", [
    ("What is the WOD for tomorrow?", 5),
    ("What’s the WOD for tommorow", 5),
    ("what is tomorrow's WOD", 5),
    ("what was yesterday's WOD", 3),
    ("How many calories did I burn yesterday", 3),
    ("How many calories did I burn today", 4),
])
def test_relative_days_are_days_on_kobes_clock(asked, msg, day):
    assert dispatcher.dispatch(msg) in ("wod", "burn"), msg
    assert [d.day for d in asked] == [day], (msg, asked)
