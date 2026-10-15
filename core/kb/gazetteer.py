"""core.kb.gazetteer — every eating place that exists in an area, from
open data, so a name resolves and a thin knowledge base has somewhere to
look before the web.

Two sources, both free and read in code (owner decision 2026-10-15):

  * OpenStreetMap, through the Overpass API: no sign-up; thinner on US
    restaurants, but it carries cuisine, website, phone and sometimes
    opening_hours and diet:* tags.
  * Foursquare OS Places (Apache 2.0, attribution kept in NOTICE): 100M+
    places with name, address, coordinates, category labels, website and
    phone; no hours, no ratings. Monthly releases; gated behind a sign-up.

The gazetteer is not the knowledge base. Nobody has vouched for a row,
it carries no hours or rating, and `promote()` copies a row into
kb_place (with its address, coordinates, website and phone as facts,
status unknown) only when an ask or a mention touches it. Google Maps
data never lands here: Google's terms keep it to 90 days.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Iterable

from core.kb import geo, schema as kb

SOURCES = ("osm", "foursquare")
SOURCE_NAMES = {"osm": "OpenStreetMap", "foursquare": "Foursquare OS Places"}
SOURCE_URLS = {"osm": "https://www.openstreetmap.org/", "foursquare": "https://opensource.foursquare.com/os-places/"}
SOURCE_LIST = "gazetteer"            # kb_place.source_list for a promoted row

# OSM: what counts as an eating place, and which tags are worth keeping.
OSM_AMENITIES = ("restaurant", "cafe", "fast_food", "bar", "pub", "ice_cream", "food_court", "biergarten")
OSM_SHOPS = ("bakery", "pastry", "deli", "coffee", "tea", "confectionery", "chocolate")
OSM_KEEP_TAGS = ("brand", "brand:wikidata", "takeaway", "delivery", "outdoor_seating", "reservation",
                 "diet:vegetarian", "diet:vegan", "diet:gluten_free", "diet:halal", "diet:kosher",
                 "wheelchair", "level", "microbrewery", "bar", "drive_through")

# Foursquare category labels → a cuisine word the family table knows. The label is the
# last segment ("Dining and Drinking > Restaurant > Thai Restaurant" → "thai").
_FSQ_CUISINE_RE = re.compile(r"^(.*?)\s+(restaurant|cuisine|food|place|shop|cafe|bakery|bar|joint|spot|steakhouse|"
                             r"pizzeria|taqueria|deli|diner)$", re.I)
_FSQ_FOOD_ROOTS = ("dining and drinking",)

CITY_KEYS = {"san jose": "san_jose", "san josé": "san_jose", "san francisco": "san_francisco",
             "mountain view": "mountain_view", "los altos": "los_altos", "los gatos": "los_gatos",
             "santa clara": "santa_clara", "san mateo": "san_mateo", "san carlos": "san_carlos",
             "redwood city": "redwood_city", "menlo park": "menlo_park", "palo alto": "palo_alto",
             "walnut creek": "walnut_creek", "half moon bay": "half_moon_bay", "daly city": "daly_city",
             "south san francisco": "south_san_francisco", "east palo alto": "east_palo_alto",
             "los altos hills": "los_altos_hills", "morgan hill": "morgan_hill", "santa cruz": "santa_cruz",
             "san rafael": "san_rafael", "mill valley": "mill_valley", "san bruno": "san_bruno",
             "foster city": "foster_city", "union city": "union_city", "san leandro": "san_leandro",
             "castro valley": "castro_valley", "el cerrito": "el_cerrito", "san pablo": "san_pablo",
             "pleasant hill": "pleasant_hill", "san ramon": "san_ramon"}


def city_key(city: str | None) -> str:
    c = (city or "").strip().lower()
    return CITY_KEYS.get(c) or c.replace(" ", "_")


def cuisine_family(cuisine: str | None) -> str | None:
    if not cuisine:
        return None
    for word in re.split(r"[;,/]", cuisine.lower()):
        word = word.strip().replace("_", " ")
        if word in kb.CUISINE_FAMILIES:
            return kb.CUISINE_FAMILIES[word]
    return None


# ── rows from the sources ────────────────────────────────────────────────
def osm_row(element: dict) -> dict | None:
    """One Overpass element (node, or way/relation with `center`) → a gazetteer row, or None."""
    tags = element.get("tags") or {}
    name = (tags.get("name") or "").strip()
    if not name:
        return None
    amenity, shop = tags.get("amenity"), tags.get("shop")
    if amenity not in OSM_AMENITIES and shop not in OSM_SHOPS:
        return None
    lat = element.get("lat") or (element.get("center") or {}).get("lat")
    lng = element.get("lon") or (element.get("center") or {}).get("lon")
    cats = [c for c in (amenity, shop) if c]
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    city = tags.get("addr:city") or ""
    address = ", ".join(x for x in (street, city, tags.get("addr:postcode")) if x) or None
    cuisine = (tags.get("cuisine") or "").replace("_", " ") or None
    keep = {k: v for k, v in tags.items() if k in OSM_KEEP_TAGS}
    kind = element.get("type", "node")
    return {
        "gaz_id": f"osm:{kind}/{element.get('id')}", "source": "osm", "name": name,
        "lat": float(lat) if lat is not None else None, "lng": float(lng) if lng is not None else None,
        "address": address, "city": city or None, "postcode": tags.get("addr:postcode"),
        "categories": ";".join(cats), "cuisine": cuisine, "website": tags.get("website") or tags.get("contact:website"),
        "phone": tags.get("phone") or tags.get("contact:phone"), "hours_text": tags.get("opening_hours"),
        "tags": keep, "date_refreshed": None, "date_closed": None,
    }


def fsq_row(r: dict) -> dict | None:
    """One Foursquare OS Places record (the dataset's column names) → a row, or None when it is
    not an eating place or has no name."""
    name = (r.get("name") or "").strip()
    labels = r.get("fsq_category_labels") or []
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except ValueError:
            labels = [x.strip() for x in labels.split(",") if x.strip()]
    labels = [str(x) for x in labels]
    if not name or not any(x.lower().startswith(_FSQ_FOOD_ROOTS) for x in labels):
        return None
    leaves = [x.split(">")[-1].strip() for x in labels]
    cuisine = None
    for leaf in leaves:
        m = _FSQ_CUISINE_RE.match(leaf)
        word = (m.group(1) if m else leaf).strip().lower()
        if word in kb.CUISINE_FAMILIES:
            cuisine = word
            break
    street = r.get("address") or ""
    city = r.get("locality") or ""
    address = ", ".join(x for x in (street, city, r.get("region"), r.get("postcode")) if x) or None
    return {
        "gaz_id": f"fsq:{r.get('fsq_place_id')}", "source": "foursquare", "name": name,
        "lat": float(r["latitude"]) if r.get("latitude") is not None else None,
        "lng": float(r["longitude"]) if r.get("longitude") is not None else None,
        "address": address, "city": city or None, "postcode": r.get("postcode"),
        "categories": ";".join(leaves), "cuisine": cuisine, "website": r.get("website"), "phone": r.get("tel"),
        "hours_text": None, "tags": {k: r[k] for k in ("instagram", "facebook_id") if r.get(k)},
        "date_refreshed": str(r.get("date_refreshed") or "") or None,
        "date_closed": str(r.get("date_closed") or "") or None,
    }


# ── the table ───────────────────────────────────────────────────────────
def upsert(con: sqlite3.Connection, rows: Iterable[dict], *, release: str | None = None,
           now: datetime | None = None) -> int:
    now_s = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for r in rows:
        if not r or not r.get("name"):
            continue
        con.execute(
            "INSERT INTO kb_gazetteer (gaz_id, source, name, name_norm, lat, lng, address, city, city_key, postcode, "
            "categories, cuisine, cuisine_family, website, phone, hours_text, tags_json, date_refreshed, date_closed, "
            "release, loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(gaz_id) DO UPDATE SET name = excluded.name, name_norm = excluded.name_norm, lat = excluded.lat, "
            "lng = excluded.lng, address = excluded.address, city = excluded.city, city_key = excluded.city_key, "
            "postcode = excluded.postcode, categories = excluded.categories, cuisine = excluded.cuisine, "
            "cuisine_family = excluded.cuisine_family, website = excluded.website, phone = excluded.phone, "
            "hours_text = excluded.hours_text, tags_json = excluded.tags_json, date_refreshed = excluded.date_refreshed, "
            "date_closed = excluded.date_closed, release = excluded.release, loaded_at = excluded.loaded_at",
            (r["gaz_id"], r["source"], r["name"], kb.norm_name(r["name"]), r.get("lat"), r.get("lng"), r.get("address"),
             r.get("city"), city_key(r.get("city")), r.get("postcode"), r.get("categories") or "", r.get("cuisine"),
             cuisine_family(r.get("cuisine")), r.get("website"), r.get("phone"), r.get("hours_text"),
             json.dumps(r.get("tags") or {}, ensure_ascii=False), r.get("date_refreshed"), r.get("date_closed"),
             release, now_s))
        n += 1
    return n


_COLS = ("gaz_id", "source", "name", "name_norm", "lat", "lng", "address", "city", "city_key", "postcode", "categories",
         "cuisine", "cuisine_family", "website", "phone", "hours_text", "tags_json", "date_refreshed", "date_closed",
         "place_id", "release", "loaded_at")
_SELECT = "SELECT " + ", ".join(_COLS) + " FROM kb_gazetteer "


def _row(r: tuple) -> dict:
    d = dict(zip(_COLS, r))
    d["tags"] = json.loads(d.pop("tags_json") or "{}")
    return d


def find(con: sqlite3.Connection, text: str, *, city: str | None = None, near: tuple[float, float] | None = None,
         km: float | None = None, limit: int = 5) -> list[dict]:
    """Rows whose name matches `text` (exact normalized name first, then a
    substring), open ones only, nearest first when `near` is given and
    within `km` of it when both are."""
    n = kb.norm_name(text)
    if not n:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    city_sql, args0 = (" AND city_key = ?", [city_key(city)]) if city else ("", [])
    for sql, args in ((_SELECT + "WHERE name_norm = ? AND date_closed IS NULL" + city_sql, [n] + args0),
                      (_SELECT + "WHERE name_norm LIKE ? AND date_closed IS NULL" + city_sql + " LIMIT 200", [f"%{n}%"] + args0)):
        for r in con.execute(sql, args).fetchall():
            d = _row(r)
            if d["gaz_id"] in seen:
                continue
            if near and d["lat"] is not None:
                d["km"] = round(geo.haversine_km(near[0], near[1], d["lat"], d["lng"]), 1)
                if km is not None and d["km"] > km:
                    continue
            seen.add(d["gaz_id"])
            out.append(d)
    out.sort(key=lambda d: (d.get("km", 1e9), d["name"]))
    return out[:limit]


def nearby(con: sqlite3.Connection, lat: float, lng: float, *, km: float, family: str | None = None,
           categories: Iterable[str] | None = None, limit: int = 40) -> list[dict]:
    """Open rows within `km` of a point, nearest first; by cuisine family
    and/or categories when given."""
    dlat = km / 111.0
    dlng = km / max(1e-6, 111.0 * abs(__import__("math").cos(__import__("math").radians(lat))))
    rows = con.execute(_SELECT + "WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND date_closed IS NULL",
                       (lat - dlat, lat + dlat, lng - dlng, lng + dlng)).fetchall()
    cats = {c.lower() for c in (categories or [])}
    out: list[dict] = []
    for r in rows:
        d = _row(r)
        if family and (d.get("cuisine_family") or "") != family:
            continue
        if cats and not (cats & {c.strip().lower() for c in (d.get("categories") or "").split(";")}):
            continue
        d["km"] = round(geo.haversine_km(lat, lng, d["lat"], d["lng"]), 1)
        if d["km"] <= km:
            out.append(d)
    # A row the feeds have written about comes first (bridges/food): someone vouched for it, a little.
    mentioned: dict[str, int] = {}
    if out:
        try:
            ids = [d["gaz_id"] for d in out]
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                q = ",".join("?" * len(chunk))
                mentioned.update(dict(con.execute(
                    f"SELECT gaz_id, COUNT(*) FROM kb_mention WHERE gaz_id IN ({q}) GROUP BY gaz_id", chunk).fetchall()))
        except sqlite3.Error:
            mentioned = {}
    for d in out:
        d["mentions"] = mentioned.get(d["gaz_id"], 0)
    out.sort(key=lambda d: (-d["mentions"], d["km"], d["name"]))
    return out[:limit]


def promote(con: sqlite3.Connection, row: dict, *, now: datetime, trace_id: str | None = None) -> dict:
    """Copy a gazetteer row into kb_place (status unknown, source_list
    "gazetteer") with its coordinates, address, website and phone as facts
    from the open-data source. Idempotent: a row already promoted, or a
    place of that name at that spot, is returned as it is."""
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    if row.get("place_id"):
        existing = con.execute("SELECT place_id, location_id FROM kb_place WHERE place_id = ?", (row["place_id"],)).fetchone()
        if existing:
            return {"place_id": existing[0], "location_id": existing[1], "facts": {}, "promoted": False}
    city = city_key(row.get("city"))
    pid = kb.place_id_for(row["name"], city, address=row.get("address"), lat=row.get("lat"), lng=row.get("lng"))
    existing = con.execute("SELECT place_id, location_id FROM kb_place WHERE place_id = ?", (pid,)).fetchone()
    if not existing:
        # the same name within 150 m already on file (from Maps, the seed, a save) is that place
        if row.get("lat") is not None:
            for r in con.execute(
                    "SELECT p.place_id, p.location_id, l.lat, l.lng FROM kb_place p JOIN kb_location l USING(location_id) "
                    "WHERE p.name_norm = ? AND l.lat IS NOT NULL", (kb.norm_name(row["name"]),)).fetchall():
                if geo.haversine_km(row["lat"], row["lng"], r[2], r[3]) <= 0.15:
                    existing = (r[0], r[1])
                    break
    if existing:
        con.execute("UPDATE kb_gazetteer SET place_id = ? WHERE gaz_id = ?", (existing[0], row["gaz_id"]))
        return {"place_id": existing[0], "location_id": existing[1], "facts": {}, "promoted": False}
    loc = kb.location_id_for(city, row.get("address"), row.get("lat"), row.get("lng"), name_hint=row["name"])
    con.execute("INSERT OR IGNORE INTO kb_location (location_id, lat, lng, address, city, created_at) VALUES (?,?,?,?,?,?)",
                (loc, row.get("lat"), row.get("lng"), row.get("address"), city, now_s))
    fam = row.get("cuisine_family") or cuisine_family(row.get("cuisine"))
    con.execute(
        "INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, cuisine_family, status, format_tags, "
        "room_tags, source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, row["name"], kb.norm_name(row["name"]), loc, (row.get("cuisine") or "").split(";")[0] or None, fam,
         "unknown", json.dumps(_format_tags(row)), "[]", SOURCE_LIST, now_s, now_s))
    facts: dict[str, int] = {}
    src_name, src_url = SOURCE_NAMES[row["source"]], SOURCE_URLS[row["source"]]
    observed = (row.get("date_refreshed") or row.get("loaded_at") or now_s)[:19]

    def f(claim: str, value: Any) -> None:
        facts[claim] = kb.add_fact(con, subject_type="place", subject_id=pid, claim_type=claim, value=value,
                                   source_url=src_url, source_name=src_name, source_rank="aggregator", method="api",
                                   observed_at=observed, trace_id=trace_id, now=now)

    if row.get("lat") is not None:
        f("geo", {"lat": row["lat"], "lng": row["lng"], "source_name": src_name})
    if row.get("website"):
        f("website", {"value": row["website"]})
    if row.get("phone"):
        f("phone", {"value": row["phone"]})
    hours = osm_hours_to_text(row.get("hours_text")) if row.get("source") == "osm" else row.get("hours_text")
    if hours:
        f("hours", {"value": hours, "note": "as OpenStreetMap had it; unverified"})
        try:
            from core import hours as chours
            for iv in chours.parse(hours).intervals:
                con.execute("INSERT INTO kb_hours (fact_id, place_id, weekday, open_min, close_min, service, note) "
                            "VALUES (?,?,?,?,?,?,?)", (facts["hours"], pid, iv.weekday, iv.open_min, iv.close_min, iv.service, iv.text))
        except Exception:  # noqa: BLE001 - the text is kept; the projection is a courtesy
            pass
    f("note", {"value": f"from {src_name}; nobody has vouched for it yet"})
    con.execute("UPDATE kb_gazetteer SET place_id = ? WHERE gaz_id = ?", (pid, row["gaz_id"]))
    return {"place_id": pid, "location_id": loc, "facts": facts, "promoted": True}


def _format_tags(row: dict) -> list[str]:
    tags = row.get("tags") or {}
    cats = {c.strip().lower() for c in (row.get("categories") or "").split(";")}
    out = []
    if cats & {"fast_food", "food_court"} or tags.get("takeaway") == "only":
        out.append("quick")
    if cats & {"cafe", "coffee", "bakery", "pastry", "ice_cream", "tea"}:
        out.append("cafe")
    if cats & {"bar", "pub", "biergarten"}:
        out.append("bar")
    if tags.get("brand"):
        out.append("chain")
    return out


# ── OSM opening_hours → the text the hours parser reads ─────────────────
_OSM_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_OSM_RULE_RE = re.compile(r"^(?P<days>(?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?(?:,\s*)?)+)?\s*"
                          r"(?P<times>(?:\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}(?:,\s*)?)+|off|closed|24/7)\s*$", re.I)


def _ampm(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    if h >= 24:
        h -= 24
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def osm_hours_to_text(spec: str | None) -> str | None:
    """"Mo-Fr 11:00-21:00; Sa,Su 12:00-22:00" → "Monday: 11:00 AM–9:00 PM; …", the
    form core.hours reads. The common grammar only (day ranges and lists,
    time ranges, off, 24/7); anything else (months, public holidays,
    "sunrise") returns None rather than a wrong week."""
    if not spec or not spec.strip():
        return None
    week: dict[int, list[str]] = {}
    if spec.strip() == "24/7":
        return "; ".join(f"{d}: Open 24 hours" for d in _DAY_NAMES)
    for rule in spec.split(";"):
        rule = rule.strip()
        if not rule or rule.upper().startswith("PH"):
            continue
        m = _OSM_RULE_RE.match(rule)
        if not m:
            return None
        days: list[int] = []
        for part in (m.group("days") or "Mo-Su").replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-")
                ia, ib = _OSM_DAYS.index(a), _OSM_DAYS.index(b)
                days += list(range(ia, ib + 1)) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
            else:
                days.append(_OSM_DAYS.index(part))
        times = m.group("times").strip().lower()
        for d in days:
            if times in ("off", "closed"):
                week[d] = ["Closed"]
            elif times == "24/7":
                week[d] = ["Open 24 hours"]
            else:
                week[d] = [f"{_ampm(a.strip())}–{_ampm(b.strip())}" for a, b in
                           (t.split("-") for t in times.replace(" ", "").split(",") if t)]
    if not week:
        return None
    return "; ".join(f"{_DAY_NAMES[d]}: {', '.join(week[d])}" for d in sorted(week))


def stats(con: sqlite3.Connection) -> dict:
    out = {"rows": con.execute("SELECT COUNT(*) FROM kb_gazetteer").fetchone()[0],
           "by_source": dict(con.execute("SELECT source, COUNT(*) FROM kb_gazetteer GROUP BY 1").fetchall()),
           "promoted": con.execute("SELECT COUNT(*) FROM kb_gazetteer WHERE place_id IS NOT NULL").fetchone()[0],
           "with_website": con.execute("SELECT COUNT(*) FROM kb_gazetteer WHERE website IS NOT NULL").fetchone()[0],
           "with_hours": con.execute("SELECT COUNT(*) FROM kb_gazetteer WHERE hours_text IS NOT NULL").fetchone()[0],
           "top_cities": con.execute("SELECT city_key, COUNT(*) FROM kb_gazetteer GROUP BY 1 ORDER BY 2 DESC LIMIT 12").fetchall()}
    return out
