"""agents.bourdain.location — where the household is, from the phone.

The bot can only know what the phone tells it. Three ways in, one
memory out: the three-hour `location` override in the session store
that the `set_location` tool, a place named in words and a Telegram
share already write (`core.state_scopes`). Every ask, `/list` and `/who`
rank from it; home is the fallback when it lapses.

  * Telegram: a location message or a live location (bot.py).
  * The phone, pushing: an iPhone Shortcut automation ("when Telegram is
    opened", "when I leave home", "when CarPlay disconnects") POSTs
    {lat, lng} to the HealthKit bridge's `/location` (the same bridge the
    Watch data already reaches), or saves {lat, lng, at} to a file in
    iCloud Drive that the Mac sees (`RAHAT_LOCATION_FILE`); the bot reads
    the file before each message (`refresh_from_file`).

Owner, 2026-10-17: "I don't want to keep tapping on location every
time… it's all automatic based on location."

Coordinates are never logged; the override carries them for three hours
in the local database, as the tool's did.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from core import state_scopes as ss
from core.kb import cities, geo

AGENT = "bourdain"
HOURS = 3.0                      # a point stands this long; refreshed by every new point
HOME_KM = 1.0                    # this close to home is home
# A live share sends a point every few seconds while the phone moves; one is written when the household has
# moved this far, or this long has passed — the ranking is no finer than a block, and the store stays small.
LIVE_MIN_KM = 0.15
LIVE_MIN_MINUTES = 10
ENV_FILE = "RAHAT_LOCATION_FILE"
SOURCES = {"telegram": "Telegram", "phone": "your phone", "file": "your phone", "words": "you said", "tool": "you said"}
_PAIR = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")
_FMT = "%Y-%m-%d %H:%M:%S"


def label_for(lat: float, lng: float, *, home: dict | None) -> tuple[str, str | None]:
    """("home", None) within a kilometre of home; else the nearest town from the city table and its key;
    else ("where you are", None)."""
    home = home or {}
    if home.get("lat") is not None and home.get("lng") is not None and \
            geo.haversine_km(lat, lng, float(home["lat"]), float(home["lng"])) <= HOME_KM:
        return "home", None
    near = cities.nearest(lat, lng)
    if near:
        return near, cities.key(near)
    return "where you are", None


def remember(chat_id: str, lat: float, lng: float, *, now: datetime, source: str = "phone", live: bool = False,
             at: datetime | None = None, home: dict | None = None, db_path: str | Path | None = None,
             only_if_moved: bool = False) -> dict:
    """Write the three-hour override for a point the household is at. Returns {label, city, source, written}.
    With `only_if_moved` (a live share's later points) nothing is written while the point is within
    LIVE_MIN_KM of the one in force and that one is younger than LIVE_MIN_MINUTES."""
    if only_if_moved:
        cur = current(str(chat_id), now=now, db_path=db_path)
        if cur and cur.get("lat") is not None and cur.get("age_min") is not None \
                and cur["age_min"] < LIVE_MIN_MINUTES \
                and geo.haversine_km(lat, lng, float(cur["lat"]), float(cur["lng"])) < LIVE_MIN_KM:
            return {"label": cur.get("label"), "city": cur.get("city"), "source": cur.get("source"), "written": False}
    label, city = label_for(lat, lng, home=home)
    value = {"label": label, "lat": float(lat), "lng": float(lng), "city": city, "shared": True, "live": bool(live),
             "source": source, "at": (at or now).strftime(_FMT)}
    ss.put(AGENT, str(chat_id), "location", value, scope="session", now=now, replace=True, expires_in_hours=HOURS,
           db_path=db_path)
    return {"label": label, "city": city, "source": source, "written": True}


def current(chat_id: str, *, now: datetime, db_path: str | Path | None = None) -> dict | None:
    """The override in force, with `age_min` and `source_label`, or None."""
    rows = ss.get_all(AGENT, str(chat_id), "location", now=now, db_path=db_path)
    if not rows:
        return None
    row = rows[-1]
    value = dict(row["value"] or {})
    at = value.get("at") or row.get("created_at")
    try:
        age = max(0, int((now - datetime.strptime(str(at), _FMT)).total_seconds() // 60))
    except (TypeError, ValueError):
        age = None
    value["age_min"] = age
    value["source_label"] = SOURCES.get(str(value.get("source") or ""), "you said")
    value["expires_at"] = row.get("expires_at")
    value["created_at"] = row.get("created_at")
    return value


def where_line(chat_id: str, *, now: datetime, db_path: str | Path | None = None) -> str | None:
    """"Berkeley · your phone, 12 min ago" for `/who`, or None when nothing is in force."""
    cur = current(chat_id, now=now, db_path=db_path)
    if not cur or cur.get("lat") is None:
        return None
    age = cur.get("age_min")
    when = "just now" if age is not None and age < 1 else (f"{age} min ago" if age is not None and age < 120
                                                            else (f"{age // 60} h ago" if age is not None else ""))
    return f"{cur.get('label') or 'where you are'} · {cur['source_label']}" + (f", {when}" if when else "")


def parse_point(text: str) -> tuple[float, float, datetime | None] | None:
    """A point from a file the phone wrote: JSON {lat, lng, at?} (`latitude`/`longitude` too), or "lat, lng"."""
    t = (text or "").strip()
    if not t:
        return None
    m = _PAIR.match(t)
    if m:
        return float(m.group(1)), float(m.group(2)), None
    try:
        d = json.loads(t)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    lat = d.get("lat", d.get("latitude"))
    lng = d.get("lng", d.get("lon", d.get("longitude")))
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    at = None
    raw_at = d.get("at") or d.get("timestamp") or d.get("date")
    if raw_at:
        at = _parse_when(str(raw_at))
    return lat, lng, at


def _parse_when(s: str) -> datetime | None:
    s = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d %b %Y at %H:%M", "%b %d, %Y at %I:%M %p"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.tzinfo is not None:                     # the phone's ISO stamp carries an offset: make it local-naive
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    return None


def from_file(path: str | Path, *, now: datetime) -> dict | None:
    """{lat, lng, at} from the phone's file, or None when it is missing, unreadable or older than HOURS."""
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        return None
    pt = parse_point(text)
    if not pt:
        return None
    lat, lng, at = pt
    at = at or mtime
    if at > now + timedelta(minutes=5) or now - at > timedelta(hours=HOURS):
        return None
    return {"lat": lat, "lng": lng, "at": at}


def refresh_from_file(chat_id: str, *, now: datetime, path: str | Path | None = None, home: dict | None = None,
                      db_path: str | Path | None = None) -> bool:
    """Before a message: if the phone's file holds a newer point than the override in force, remember it.
    True when something was written. Cheap (a stat and a small read); never raises."""
    path = path or os.getenv(ENV_FILE)
    if not path:
        return False
    try:
        pt = from_file(path, now=now)
        if not pt:
            return False
        if home is None:
            home = household_home()
        cur = current(chat_id, now=now, db_path=db_path)
        cur_at = (cur or {}).get("at") or (cur or {}).get("created_at")
        if cur_at and pt["at"].strftime(_FMT) <= str(cur_at):      # nothing newer than what is in force
            return False
        remember(chat_id, pt["lat"], pt["lng"], now=now, source="file", at=pt["at"], home=home, db_path=db_path)
        return True
    except Exception:  # noqa: BLE001 - a feed never breaks a message
        return False


def household_home() -> dict:
    """The profile's home (lat, lng, city) for the labels; {} when the profile can't be read."""
    try:
        from agents.bourdain import profile as cprofile
        return dict((cprofile.load() or {}).get("home") or {})
    except Exception:  # noqa: BLE001
        return {}


def primary_chat() -> str | None:
    """The owner's chat: the configured primary, else the household registry's."""
    env = (os.getenv("BOURDAIN_PRIMARY_CHAT") or "").strip()
    if env:
        return env
    try:
        from agents.bourdain import state
        for cid, info in state.list_household_chats().items():
            if (info or {}).get("role") == "primary":
                return str(cid)
    except Exception:  # noqa: BLE001
        pass
    return None
