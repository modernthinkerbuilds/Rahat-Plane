"""Regression (2026-06-21): "tomorrow's WOD" returned the WRONG DAY.

handle_gym_wod_on matched only the weekday NAME, so for tomorrow=Monday it
returned the synced blob's FIRST Monday ("Mon 15", last Monday) instead of the
one matching tomorrow's date ("Mon 22"). handle_gym_wod_on_date matches the
SugarWOD header's day-of-month, so a relative day resolves to the right week's
WOD — or says "not synced" rather than showing a different day's programming.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core import dispatcher


def _two_week_blob():
    from agents.the_scientist.protocols import GymDay
    return [
        GymDay(label="Mon 15", weekday="Mon", body="Clean & Jerk\nThe Wolf",
               strength="Clean & Jerk", blockers=[]),
        GymDay(label="Mon 22", weekday="Mon", body="Back Squat 5x5\nFran",
               strength="Back Squat", blockers=[]),
    ]


def test_date_aware_picks_the_right_week(monkeypatch):
    from agents.the_scientist import handler as k
    monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _two_week_blob())
    out = k.handle_gym_wod_on_date(datetime(2026, 6, 22))
    assert "Mon 22" in out and "Mon 15" not in out, (
        "the 22nd must select 'Mon 22', not the first Monday in the blob")


def test_date_not_in_blob_says_not_synced(monkeypatch):
    from agents.the_scientist import handler as k
    monkeypatch.setattr(k, "parse_gym_plan", lambda *a, **kw: _two_week_blob())
    out = k.handle_gym_wod_on_date(datetime(2026, 6, 29))
    assert "no WOD synced" in out, (
        "a date absent from the blob must say so, never show a different day")


def test_relative_route_resolves_a_date(monkeypatch):
    """The dispatcher's relative-day route hands a DATE to
    handle_gym_wod_on_date (not a bare weekday index)."""
    captured = {}
    from agents.the_scientist import handler as k

    def _fake(target):
        captured["d"] = target
        return "OK"

    monkeypatch.setattr(k, "handle_gym_wod_on_date", _fake)
    assert dispatcher.dispatch("what is tomorrow's WOD") == "OK"
    assert captured["d"].date() == (datetime.now() + timedelta(days=1)).date()
