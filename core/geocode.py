"""core.geocode — coordinates from an address or a place name, through
OpenStreetMap's Nominatim (decision D8, 2026-10-03): a documented
public API, one request per second, attribution kept on the record.
No paid maps API.

`geocode_many()` does a list of addresses at once: US street addresses go
to the U.S. Census Bureau geocoder first (free, no key, no one-per-second
limit, so several run at a time), and what it misses goes to Nominatim,
one per second, up to a cap.

Returns an evidence-shaped dict the caller stores as a `geo` fact:
{"lat", "lng", "display_name", "source_url", "source_name",
 "observed_at", "licence"}, or None when nothing matched.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import httpcache, sources

ENDPOINT = "https://nominatim.openstreetmap.org/search"
CENSUS_ENDPOINT = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
_US_ADDRESS = re.compile(r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*(?:,\s*(?:USA|United States))?\s*$")
_UNIT = re.compile(r"\s*(?:#\s*[\w-]+|\b(?:suite|ste|unit|apt|bldg|building|fl|floor|rm|room)\.?\s*#?\s*[\w-]+)(?=\s*,)", re.I)


def street_address(address: str) -> str:
    """The address without its unit ("#106", "Suite 8"): geocoders place the
    building, and a unit number only makes them miss."""
    return " ".join(_UNIT.sub("", address or "").split())


def geocode(query: str, *, now: datetime | None = None, opener=None, timeout: float = 10.0) -> dict | None:
    q = (query or "").strip()
    if not q:
        return None
    url = ENDPOINT + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 0})
    pol = sources.policy_for(url, fetch_robots=lambda u: None)
    if pol["policy"] not in ("api", "allowed"):
        return None
    sources.throttle(url)
    headers = {"User-Agent": sources.user_agent(), "Accept": "application/json"}
    try:
        fetched = httpcache.get(url, headers=headers, timeout=timeout, opener=opener)
        if not fetched.ok:
            return None
        data = json.loads(fetched.body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - a hermetic miss or bad JSON: no match
        return None
    if not isinstance(data, list) or not data:
        return None
    hit = data[0]
    try:
        lat, lng = float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"lat": lat, "lng": lng, "display_name": hit.get("display_name", ""),
            "source_url": url, "source_name": "OpenStreetMap Nominatim",
            "observed_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "licence": hit.get("licence", "Data © OpenStreetMap contributors, ODbL")}


def geocode_census(address: str, *, now: datetime | None = None, opener=None, timeout: float = 10.0) -> dict | None:
    """A US street address through the U.S. Census Bureau geocoder."""
    a = street_address(address)
    if not a or not _US_ADDRESS.search(a):
        return None
    url = CENSUS_ENDPOINT + "?" + urllib.parse.urlencode({"address": a, "benchmark": "Public_AR_Current", "format": "json"})
    pol = sources.policy_for(url, fetch_robots=lambda u: None)
    if pol["policy"] not in ("api", "allowed"):
        return None
    sources.throttle(url)
    headers = {"User-Agent": sources.user_agent(), "Accept": "application/json"}
    try:
        fetched = httpcache.get(url, headers=headers, timeout=timeout, opener=opener)
        if not fetched.ok:
            return None
        data = json.loads(fetched.body.decode("utf-8", "replace"))
        matches = ((data.get("result") or {}).get("addressMatches") or []) if isinstance(data, dict) else []
        if not matches:
            return None
        c = matches[0].get("coordinates") or {}
        lat, lng = float(c["y"]), float(c["x"])
    except Exception:  # noqa: BLE001 - a hermetic miss, bad JSON or no coordinates: no match
        return None
    return {"lat": lat, "lng": lng, "display_name": matches[0].get("matchedAddress", ""),
            "source_url": url, "source_name": "U.S. Census Bureau Geocoder",
            "observed_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "licence": "public domain (U.S. Census Bureau)"}


def geocode_many(addresses: list[str], *, now: datetime | None = None, workers: int = 5,
                 fallback_cap: int = 4) -> dict[str, dict | None]:
    """{address: geocode or None} for a list of addresses. US addresses go to
    the Census geocoder, several at a time; the misses, and anything not a
    US address, go to Nominatim one per second, at most `fallback_cap` of
    them (the rest stay None: their distance is then unknown)."""
    todo = list(dict.fromkeys(a for a in addresses if (a or "").strip()))
    out: dict[str, dict | None] = {a: None for a in todo}
    us = [a for a in todo if _US_ADDRESS.search(street_address(a))]
    if us:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(us)))) as ex:
            for a, g in zip(us, ex.map(lambda x: geocode_census(x, now=now), us)):
                out[a] = g
    for a in [a for a in todo if out[a] is None][:max(0, fallback_cap)]:
        out[a] = geocode(street_address(a), now=now)
    return out
