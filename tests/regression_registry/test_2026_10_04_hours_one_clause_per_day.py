"""Regression pin (2026-10-04): hours written one day per sentence parse
per day.

Before the fix, Google's own format — "Monday: 12pm-11pm. Tuesday:
12pm-11pm. …" — was one clause: the first day spec (Monday) took all
seven ranges and every other day had none, so a parlor open 12–11 every
day read as "not open" on a Tuesday afternoon (golden case T13).
Day specs separated only by periods or spaces now start their own
clause once the previous clause owns a time range.
"""
from __future__ import annotations

import pytest

from core import hours

GOOGLE = ("Monday: 12pm-11pm. Tuesday: 12pm-11pm. Wednesday: 12pm-11pm. Thursday: 12pm-11pm. "
          "Friday: 12pm-12am. Saturday: 12pm-12am. Sunday: 12pm-11pm.")


@pytest.mark.parametrize("weekday,minute,open_", [
    (1, 15 * 60 + 10, True),        # Tuesday 3:10 PM: the T13 ask
    (4, 23 * 60 + 30, True),        # Friday 11:30 PM: open till midnight
    (0, 23 * 60 + 30, False),       # Monday 11:30 PM: closed at 11
    (6, 11 * 60, False),            # Sunday 11 AM: opens at noon
])
def test_google_format_parses_per_day(weekday, minute, open_):
    h = hours.parse(GOOGLE)
    assert {i.weekday for i in h.intervals} == set(range(7)) and not h.unparsed
    assert hours.open_at(h, weekday, minute)["open"] is open_


def test_closed_and_all_day_clauses():
    h = hours.parse("Monday: Closed. Tuesday: 11am-9pm. Wednesday: Open 24 hours.")
    assert hours.open_at(h, 0, 12 * 60)["open"] is False and 0 in h.closed_days
    assert hours.open_at(h, 1, 12 * 60)["open"] is True
    assert hours.open_at(h, 2, 3 * 60)["open"] is True
    h = hours.parse("Mon–Sat 11–9, Sun closed")
    assert hours.open_at(h, 6, 12 * 60)["open"] is False and hours.open_at(h, 5, 20 * 60)["open"] is True


def test_listed_days_are_unioned_and_split_days_keep_their_ranges():
    h = hours.parse("Mon, Wed, Fri 9–5")
    assert sorted({i.weekday for i in h.intervals}) == [0, 2, 4]
    h = hours.parse("Sat 11:30–15:00, 16:00–22:00")
    assert len([i for i in h.intervals if i.weekday == 5]) == 2
