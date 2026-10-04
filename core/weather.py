"""core.weather — the official forecast with its issuance time, through
the National Weather Service API called as documented (decision D9,
2026-10-03): `api.weather.gov/points/{lat},{lng}` → the forecast and
alert endpoints, identified by the User-Agent. US only.

Returns an evidence-shaped dict: {"issued", "periods": [...],
"hazards": [...], "source_url", "source_name", "observed_at"} or None.
A forecast is stale after the next scheduled issuance; the caller
labels it with its time either way (platform PR-E4).
"""
from __future__ import annotations

import json
from datetime import datetime

from core import httpcache, sources

BASE = "https://api.weather.gov"


def _get(url: str, opener=None, timeout: float = 10.0) -> dict | None:
    pol = sources.policy_for(url, fetch_robots=lambda u: None)
    if pol["policy"] not in ("api", "allowed"):
        return None
    headers = {"User-Agent": sources.user_agent(), "Accept": "application/geo+json, application/json"}
    try:
        fetched = httpcache.get(url, headers=headers, timeout=timeout, opener=opener)
        if not fetched.ok:
            return None
        return json.loads(fetched.body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - a hermetic miss or bad JSON: no forecast
        return None


def forecast(lat: float, lng: float, *, now: datetime | None = None, opener=None) -> dict | None:
    points = _get(f"{BASE}/points/{lat:.4f},{lng:.4f}", opener)
    if not points:
        return None
    props = points.get("properties", {})
    fc_url = props.get("forecast")
    zone = props.get("forecastZone", "")
    out = {"source_url": fc_url or "", "source_name": "National Weather Service",
           "observed_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
           "issued": None, "periods": [], "hazards": []}
    if fc_url:
        fc = _get(fc_url, opener)
        if fc:
            fp = fc.get("properties", {})
            out["issued"] = fp.get("updateTime") or fp.get("generatedAt")
            for p in (fp.get("periods") or [])[:8]:
                out["periods"].append({"name": p.get("name"), "start": p.get("startTime"),
                                       "temp": p.get("temperature"), "unit": p.get("temperatureUnit"),
                                       "wind": p.get("windSpeed"), "short": p.get("shortForecast"),
                                       "rain_pct": (p.get("probabilityOfPrecipitation") or {}).get("value")})
    alerts = _get(f"{BASE}/alerts/active?point={lat:.4f},{lng:.4f}", opener)
    if alerts:
        for a in alerts.get("features") or []:
            ap = a.get("properties", {})
            out["hazards"].append({"event": ap.get("event"), "severity": ap.get("severity"),
                                   "onset": ap.get("onset"), "ends": ap.get("ends") or ap.get("expires"),
                                   "headline": ap.get("headline"), "sent": ap.get("sent")})
    out["zone"] = zone
    return out
