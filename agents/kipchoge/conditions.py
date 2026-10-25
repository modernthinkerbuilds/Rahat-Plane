"""agents.kipchoge.conditions — the weather of a run (PRD v0.3 §6.1, §9.1).

Order for a past run (the readback's change 4, as the owner tightened
it): the Watch's own temperature and humidity when HAE sent them; the
Open-Meteo forecast API's past hours for runs inside 92 days; the
Open-Meteo archive for the backfill. NWS has no point archive and is a
forecast-only fallback for upcoming runs (S2). Each field records its
own source (the Oct 18 workout had humidity and no temperature).

Coordinates are rounded to 0.02° (about 2 km) before any call and the
rounded point is what the cache is keyed on. Network only here, behind
an `http` seam; under RAHAT_TEST_MODE=1 nothing touches the wire unless
a seam or a cassette is given. Responses are cached per rounded point
and day under `vault/kipchoge/wx/`.

Derived: dew point from temperature and relative humidity (Magnus), the
heat index as "apparent" when the API gives none, the PRD's heat sum
(air °F + dew point °F) for the guardrails.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,wind_speed_10m,cloud_cover,uv_index"
FORECAST_PAST_DAYS_MAX = 92
ROUND_DEG = 0.02
USER_AGENT = "rahat-kipchoge/1.0 (a household's running coach; one call per run)"


@dataclass
class Field:
    value: float | None
    unit: str
    source: str


@dataclass
class Conditions:
    temperature_f: Field
    humidity_pct: Field
    dew_point_f: Field
    apparent_f: Field
    wind_mph: Field
    cloud_pct: Field
    uv: Field
    at_local: str = ""
    point: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {k: {"value": v.value, "unit": v.unit, "source": v.source} for k, v in self.__dict__.items()
                if isinstance(v, Field)} | {"at_local": self.at_local, "point": list(self.point) if self.point else None,
                                           "notes": self.notes}

    @property
    def heat_sum(self) -> float | None:
        """Air °F + dew point °F: the PRD's heat guardrail number (§8.3)."""
        if self.temperature_f.value is None or self.dew_point_f.value is None:
            return None
        return round(self.temperature_f.value + self.dew_point_f.value)


def round_point(lat: float, lon: float, step: float = ROUND_DEG) -> tuple[float, float]:
    return round(round(lat / step) * step, 2), round(round(lon / step) * step, 2)


def c_to_f(c: float | None) -> float | None:
    return None if c is None else c * 9.0 / 5.0 + 32.0


def f_to_c(f: float | None) -> float | None:
    return None if f is None else (f - 32.0) * 5.0 / 9.0


def dew_point_c(temp_c: float, rh: float) -> float:
    """Magnus formula (Alduchov & Eskridge coefficients)."""
    a, b = 17.625, 243.04
    rh = max(1.0, min(100.0, rh))
    gamma = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def heat_index_f(temp_f: float, rh: float) -> float:
    """NWS heat index (Rothfusz), meaningful above 80°F; below it, the air
    temperature (with the standard simple adjustment)."""
    simple = 0.5 * (temp_f + 61.0 + (temp_f - 68.0) * 1.2 + rh * 0.094)
    if (simple + temp_f) / 2.0 < 80.0:
        return simple
    t, r = temp_f, rh
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r - 6.83783e-3 * t * t - 5.481717e-2 * r * r
          + 1.22874e-3 * t * t * r + 8.5282e-4 * t * r * r - 1.99e-6 * t * t * r * r)
    return hi


# ── the Watch's own reading ───────────────────────────────────────────
def from_workout_meta(meta: dict) -> dict[str, Field]:
    """temperature and humidity from workout_meta when HAE sent them
    (degF or degC; %)."""
    out: dict[str, Field] = {}
    t = meta.get("temperature") or {}
    if t.get("value") is not None:
        units = (t.get("units") or "").lower()
        f = t["value"] if "f" in units or not units else c_to_f(t["value"])
        out["temperature_f"] = Field(round(f, 1), "°F", "the Watch (workout metadata)")
    h = meta.get("humidity") or {}
    if h.get("value") is not None:
        out["humidity_pct"] = Field(round(h["value"], 1), "%", "the Watch (workout metadata)")
    return out


# ── Open-Meteo ────────────────────────────────────────────────────────
def _cache_path(point: tuple[float, float], day: str) -> Path:
    base = Path(os.getenv("RAHAT_KIPCHOGE_WX_DIR") or "")
    if not base or not str(base):
        from agents.kipchoge.state import kipchoge_dir
        base = kipchoge_dir() / "wx"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{point[0]:.2f}_{point[1]:.2f}_{day}.json"


def _get(url: str, params: dict, http: Callable[[str, dict], dict] | None) -> dict | None:
    if http is not None:
        return http(url, params)
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return None                                  # hermetic: no wire without a seam
    import requests
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()


def hourly_for(point: tuple[float, float], day: str, *, now: datetime, http=None, use_cache: bool = True) -> dict | None:
    """The day's hourly rows at the rounded point: from the cache, the
    forecast API's past days (inside 92 days) or the archive (older).
    Returns Open-Meteo's `hourly` dict (time, temperature_2m, …) with a
    `_source` key, or None when nothing answered."""
    cache = _cache_path(point, day) if use_cache else None
    if cache and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data.setdefault("_source", "cache")
            return data
        except ValueError:
            pass
    age_days = (now.date() - datetime.strptime(day, "%Y-%m-%d").date()).days
    params = {"latitude": point[0], "longitude": point[1], "hourly": HOURLY, "timezone": "auto",
              "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}
    if age_days <= FORECAST_PAST_DAYS_MAX:
        url = FORECAST_URL
        params |= {"past_days": min(FORECAST_PAST_DAYS_MAX, max(0, age_days + 1)), "forecast_days": 1}
        source = "Open-Meteo forecast (past hours)"
    else:
        url = ARCHIVE_URL
        params |= {"start_date": day, "end_date": day}
        source = "Open-Meteo archive"
    try:
        data = _get(url, params, http)
    except Exception:  # noqa: BLE001 — the run is analyzed without weather, and says so
        return None
    if not isinstance(data, dict) or not isinstance(data.get("hourly"), dict):
        return None
    hourly = dict(data["hourly"])
    hourly["_source"] = source
    if cache:
        try:
            cache.write_text(json.dumps(hourly), encoding="utf-8")
        except OSError:
            pass
    return hourly


def _nearest_hour(hourly: dict, at_local: datetime) -> int | None:
    times = hourly.get("time") or []
    best, best_d = None, None
    for i, t in enumerate(times):
        try:
            dt = datetime.strptime(str(t)[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        d = abs((dt - at_local).total_seconds())
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best if best is not None and best_d is not None and best_d <= 2 * 3600 else None


def _col(hourly: dict, name: str, i: int) -> float | None:
    col = hourly.get(name) or []
    try:
        v = col[i]
        return float(v) if v is not None else None
    except (IndexError, TypeError, ValueError):
        return None


# ── the run's conditions ──────────────────────────────────────────────
def conditions_for_run(*, start_local: datetime, point: tuple[float, float] | None, meta: dict, now: datetime,
                       http=None, use_cache: bool = True) -> Conditions:
    """Per field, the first source that has it: the Watch's own reading,
    then Open-Meteo at the rounded start point for the run's hour. Dew
    point and apparent temperature are derived from temperature and
    humidity when the API gave none."""
    none = lambda unit: Field(None, unit, "")  # noqa: E731
    c = Conditions(none("°F"), none("%"), none("°F"), none("°F"), none("mph"), none("%"), none(""),
                   at_local=start_local.strftime("%Y-%m-%d %H:%M"), point=point)
    for k, f in from_workout_meta(meta or {}).items():
        setattr(c, k, f)
    hourly = None
    if point is not None:
        hourly = hourly_for(point, start_local.strftime("%Y-%m-%d"), now=now, http=http, use_cache=use_cache)
    if hourly:
        i = _nearest_hour(hourly, start_local + timedelta(minutes=30))
        src = hourly.get("_source", "Open-Meteo")
        if i is not None:
            if c.temperature_f.value is None:
                c.temperature_f = Field(_col(hourly, "temperature_2m", i), "°F", src)
            if c.humidity_pct.value is None:
                c.humidity_pct = Field(_col(hourly, "relative_humidity_2m", i), "%", src)
            c.dew_point_f = Field(_col(hourly, "dew_point_2m", i), "°F", src)
            c.apparent_f = Field(_col(hourly, "apparent_temperature", i), "°F", src)
            c.wind_mph = Field(_col(hourly, "wind_speed_10m", i), "mph", src)
            c.cloud_pct = Field(_col(hourly, "cloud_cover", i), "%", src)
            c.uv = Field(_col(hourly, "uv_index", i), "", src)
        else:
            c.notes.append("the weather rows did not cover the run's hour")
    elif point is not None:
        c.notes.append("no weather answered for the run's point and day")
    else:
        c.notes.append("no route point: weather not looked up")
    if c.dew_point_f.value is None and c.temperature_f.value is not None and c.humidity_pct.value is not None:
        c.dew_point_f = Field(round(c_to_f(dew_point_c(f_to_c(c.temperature_f.value), c.humidity_pct.value)), 1), "°F",
                              "derived from temperature and humidity (Magnus)")
    if c.apparent_f.value is None and c.temperature_f.value is not None and c.humidity_pct.value is not None:
        c.apparent_f = Field(round(heat_index_f(c.temperature_f.value, c.humidity_pct.value), 1), "°F",
                             "derived: heat index from temperature and humidity")
    return c
