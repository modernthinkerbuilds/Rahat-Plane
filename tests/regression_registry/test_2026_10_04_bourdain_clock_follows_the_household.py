"""Regression pin (2026-10-04): Bourdain's clock is where the household
is: the trip's zone while a trip leg is on, the home zone otherwise.

LIVE (2026-10-04, owner: "Bourdain seems to report times in EST?"):
new_plane/bourdain_runner/bot.trip_now() returned Eastern time whenever a
trip was LOADED in the store, not when one was on. The September trip
(Sep 21–27) stayed loaded, so a week after the family flew home every
ask ran three hours ahead: the "Checked" time, the hours check for
"open now", and which evening "tonight" meant.

THE PINS.
  * During a trip leg (by the leg's own local date): trip time.
  * After the trip (still loaded): the profile's home zone.
  * No trip at all: the home zone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from new_plane.bourdain_runner import bot

TRIP = {"stays": {"alpha_city": {"dates": "Mon Sep 21 – Thu Sep 24"},
                  "beta_city": {"dates": "Thu Sep 24 – Sun Sep 27"}}}
PROFILE = {"version": 1, "members": {"primary": {"role": "primary"}},
           "household": {"rules": []}, "home": {"tz": "America/Los_Angeles"}}


@pytest.fixture
def household(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    store = tmp_path / "store.json"
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps(PROFILE))
    monkeypatch.setenv("RAHAT_BOURDAIN_STORE_JSON", str(store))
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(prof))

    def load(trip):
        store.write_text(json.dumps({"trip": trip} if trip else {}))
    return load


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_during_a_trip_leg_the_clock_is_trip_time(household):
    household(TRIP)
    assert bot.trip_now(_utc(2026, 9, 22, 22, 40)) == datetime(2026, 9, 22, 18, 40)     # Eastern


def test_after_the_trip_the_clock_is_home_time(household):
    household(TRIP)                                                                    # still loaded
    assert bot.trip_now(_utc(2026, 10, 4, 17, 5)) == datetime(2026, 10, 4, 10, 5)      # Pacific, not 13:05


def test_with_no_trip_the_clock_is_home_time(household):
    household(None)
    assert bot.trip_now(_utc(2026, 10, 4, 17, 5)) == datetime(2026, 10, 4, 10, 5)
