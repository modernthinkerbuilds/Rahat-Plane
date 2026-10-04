"""core.harness.evidence — what the verifier, the renderer and the judge
see of one answer's evidence (ADR-018 §2.2 steps 4, 5 and 7).

`Evidence` is a read-only view over the knowledge base for the places an
answer names: the place row, every current fact with its source and
read time, the newest counted rating, the hours (parsed for a weekday
and minute), the menu items, the acclaim, the household's verdicts and
the coordinates. It caches per place, never writes, and never consults
the network. Timing helpers turn a frame's time spec into the moment
the hours must hold for (`arrival_for`).

The same object is serialized for the judge (`for_judge()`): facts
only, with their ids, so a verdict can cite record ids and nothing else.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from core import hours as chours
from core.kb import geo, query, schema as kb

CLAIMS = ("hours", "status", "price", "menu", "room", "acclaim", "location_count", "phone",
          "map_url", "yelp_url", "why", "note", "reservations", "payment", "seating", "format",
          "dish_note:primary", "dish_note:spouse", "transit", "minutes_from_stay", "geo")

# When a frame names a meal but no time, the hours are checked for this
# minute of the day (local). The executor may override with `arrival`.
MEAL_DEFAULT_MINUTE = {"breakfast": 8 * 60 + 30, "coffee": 7 * 60 + 30, "brunch": 11 * 60,
                       "lunch": 12 * 60 + 30, "snack": None, "dessert": 20 * 60 + 30,
                       "gelato": 20 * 60 + 30, "ice cream": 20 * 60 + 30, "hot chocolate": None,
                       "dinner": 19 * 60, "late": None, "drinks": 21 * 60}
DEFAULT_TRAVEL_MIN = 20


def _value(f: dict | None) -> Any:
    if not f:
        return None
    v = f.get("value")
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


# Status words as sources write them → the few the checks read. LIVE
# (2026-10-04): a search result recorded "Open", the check compared with
# "open", and a cozy café open that morning was dropped as "status is Open".
_STATUS_WORDS = {
    "open": "open", "opened": "open", "open now": "open", "currently open": "open", "operational": "open",
    "operating": "open", "business_operational": "open", "active": "open",
    "closed temporarily": "temporarily_closed", "temporarily closed": "temporarily_closed",
    "temporarily_closed": "temporarily_closed", "closed_temporarily": "temporarily_closed",
    "closed permanently": "permanently_closed", "permanently closed": "permanently_closed",
    "permanently_closed": "permanently_closed", "closed_permanently": "permanently_closed",
    "closed": "closed", "moved": "moved", "relocated": "moved", "coming soon": "coming_soon", "coming_soon": "coming_soon",
    "seasonal": "seasonal", "closed for the season": "seasonal", "unknown": "unknown",
}


def normalize_status(value: str) -> str:
    v = " ".join(str(value or "").strip().lower().replace("-", " ").split())
    if v in _STATUS_WORDS:
        return _STATUS_WORDS[v]
    if v.startswith(("open", "opens")):          # "open until 5 pm", "opens at 8"
        return "open"
    return v.replace(" ", "_") or "unknown"


@dataclass
class PlaceEvidence:
    place_id: str
    place: dict | None
    facts: dict[str, dict] = field(default_factory=dict)      # claim → current fact (with fact_id)
    rating: dict | None = None
    menu: list[dict] = field(default_factory=list)
    acclaim: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    hours_rows: list[dict] = field(default_factory=list)
    saved: bool = False

    @property
    def name(self) -> str:
        return (self.place or {}).get("name") or self.place_id

    @property
    def exists(self) -> bool:
        return self.place is not None

    def value(self, claim: str) -> Any:
        return _value(self.facts.get(claim))

    def fact_id(self, claim: str) -> int | None:
        f = self.facts.get(claim)
        return int(f["fact_id"]) if f else None

    @property
    def latlng(self) -> tuple[float, float] | None:
        p = self.place or {}
        if p.get("lat") is not None and p.get("lng") is not None:
            return (p["lat"], p["lng"])
        g = self.value("geo")
        if isinstance(g, dict) and g.get("lat") is not None:
            return (g["lat"], g["lng"])
        return None

    @property
    def price_level(self) -> str | None:
        v = self.value("price")
        if isinstance(v, str) and v.strip():
            return v.strip()
        return (self.place or {}).get("price_level")

    @property
    def status(self) -> str:
        v = self.value("status")
        if isinstance(v, str) and v.strip():
            return normalize_status(v)
        return normalize_status((self.place or {}).get("status") or "unknown")

    @property
    def location_count(self) -> int | None:
        v = self.value("location_count")
        try:
            if v is not None:
                return int(v)
        except (TypeError, ValueError):
            pass
        return (self.place or {}).get("location_count")

    @property
    def room(self) -> dict:
        """{bar_led, seafood_led, text}: from a structured room fact, the
        room notes text, or the seed's room tags."""
        v = self.facts.get("room", {}).get("value") if self.facts.get("room") else None
        out: dict[str, Any] = {"bar_led": None, "seafood_led": None, "text": ""}
        if isinstance(v, dict):
            out["bar_led"] = v.get("bar_led")
            out["seafood_led"] = v.get("seafood_led")
            out["text"] = str(v.get("value") or v.get("text") or "")
        elif isinstance(v, str):
            out["text"] = v
        tags = " ".join((self.place or {}).get("room_tags") or [])
        text = (out["text"] + " " + tags).lower()
        if out["bar_led"] is None and text:
            if re.search(r"\b(wine bar|cocktail bar|pub|taproom|brewery|bar-led|bar led|sports bar|beer hall)\b", text):
                out["bar_led"] = True
        if out["seafood_led"] is None and text:
            if re.search(r"\b(seafood-led|seafood led|oyster bar|raw bar|fish house|crab shack|lobster)\b", text):
                out["seafood_led"] = True
        return out

    def hours(self) -> chours.Hours:
        """Hours from the current hours fact, else from the kb_hours rows."""
        text = self.value("hours")
        if isinstance(text, str) and text.strip():
            h = chours.parse(text)
            if h.parsed:
                return h
        h = chours.Hours(source_text=str(text or ""))
        for r in self.hours_rows:
            if r.get("open_min") is not None and r.get("close_min") is not None:
                h.intervals.append(chours.Interval(weekday=int(r["weekday"]), open_min=int(r["open_min"]),
                                                   close_min=int(r["close_min"]), service=r.get("service") or "all",
                                                   text=r.get("note") or ""))
        if not h.parsed and isinstance(text, str) and text.strip():
            h.unparsed.append(text)
        return h

    def menu_match(self, dish: str) -> dict | None:
        """The menu item a diner line names, by token overlap."""
        d = _tokens(dish)
        if not d:
            return None
        best, score = None, 0.0
        for item in self.menu:
            t = _tokens(item.get("name", ""))
            if not t:
                continue
            s = len(d & t) / len(d | t)
            if s > score:
                best, score = item, s
        return best if score >= 0.34 else None

    def as_json(self) -> dict:
        return {"place_id": self.place_id, "name": self.name, "exists": self.exists,
                "city": (self.place or {}).get("city"), "address": (self.place or {}).get("address"),
                "cuisine": (self.place or {}).get("cuisine"), "family": (self.place or {}).get("cuisine_family"),
                "price_level": self.price_level, "status": self.status, "location_count": self.location_count,
                "rating": self.rating, "room": self.room, "saved": self.saved,
                "facts": {c: {"fact_id": f["fact_id"], "value": _value(f), "source_url": f.get("source_url"),
                              "source_name": f.get("source_name"), "observed_at": f.get("observed_at"),
                              "method": f.get("method")} for c, f in self.facts.items()},
                "menu": self.menu[:30], "acclaim": self.acclaim[:10], "verdicts": self.verdicts[:10],
                "latlng": self.latlng}


def _tokens(s: str) -> set[str]:
    stop = {"the", "a", "an", "and", "with", "of", "or", "for", "in", "on", "to", "gf", "gluten", "free"}
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split() if w not in stop and len(w) > 2}


class Evidence:
    """A read-only, cached view over the knowledge base for one answer."""

    def __init__(self, con: sqlite3.Connection, *, now: datetime, mode: str = "home"):
        self.con = con
        self.now = now
        self.mode = mode
        self._places: dict[str, PlaceEvidence] = {}
        self._facts_by_id: dict[int, dict] = {}
        self._saved: set[str] | None = None

    def refresh(self) -> None:
        """Forget what was read about each place. A repair round can record
        new facts (a rating, the hours, the place's own status) for a place
        the first answer already named; the checks must see them. Records by
        id never change, so those stay."""
        self._places.clear()
        self._saved = None

    # ---- places ----
    def place(self, place_id: str) -> PlaceEvidence:
        if place_id in self._places:
            return self._places[place_id]
        row = query.place(self.con, place_id)
        pe = PlaceEvidence(place_id=place_id, place=row)
        if row:
            for claim in CLAIMS:
                f = kb.current_fact(self.con, "place", place_id, claim)
                if f:
                    f.setdefault("subject_type", "place")
                    f.setdefault("subject_id", place_id)
                    pe.facts[claim] = f
                    self._facts_by_id[int(f["fact_id"])] = f
            pe.rating = query.rating(self.con, place_id)
            pe.menu = [dict(zip(("item_id", "fact_id", "name", "price_text", "diet_tags", "gf", "day_availability", "note"), r))
                       for r in self.con.execute(
                           "SELECT item_id, fact_id, name, price_text, diet_tags, gf, day_availability, note "
                           "FROM kb_menu_item WHERE place_id = ? ORDER BY item_id", (place_id,))]
            pe.acclaim = [dict(zip(("acclaim_id", "writer", "rank", "date", "url", "quote", "fact_id"), r))
                          for r in self.con.execute(
                              "SELECT acclaim_id, writer, rank, date, url, quote, fact_id FROM kb_acclaim "
                              "WHERE place_id = ? ORDER BY date DESC", (place_id,))]
            pe.verdicts = [dict(zip(("kind", "value", "text", "at", "member_id"), (r[0], json.loads(r[1]) if r[1] else None, r[2], r[3], r[4])))
                           for r in self.con.execute(
                               "SELECT kind, value_json, text, at, member_id FROM kb_verdict WHERE place_id = ? "
                               "ORDER BY at DESC", (place_id,))]
            pe.hours_rows = [dict(zip(("weekday", "open_min", "close_min", "service", "note"), r))
                             for r in self.con.execute(
                                 "SELECT weekday, open_min, close_min, service, note FROM kb_hours WHERE place_id = ? "
                                 "ORDER BY hours_id", (place_id,))]
            pe.saved = place_id in self.saved_ids()
        self._places[place_id] = pe
        return pe

    def saved_ids(self) -> set[str]:
        if self._saved is None:
            self._saved = query.saved_ids(self.con)
        return self._saved

    def fact(self, fact_id: int) -> dict | None:
        if fact_id in self._facts_by_id:
            return self._facts_by_id[fact_id]
        row = self.con.execute(
            "SELECT fact_id, subject_type, subject_id, claim_type, value_json, value_text, source_url, "
            "source_name, source_rank, method, observed_at, freshness_hours FROM kb_fact WHERE fact_id = ?",
            (fact_id,)).fetchone()
        if not row:
            return None
        keys = ["fact_id", "subject_type", "subject_id", "claim_type", "value_json", "value_text", "source_url",
                "source_name", "source_rank", "method", "observed_at", "freshness_hours"]
        d = dict(zip(keys, row))
        d["value"] = json.loads(d["value_json"]) if d["value_json"] else d["value_text"]
        self._facts_by_id[fact_id] = d
        return d

    def is_fresh(self, f: dict | None) -> bool:
        return bool(f) and kb.is_fresh(f, self.now, mode=self.mode)

    def excluded(self, recent_days: int = 14) -> dict[str, str]:
        return query.excluded_ids(self.con, now=self.now, recent_days=recent_days)

    def recent_families(self, days: int = 14) -> set[str]:
        return query.recent_families(self.con, now=self.now, days=days)

    def regulars(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.con.execute("SELECT place_id, name FROM kb_regulars")}

    def household_verdicts(self) -> list[dict]:
        return [dict(zip(("kind", "place_id", "class_target", "text", "at"), r)) for r in self.con.execute(
            "SELECT kind, place_id, class_target, text, at FROM kb_verdict ORDER BY at DESC LIMIT 60")]

    def minutes(self, origin: tuple[float, float] | None, place_id: str, mode: str) -> int | None:
        pe = self.place(place_id)
        if not origin or not pe.latlng:
            return None
        return geo.about_minutes(origin[0], origin[1], pe.latlng[0], pe.latlng[1], mode=mode)

    def for_judge(self, place_ids: list[str]) -> list[dict]:
        return [self.place(pid).as_json() for pid in place_ids]

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:  # noqa: BLE001
            pass


# ── time helpers ────────────────────────────────────────────────────────
def _hm(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    m = re.match(r"^\s*~?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)?\s*$", s.strip())
    if not m:
        return None
    h, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mm > 59:
        return None
    return h, mm


def day_for(time_spec: Any, now: datetime) -> datetime:
    """The calendar day the ask is about (midnight of it)."""
    d = getattr(time_spec, "day", None)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not d or d == "today":
        return base
    if d == "tomorrow":
        return base + timedelta(days=1)
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d")
    except ValueError:
        return base


def arrival_for(frame: Any, now: datetime, *, about_minutes: int | None = None,
                arrival_hint: str | None = None) -> tuple[datetime, str]:
    """When the hours must hold, and why: ("at" | "arrive" | "depart+travel"
    | "open_by" | "meal default" | "now+travel")."""
    t = frame.time
    day = day_for(t, now)
    if t.kind == "at" and t.at:
        try:
            return datetime.fromisoformat(t.at[:16]), "at"
        except ValueError:
            pass
    hm = _hm(t.arrive) or _hm(arrival_hint)
    if hm:
        return day.replace(hour=hm[0], minute=hm[1]), "arrive"
    hm = _hm(t.depart)
    if hm:
        return day.replace(hour=hm[0], minute=hm[1]) + timedelta(minutes=about_minutes or DEFAULT_TRAVEL_MIN), "depart+travel"
    hm = _hm(t.open_by)
    if hm:
        return day.replace(hour=hm[0], minute=hm[1]), "open_by"
    meal = (frame.meal or "").lower()
    if t.kind in ("day", "window") or (t.day and t.day != "today"):
        minute = MEAL_DEFAULT_MINUTE.get(meal)
        if minute is not None:
            return day.replace(hour=minute // 60, minute=minute % 60), "meal default"
        if day.date() != now.date():
            return day.replace(hour=12, minute=30), "meal default"
    travel = about_minutes if about_minutes is not None else DEFAULT_TRAVEL_MIN
    return now + timedelta(minutes=travel), "now+travel"
