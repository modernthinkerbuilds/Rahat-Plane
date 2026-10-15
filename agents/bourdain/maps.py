"""agents.bourdain.maps — Google Maps places, as the knowledge base keeps them.

`maps_search` asks Gemini's Grounding with Google Maps for the places an
ask needs. Each grounding chunk is Google Maps' own record of one place:
the address, the Google rating and review count, the price range, the
weekly hours, the phone and website, a description and its attributes
(atmosphere, children, offerings, service options…). This module reads
those records — never the model's prose — into the knowledge base, so the
checks run on Google's data and every fact cites the place's Maps link.

Google's terms for Maps-grounded results (Gemini API Additional Terms):
they may be kept up to 90 days; each place is shown with "Google Maps"
and its link; no Maps data is exported. So every fact here is written
with source "Google Maps" and the place's Maps link, and `purge()` drops
what is older than 90 days, with places only Maps knew about.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
from typing import Callable

from core import hours as chours
from core.kb import schema as kb

MAPS_SOURCE = "Google Maps"
KEEP_DAYS = 90
SOURCE_LIST = "maps"

_ITEM = re.compile(r"^\*\s+\*\*(.+?):\*\*\s*(.*)$")
_SUB = re.compile(r"^\s+\*\s+(.*)$")
_TITLE = re.compile(r"^\*\*Title:\*\*\s*(.+)$")
_RATING = re.compile(r"^\s*([0-5](?:\.\d)?)\s*(?:\(\s*([\d,]+)\s+reviews?\s*\))?")
_PRICE_RANGE = re.compile(r"\$\s*(\d+)\s*(?:[-–]\s*\$?\s*(\d+)|\+)?")
_STATE_ZIP = re.compile(r"^[A-Z]{2}\s+\d{5}(?:-\d{4})?$")
_CATEGORY = re.compile(r"(?:(?<=^)|(?<=\.\s))([A-Z][A-Za-z /&+'-]{1,40}):\s")

# A room led by drinks (the household's no-bar rule): the description says so. A coffee, espresso, tea,
# juice, salad, sushi or dessert "bar" is not one.
_BAR = re.compile(r"\b(?<!coffee )(?<!espresso )(?<!tea )(?<!juice )(?<!salad )(?<!sushi )(?<!dessert )(?<!yogurt )"
                  r"(?<!snack )(?<!milk )(?<!boba )(?<!poke )(?<!raw )(?<!oyster )(?<!salad-)(bar|pub|brewery|brewpub|"
                  r"taproom|tap room|tavern|saloon|beer hall|biergarten|beer garden|wine bar|cocktail lounge|nightclub)\b", re.I)
_SEAFOOD = re.compile(r"\b(seafood|oysters?|fish house|fish market|crab|lobster|sushi|poke|raw bar|ceviche)\b", re.I)
_CAFE = re.compile(r"\b(coffee|caf[eé]|espresso|roaster|roastery|roasting)\b", re.I)
_OTHER_KINDS = (("dessert", re.compile(r"\b(ice cream|gelato|dessert|creamery|frozen yogurt|crepes?|waffles?|cannoli)\b", re.I)),
                ("bakery", re.compile(r"\b(bakery|bakeshop|pastry|patisserie|p[aâ]tisserie|boulangerie)\b", re.I)),
                ("tea", re.compile(r"\b(boba|bubble tea|milk tea|tea house|teahouse)\b", re.I)))
_ROOM_KEEP = ("atmosphere", "children", "crowd", "amenities", "dining options")
_CROWD_KEEP = re.compile(r"family|kids|children|groups", re.I)
# Food words that name a dish more than a cuisine: a café that serves breakfast is still a café.
_GENERIC_FOOD = {"breakfast", "diner", "pasta", "pizza", "ramen", "sushi", "taqueria"}


@dataclass
class MapsPlace:
    name: str
    url: str = ""
    google_place_id: str = ""
    address: str | None = None
    city: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_range: str | None = None
    price_level: str | None = None
    phone: str | None = None
    website: str | None = None
    description: str = ""
    summary: list[str] = field(default_factory=list)
    attributes: dict[str, list[str]] = field(default_factory=dict)
    menu: list[dict] = field(default_factory=list)          # {"name": str, "tags": [str]}
    hours_text: str | None = None
    status: str = "open"                                    # open | permanently closed | temporarily closed
    cuisine: str | None = None
    format_tags: list[str] = field(default_factory=list)
    room_tags: list[str] = field(default_factory=list)
    bar_led: bool = False
    seafood_led: bool = False


# ── reading one Maps record ─────────────────────────────────────────────
def _sections(text: str) -> tuple[str | None, dict[str, list[str]]]:
    title, cur = None, None
    out: dict[str, list[str]] = {}
    for raw in (text or "").splitlines():
        line = raw.rstrip().replace("​", "")
        m = _TITLE.match(line.strip())
        if m:
            title = m.group(1).strip()
            continue
        if line.startswith("* **"):
            m = _ITEM.match(line)
            if m:
                cur = m.group(1).strip().lower()
                out[cur] = [m.group(2).strip()] if m.group(2).strip() else []
                continue
        if cur is None or not line.strip():
            continue
        sm = _SUB.match(line)
        out[cur].append((sm.group(1) if sm else line).strip())
    return title, out


def price_level(price_range: str | None) -> str | None:
    """Google's per-person range as the household's $ scale."""
    if not price_range:
        return None
    s = price_range.strip()
    if re.fullmatch(r"\$+", s):
        return s[:4]
    m = _PRICE_RANGE.search(s)
    if not m:
        return None
    top = int(m.group(2) or m.group(1))
    if "+" in s and not m.group(2):
        top = max(top, 101)
    return "$" if top <= 10 else "$$" if top <= 30 else "$$$" if top <= 60 else "$$$$"


def _attributes(text: str) -> dict[str, list[str]]:
    t = re.sub(r"^\s*Information about the place:\s*", "", (text or "").strip())
    starts = list(_CATEGORY.finditer(t))
    out: dict[str, list[str]] = {}
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(t)
        val = t[m.end():end].strip().rstrip(".").strip()
        if val:
            out[m.group(1).strip().lower()] = [v.strip() for v in val.split(",") if v.strip()]
    return out


def _city(address: str | None) -> str | None:
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if parts and parts[-1].lower() in ("usa", "united states", "us"):
        parts = parts[:-1]
    if len(parts) >= 2 and _STATE_ZIP.match(parts[-1]):
        return parts[-2]
    return parts[-2] if len(parts) >= 3 else None


def city_key(city: str | None) -> str:
    return (city or "").strip().lower().replace(" ", "_")


def _cuisine(text: str) -> str | None:
    """A national cuisine named in the description wins (an Indian café is
    Indian — the household's exclusions read it), then a café, then a dish
    word, then a dessert, bakery or tea shop."""
    low = (text or "").lower()
    keys = sorted(kb.CUISINE_FAMILIES, key=len, reverse=True)
    hit = lambda pool: next((k for k in pool if re.search(r"\b" + re.escape(k) + r"\b", low)), None)  # noqa: E731
    national = hit([k for k in keys if k not in _GENERIC_FOOD])
    if national:
        return national
    if _CAFE.search(low):
        return "cafe"
    generic = hit([k for k in keys if k in _GENERIC_FOOD])
    if generic:
        return generic
    return next((kind for kind, rx in _OTHER_KINDS if rx.search(low)), None)


def _menu(lines: list[str]) -> list[dict]:
    out = []
    for ln in lines[:12]:
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", ln)
        name, tags = (m.group(1), [t.strip().lower() for t in m.group(2).split(",") if t.strip()]) if m else (ln, [])
        if name.strip():
            out.append({"name": name.strip()[:80], "tags": tags})
    return out


def _tags(mp: MapsPlace) -> tuple[list[str], list[str]]:
    """(format tags, room tags) the checks and the model read."""
    text = f"{mp.name} {mp.description}".lower()
    a = mp.attributes
    fmt: list[str] = []
    if _CAFE.search(text):
        fmt.append("cafe")
    if re.search(r"\broast(er|ery|ing)\b", text):
        fmt.append("roaster")
    if "board game" in text or "tabletop" in text:
        fmt += ["board_games"] + (["board_game_cafe"] if (_CAFE.search(text) or "cafe" in text) else [])
    svc = " ".join(a.get("service options", [])).lower()
    if "dine-in" in svc:
        fmt.append("dine_in")
    if "takeout" in svc:
        fmt.append("takeout")
    if "quick bite" in " ".join(a.get("offerings", [])).lower():
        fmt.append("quick")
    if "accepts reservations" in " ".join(a.get("planning", [])).lower():
        fmt.append("reservations")
    for kind, rx in _OTHER_KINDS:
        if rx.search(text):
            fmt.append(kind)
    room: list[str] = []
    for cat in _ROOM_KEEP:
        for v in a.get(cat, []):
            v = v.lower().strip()
            if cat == "amenities":
                if "wi-fi" not in v:
                    continue
                v = "wifi"
            if cat == "dining options" and v != "seating":
                continue
            if cat == "crowd" and not _CROWD_KEEP.search(v):
                continue
            room.append(v.replace(" ", "_"))
    return list(dict.fromkeys(fmt)), list(dict.fromkeys(room))


def parse_place(chunk: dict) -> MapsPlace | None:
    """One Google Maps grounding chunk ({url, title, text, place_id}) as a place."""
    text = chunk.get("text") or ""
    title, sec = _sections(text)
    name = (title or (chunk.get("title") or "").removesuffix(" - Google Maps")).strip()
    if not name:
        return None
    first = lambda k: (sec.get(k) or [None])[0]  # noqa: E731
    mp = MapsPlace(name=name, url=chunk.get("url") or "", google_place_id=chunk.get("place_id") or "")
    mp.address = first("address")
    mp.city = _city(mp.address)
    m = _RATING.match(first("rating") or "")
    if m:
        mp.rating = float(m.group(1))
        mp.rating_count = int(m.group(2).replace(",", "")) if m.group(2) else None
    mp.price_range = first("price range")
    mp.price_level = price_level(mp.price_range)
    mp.phone = first("phone")
    mp.website = first("website")
    mp.description = " ".join(sec.get("description") or [])[:600]
    mp.summary = [s[:200] for s in (sec.get("generated summary") or [])][:3]
    mp.attributes = _attributes(" ".join(sec.get("attributes") or []))
    mp.menu = _menu(sec.get("menu highlight") or sec.get("menu highlights") or [])
    hrs = [h for h in (sec.get("opening hours") or []) if ":" in h]
    mp.hours_text = "; ".join(hrs) or None
    low = text.lower()
    mp.status = ("permanently closed" if "permanently closed" in low
                 else "temporarily closed" if "temporarily closed" in low else "open")
    mp.cuisine = _cuisine(f"{mp.name}. {mp.description}")
    mp.format_tags, mp.room_tags = _tags(mp)
    desc = f"{mp.name}. {mp.description}"
    mp.bar_led = bool(_BAR.search(desc))
    mp.seafood_led = bool(_SEAFOOD.search(desc))
    return mp


def clean(mp: MapsPlace, fn: Callable) -> MapsPlace:
    """Every string through `fn` (the prompt-injection strike): Maps text is data."""
    return replace(mp, **{f.name: fn(getattr(mp, f.name)) for f in fields(mp)
                          if isinstance(getattr(mp, f.name), (str, list, dict))})


# ── into the knowledge base ─────────────────────────────────────────────
def _street(address: str | None) -> str:
    from core.geocode import street_address
    return kb.norm_name(street_address(address or "").split(",")[0])


def find_existing(con, name: str, city: str, address: str | None) -> str | None:
    """The place already on record as this one: the same name in the same
    city, or one name a branch label of the other ("Lantern Coffee" /
    "Lantern Coffee - Old Town") at the same street address. Two
    sources must not split one café's facts across two ids (replayed
    2026-10-04: a search snippet's copy of a Maps café carried the pick,
    without its Maps hours, rating or coordinates)."""
    norm = kb.norm_name(name)
    if not norm:
        return None
    street = _street(address)
    rows = con.execute("SELECT p.place_id, p.name_norm, l.address FROM kb_place p "
                       "LEFT JOIN kb_location l ON l.location_id = p.location_id "
                       "WHERE (l.city = ? OR l.city IS NULL OR l.city = '') AND (p.name_norm = ? OR p.name_norm LIKE ? "
                       "OR ? LIKE p.name_norm || '%')", (city, norm, norm + "%", norm)).fetchall()
    best = None
    for pid, pnorm, addr in rows:
        other = _street(addr)
        same_street = bool(street and other and (street in other or other in street))
        if pnorm == norm and (same_street or not street or not other):
            return pid
        if pnorm != norm and same_street and best is None:
            best = pid
    return best


def write_place(con, mp: MapsPlace, *, now: datetime, trace_id: str | None = None) -> dict:
    """Upsert the place and append its Maps facts. Returns {place_id, location_id, facts: {claim: fact_id}}."""
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    city = city_key(mp.city)
    pid = find_existing(con, mp.name, city, mp.address)
    if pid:
        loc = con.execute("SELECT location_id FROM kb_place WHERE place_id = ?", (pid,)).fetchone()[0]
        con.execute("UPDATE kb_location SET address = COALESCE(address, ?) WHERE location_id = ?", (mp.address, loc))
        row = con.execute("SELECT format_tags, room_tags FROM kb_place WHERE place_id = ?", (pid,)).fetchone()
        fmt = list(dict.fromkeys(json.loads(row[0] or "[]") + mp.format_tags))
        room = list(dict.fromkeys(json.loads(row[1] or "[]") + mp.room_tags))
        con.execute("UPDATE kb_place SET cuisine = COALESCE(cuisine, ?), cuisine_family = COALESCE(cuisine_family, ?), "
                    "price_level = COALESCE(?, price_level), format_tags = ?, room_tags = ?, updated_at = ? WHERE place_id = ?",
                    (mp.cuisine, kb.CUISINE_FAMILIES.get(mp.cuisine or ""), mp.price_level, json.dumps(fmt),
                     json.dumps(room), now_s, pid))
    else:
        loc = kb.location_id_for(city, mp.address, None, None, name_hint=mp.name)
        con.execute("INSERT OR IGNORE INTO kb_location (location_id, address, city, created_at) VALUES (?,?,?,?)",
                    (loc, mp.address, city, now_s))
        pid = kb.place_id_for(mp.name, city, address=mp.address)
        con.execute(
            "INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, cuisine_family, price_level, status, "
            "format_tags, room_tags, source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(place_id) DO UPDATE SET updated_at = excluded.updated_at",
            (pid, mp.name, kb.norm_name(mp.name), loc, mp.cuisine, kb.CUISINE_FAMILIES.get(mp.cuisine or ""),
             mp.price_level, "open" if mp.status == "open" else "closed", json.dumps(mp.format_tags),
             json.dumps(mp.room_tags), SOURCE_LIST, now_s, now_s))
    facts: dict[str, int] = {}

    def f(claim: str, value) -> int:
        fid = kb.add_fact(con, subject_type="place", subject_id=pid, claim_type=claim, value=value,
                          source_url=mp.url or None, source_name=MAPS_SOURCE, source_rank="aggregator",
                          method="api", observed_at=now_s, trace_id=trace_id, now=now)
        facts[claim] = fid
        return fid

    if mp.rating is not None:
        fid = f("rating", {"value": mp.rating, "source": MAPS_SOURCE, "count": mp.rating_count})
        if mp.rating_count:
            con.execute("INSERT OR REPLACE INTO kb_rating (fact_id, place_id, source, value, count, observed_at) "
                        "VALUES (?,?,?,?,?,?)", (fid, pid, MAPS_SOURCE, mp.rating, mp.rating_count, now_s))
    if mp.hours_text:
        fid = f("hours", {"value": mp.hours_text})
        for iv in chours.parse(mp.hours_text).intervals:
            con.execute("INSERT INTO kb_hours (fact_id, place_id, weekday, open_min, close_min, service, note) "
                        "VALUES (?,?,?,?,?,?,?)", (fid, pid, iv.weekday, iv.open_min, iv.close_min, iv.service, iv.text))
    fid = f("status", mp.status)
    con.execute("UPDATE kb_place SET status = ?, status_fact_id = ? WHERE place_id = ?",
                ("open" if mp.status == "open" else "closed", fid, pid))
    if mp.price_level:
        f("price", {"value": mp.price_level, "range": mp.price_range})
    if mp.phone:
        f("phone", {"value": mp.phone})
    if mp.website:
        f("website", {"value": mp.website})          # 2026-10-15: the nightly own-site refresh reads it
    if mp.url:
        f("map_url", {"value": mp.url})
    if mp.menu:
        fid = f("menu", {"value": [m["name"] for m in mp.menu]})
        for item in mp.menu:
            gf = "yes" if any(t in ("gluten-free", "gluten free", "gf") for t in item["tags"]) else "unknown"
            con.execute("INSERT INTO kb_menu_item (place_id, fact_id, name, diet_tags, gf) VALUES (?,?,?,?,?)",
                        (pid, fid, item["name"], json.dumps(item["tags"]), gf))
    room_text = "; ".join(f"{k}: {', '.join(v)}" for k, v in mp.attributes.items() if k in _ROOM_KEEP)
    f("room", {"bar_led": mp.bar_led, "seafood_led": mp.seafood_led, "value": room_text[:300]})
    if mp.description:
        f("note", {"value": mp.description[:300]})
    return {"place_id": pid, "location_id": loc, "facts": facts}


def purge(con, *, now: datetime, days: int = KEEP_DAYS) -> int:
    """Delete Maps facts older than `days` (Google's limit is 90), their
    projections, and the places only Google Maps knew about that carry
    nothing of the household's. Returns the number of facts deleted."""
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    ids = [r[0] for r in con.execute("SELECT fact_id FROM kb_fact WHERE source_name = ? AND observed_at < ?",
                                     (MAPS_SOURCE, cutoff))]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        for table in ("kb_rating", "kb_hours", "kb_menu_item"):
            con.execute(f"DELETE FROM {table} WHERE fact_id IN ({q})", chunk)
        con.execute(f"UPDATE kb_place SET status_fact_id = NULL WHERE status_fact_id IN ({q})", chunk)
        con.execute(f"DELETE FROM kb_fact WHERE fact_id IN ({q})", chunk)
    orphans = [r[0] for r in con.execute(
        "SELECT p.place_id FROM kb_place p WHERE p.source_list = ? "
        "AND NOT EXISTS (SELECT 1 FROM kb_fact f WHERE f.subject_type = 'place' AND f.subject_id = p.place_id "
        "                AND f.claim_type != 'geo') "
        "AND NOT EXISTS (SELECT 1 FROM kb_save s WHERE s.place_id = p.place_id) "
        "AND NOT EXISTS (SELECT 1 FROM kb_verdict v WHERE v.place_id = p.place_id)", (SOURCE_LIST,))]
    for pid in orphans:
        loc = con.execute("SELECT location_id FROM kb_place WHERE place_id = ?", (pid,)).fetchone()
        con.execute("DELETE FROM kb_fact WHERE subject_type = 'place' AND subject_id = ?", (pid,))
        con.execute("DELETE FROM kb_place WHERE place_id = ?", (pid,))
        if loc and not con.execute("SELECT 1 FROM kb_place WHERE location_id = ? LIMIT 1", (loc[0],)).fetchone():
            con.execute("DELETE FROM kb_location WHERE location_id = ?", (loc[0],))
    return len(ids)
