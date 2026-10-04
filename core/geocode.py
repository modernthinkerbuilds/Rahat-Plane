"""core.geocode — coordinates from an address or a place name, through
OpenStreetMap's Nominatim (decision D8, 2026-10-03): a documented
public API, one request per second, attribution kept on the record.
No paid maps API.

Returns an evidence-shaped dict the caller stores as a `geo` fact:
{"lat", "lng", "display_name", "source_url", "source_name",
 "observed_at", "licence"}, or None when nothing matched.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

from core import httpcache, sources

ENDPOINT = "https://nominatim.openstreetmap.org/search"


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
