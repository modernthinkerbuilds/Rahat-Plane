"""core.kb.geo — distance from stored coordinates, labeled "about".

No routes API is licensed (decided 2026-10-03), so minutes are
great-circle distance times a per-mode factor (a drive: city streets,
then open road). Every number produced
here is an estimate and the renderer prints it with "about".

The Manhattan grid (`grid: {x, st}` on seed places: x = miles west of
6th Ave, st = street number) converts to coordinates by rotating the
grid 29° off true north from an anchor at 6th Ave and 23rd St. Good to
a block or two, which is what "about 12 minutes" needs.
"""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088

# Minutes per kilometre, straight-line, with a detour factor folded in:
# walking 1.3x at stroller pace; driving 1.4x on city streets; transit
# is walking plus a flat wait.
MODE_FACTORS = {"walk": 15.0 * 1.3, "drive": 2.4 * 1.4, "transit": 6.0 * 1.3}
TRANSIT_WAIT_MIN = 8.0

# Driving is city streets for the first few kilometres, then the roads a
# longer drive takes. LIVE (2026-10-04, a household at home in a suburb):
# one city-street rate for every distance made "I can drive 50 minutes" a
# 15 km circle — a café 38 km up the freeway, a half-hour drive, came back
# as 128 minutes and was left out, and a whole-area ask found one place.
# Checked against ordinary weekend drives: 8 km ≈ 15 min, 14 km ≈ 20,
# 38 km ≈ 35–40, 60 km ≈ 55.
DRIVE_CITY_KM = 4.0
DRIVE_OPEN_MIN_PER_KM = 0.8

_GRID_ANCHOR = (40.7425, -73.9925)   # 6th Ave at 23rd St
_GRID_THETA = math.radians(29.0)     # avenues run 29° east of north
_MILES_PER_STREET = 0.05             # 20 streets to the mile
_MILES_PER_DEG_LAT = 69.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def about_minutes(lat1: float, lng1: float, lat2: float, lng2: float,
                  mode: str = "walk") -> int:
    """Estimated minutes between two points for a travel mode."""
    km = haversine_km(lat1, lng1, lat2, lng2)
    if mode == "drive":
        minutes = min(km, DRIVE_CITY_KM) * MODE_FACTORS["drive"] + max(0.0, km - DRIVE_CITY_KM) * DRIVE_OPEN_MIN_PER_KM
        return max(1, int(round(minutes)))
    factor = MODE_FACTORS.get(mode, MODE_FACTORS["walk"])
    minutes = km * factor + (TRANSIT_WAIT_MIN if mode == "transit" else 0.0)
    return max(1, int(round(minutes)))


def grid_to_latlng(x: float, st: float) -> tuple[float, float]:
    """Manhattan grid → (lat, lng). `x` miles west of 6th Ave (east is
    negative), `st` the street number (below Houston negative)."""
    lat0, lng0 = _GRID_ANCHOR
    d_st_miles = (st - 23.0) * _MILES_PER_STREET
    north = x * math.sin(_GRID_THETA) + d_st_miles * math.cos(_GRID_THETA)
    east = -x * math.cos(_GRID_THETA) + d_st_miles * math.sin(_GRID_THETA)
    lat = lat0 + north / _MILES_PER_DEG_LAT
    lng = lng0 + east / (_MILES_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return (round(lat, 6), round(lng, 6))


def grid_walk_minutes(x1: float, st1: float, x2: float, st2: float) -> int:
    """The seed's own rule: 20 × (|dx| + 0.05 × |dst|) minutes on foot."""
    return max(1, int(round(20.0 * (abs(x1 - x2) + 0.05 * abs(st1 - st2)))))


def detour_minutes(origin: tuple[float, float], dest: tuple[float, float],
                   via: tuple[float, float], mode: str = "drive") -> int:
    """How many extra minutes a stop at `via` adds to origin → dest."""
    direct = about_minutes(*origin, *dest, mode=mode)
    with_stop = about_minutes(*origin, *via, mode=mode) + about_minutes(*via, *dest, mode=mode)
    return max(0, with_stop - direct)
