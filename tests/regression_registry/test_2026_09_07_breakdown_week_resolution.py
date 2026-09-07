"""Regression pin (2026-09-07) — the by-day burn breakdown renders the
week the message names.

LIVE BUG (2026-09-07, Monday 07:08, owner screenshot): "How many
calories did I burn by the day last week", "…by the day past week",
and "…by the day the week starting August 31" ALL returned the CURRENT
week's grid — Monday 0 kcal, six "(upcoming)" rows, "So far: 0 kcal".
Two causes: the dispatcher's daily_breakdown route (correctly) outranks
last_week for by-day phrasings, and handle_daily_burn_breakdown only
knew how to render this week.

THE PINS.
  * resolve_week_monday: 'last/past/previous/prior week' → last week's
    Monday; 'week starting|of|beginning <Mon DD>' and ISO dates → that
    date's Monday (a date that would be in the future rolls back a
    year); no week phrase → this week. ONE resolver, derived from
    week_bounds() — the date-trap rule.
  * The dispatcher passes the message through, so the three live
    phrasings render last week with a 'Total' line and no '(upcoming)'
    rows; the bare ask still renders this week ('So far').
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


def _this_monday(now: datetime) -> datetime:
    from agents.the_scientist.protocols import week_bounds
    return week_bounds(now)[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    from core import io as cio
    now = datetime.now()
    last_mon = _this_monday(now) - timedelta(days=7)
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS raw_vitals (metric_type TEXT, "
                "value REAL, timestamp TEXT)")
    con.execute("DELETE FROM raw_vitals WHERE metric_type='active_calories'")
    for i, kcal in enumerate([587, 552, 1263, 850, 1191, 740, 610]):
        d = last_mon + timedelta(days=i)
        con.execute("INSERT INTO raw_vitals VALUES ('active_calories', ?, ?)",
                    (kcal, f"{d:%Y-%m-%d} 23:59:00"))
    con.commit()
    con.close()
    yield last_mon
    con = sqlite3.connect(str(cio.DB_PATH))
    con.execute("DELETE FROM raw_vitals WHERE metric_type='active_calories'")
    con.commit()
    con.close()


# ── the resolver ──────────────────────────────────────────────────────
def test_resolver_relative_explicit_and_default():
    from agents.the_scientist.handler import resolve_week_monday
    wed = datetime(2026, 9, 9, 8, 0)                     # Wed Sep 9
    assert resolve_week_monday("by the day last week", wed).date() == \
        datetime(2026, 8, 31).date()
    assert resolve_week_monday("burn by day past week", wed).date() == \
        datetime(2026, 8, 31).date()
    assert resolve_week_monday("the week starting August 31", wed).date() == \
        datetime(2026, 8, 31).date()
    assert resolve_week_monday("week of Sep 2", wed).date() == \
        datetime(2026, 8, 31).date()                     # mid-week date → its Monday
    assert resolve_week_monday("week starting 2026-08-24", wed).date() == \
        datetime(2026, 8, 24).date()
    assert resolve_week_monday("give me calories by the day", wed).date() == \
        datetime(2026, 9, 7).date()                      # this week
    # A month/day that would be in the future rolls back a year.
    jan = datetime(2027, 1, 5, 8, 0)
    assert resolve_week_monday("week starting Dec 29", jan).year == 2026


# ── the live messages, verbatim ───────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "How many calories did I burn by the day last week",
    "How many calories did I burn by the day past week",
])
def test_live_phrasings_render_last_week(env, msg):
    from core import dispatcher
    out = dispatcher.dispatch(msg)
    assert out is not None
    assert f"week of {env.strftime('%b %-d')}" in out
    assert "(upcoming)" not in out and "Total:" in out
    assert "1,263 kcal" in out and "5,793 kcal" in out


def test_week_starting_date_phrasing_renders_that_week(env):
    from core import dispatcher
    out = dispatcher.dispatch("How many calories did I burn by the day "
                              f"the week starting {env:%B %-d}")
    assert f"week of {env.strftime('%b %-d')}" in out
    assert "Total:" in out and "5,793 kcal" in out


def test_bare_ask_still_means_this_week(env):
    from core import dispatcher
    from agents.the_scientist.protocols import week_bounds
    this_mon = week_bounds(datetime.now())[0]
    out = dispatcher.dispatch("give me calories by the day")
    assert f"week of {this_mon.strftime('%b %-d')}" in out
    assert "So far:" in out
