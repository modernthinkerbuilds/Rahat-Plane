"""Regression pin (2026-10-04): a drive's minutes count the open road.

LIVE (2026-10-04, at home in a suburb): "I can drive 50 minutes" found
one or two places. Drive minutes were straight-line kilometres times one
city-street rate (about 3.4 min/km), so a 50-minute budget was a 15 km
circle: a café 38 km up the freeway — a half-hour drive — came back from
route_time as 128 minutes and was left out, and anything past the next
town failed the distance check.

THE PINS (core.kb.geo.about_minutes, mode "drive").
  * The first few kilometres keep the city rate (a city leg's short drive
    is unchanged); past them, the open-road rate.
  * Ordinary drives land where a driver expects them; minutes never fall
    as the distance grows; walking is unchanged.
  * A 50-minute budget reaches a place 38 km away, and not one 80 km away.
(Coordinates below are arbitrary points, not a household's.)
"""
from __future__ import annotations

from core.kb import geo

ORIGIN = (10.0, 20.0)


def _north(km: float) -> tuple[float, float]:
    return (ORIGIN[0] + km / 111.195, ORIGIN[1])


def _drive(km: float) -> int:
    return geo.about_minutes(*ORIGIN, *_north(km), mode="drive")


def test_short_city_drives_are_unchanged():
    for km in (0.5, 1.6, 3.0, 4.0):
        assert _drive(km) == max(1, round(km * geo.MODE_FACTORS["drive"]))


def test_ordinary_drives_land_where_a_driver_expects():
    assert 12 <= _drive(8) <= 18
    assert 18 <= _drive(14) <= 24
    assert 35 <= _drive(38) <= 45          # was 128
    assert 50 <= _drive(60) <= 62


def test_minutes_never_fall_and_walking_is_unchanged():
    mins = [_drive(k / 2) for k in range(0, 200)]
    assert mins == sorted(mins)
    walk = geo.about_minutes(*ORIGIN, *_north(1.6), mode="walk")
    assert walk == round(geo.haversine_km(*ORIGIN, *_north(1.6)) * geo.MODE_FACTORS["walk"])


def test_a_fifty_minute_budget_reaches_the_next_towns():
    assert _drive(38) <= 50 < _drive(80)
