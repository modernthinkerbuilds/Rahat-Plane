"""core.kb.schema — the knowledge base tables (ADR-018 §2.6).

Every row that states something about the world is a `kb_fact`: claim
type, value, where it came from, when it was read, how long it is
trusted. `kb_hours` and `kb_rating` are typed projections of facts;
places, locations, saves, verdicts and visits are the entities those
facts hang on. Nothing is overwritten: a new observation is a new fact
row, and `current_fact()` returns the newest one per (subject, claim).

Identity: a place is one physical location. `place_id_for(name, city,
address=None, lat=None, lng=None)` hashes the normalized name with a
7-character geohash when coordinates exist, else with city + normalized
address, else with the city alone. Merges are recorded in `kb_merge`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from core import io as cio

SCHEMA_VERSION = 1

SUBJECT_TYPES = ("place", "brand", "landmark", "area", "leg", "weather", "route",
                 "household", "seed", "member")
METHODS = ("fetch", "search_citation", "seed", "user_report", "api", "owner")
SOURCE_RANKS = ("own_site", "ordering_page", "aggregator", "critic", "user_report",
                "seed", "search_citation", "unverified", "")

# Freshness defaults per claim type, in hours (platform PR-E4). The
# caller passes `mode="trip"` or `"home"` where the window differs.
FRESHNESS_HOURS: dict[str, dict[str, float]] = {
    "status":  {"trip": 24, "home": 24 * 7},
    "hours":   {"trip": 24, "home": 24},
    "rating":  {"trip": 24 * 7, "home": 24 * 7},
    "price":   {"trip": 24 * 30, "home": 24 * 30},
    "menu":    {"trip": 24 * 30, "home": 24 * 30},
    "weather": {"trip": 12, "home": 12},
    "route_minutes": {"trip": 1, "home": 1},
    "acclaim": {"trip": float("inf"), "home": float("inf")},
}


def freshness_hours(claim_type: str, mode: str = "home") -> float:
    base = claim_type.split(":")[0]
    return FRESHNESS_HOURS.get(base, {}).get(mode, 24 * 30)


_DDL = f"""
CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS kb_location (
    location_id TEXT PRIMARY KEY,
    lat REAL, lng REAL,
    address TEXT, neighborhood TEXT, city TEXT, region TEXT, country TEXT DEFAULT 'US',
    grid_x REAL, grid_st REAL,                 -- the Manhattan grid, when present
    geocode_fact_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_brand (
    brand_id TEXT PRIMARY KEY,
    name TEXT NOT NULL, name_norm TEXT NOT NULL,
    location_count INTEGER, location_count_fact_id INTEGER
);

CREATE TABLE IF NOT EXISTS kb_place (
    place_id TEXT PRIMARY KEY,
    name TEXT NOT NULL, name_norm TEXT NOT NULL,
    brand_id TEXT, location_id TEXT,
    cuisine TEXT, cuisine_family TEXT,
    price_level TEXT,
    status TEXT NOT NULL DEFAULT 'open', status_fact_id INTEGER,
    founded INTEGER,
    location_count INTEGER, location_count_fact_id INTEGER,
    format_tags TEXT NOT NULL DEFAULT '[]',     -- JSON list
    room_tags TEXT NOT NULL DEFAULT '[]',       -- JSON list
    source_list TEXT NOT NULL DEFAULT '',       -- yours | mine | live | prep | ''
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_place_name ON kb_place(name_norm);
CREATE INDEX IF NOT EXISTS kb_place_loc ON kb_place(location_id);

CREATE TABLE IF NOT EXISTS kb_landmark (
    landmark_id TEXT PRIMARY KEY,
    name TEXT NOT NULL, name_norm TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'landmark',      -- museum | station | garage | campus | park | stay | landmark
    location_id TEXT, note TEXT, source_fact_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_alias (
    alias_norm TEXT NOT NULL,
    target_type TEXT NOT NULL,                  -- place | landmark | area
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'alias',         -- alias | dictation | ambiguous
    readings TEXT, rule TEXT,                   -- for ambiguous aliases: JSON list, and the rule
    created_at TEXT NOT NULL,
    PRIMARY KEY (alias_norm, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS kb_fact (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    value_json TEXT, value_text TEXT,
    source_url TEXT, source_name TEXT, source_rank TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL,
    observed_at TEXT NOT NULL, applies_to TEXT,
    freshness_hours REAL,
    trace_id TEXT, superseded_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_fact_subject ON kb_fact(subject_type, subject_id, claim_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS kb_hours (
    fact_id INTEGER PRIMARY KEY,
    place_id TEXT NOT NULL,
    weekday INTEGER NOT NULL,                   -- 0 = Monday
    open_min INTEGER, close_min INTEGER,
    last_seating_min INTEGER, kitchen_close_min INTEGER,
    service TEXT NOT NULL DEFAULT 'all', note TEXT
);
CREATE INDEX IF NOT EXISTS kb_hours_place ON kb_hours(place_id, weekday);

CREATE TABLE IF NOT EXISTS kb_rating (
    fact_id INTEGER PRIMARY KEY,
    place_id TEXT NOT NULL,
    source TEXT NOT NULL, value REAL, count INTEGER,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_rating_place ON kb_rating(place_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS kb_menu_item (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT NOT NULL, fact_id INTEGER,
    name TEXT NOT NULL, price_cents INTEGER, price_text TEXT,
    diet_tags TEXT NOT NULL DEFAULT '[]', gf TEXT NOT NULL DEFAULT 'unknown',   -- yes | on_request | no | unknown
    day_availability TEXT, signature INTEGER NOT NULL DEFAULT 0, note TEXT
);
CREATE INDEX IF NOT EXISTS kb_menu_place ON kb_menu_item(place_id);

CREATE TABLE IF NOT EXISTS kb_list (
    list_id TEXT PRIMARY KEY,
    name TEXT NOT NULL, publisher TEXT, author TEXT, date TEXT, url TEXT, city TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS kb_acclaim (
    acclaim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT NOT NULL, list_id TEXT, writer TEXT, rank INTEGER, date TEXT,
    url TEXT, quote TEXT, fact_id INTEGER
);
CREATE INDEX IF NOT EXISTS kb_acclaim_place ON kb_acclaim(place_id);

CREATE TABLE IF NOT EXISTS kb_save (
    save_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT NOT NULL, member_id TEXT NOT NULL,
    saved_at TEXT NOT NULL, heard_from TEXT, occasion TEXT, for_date TEXT, note TEXT,
    trace_id TEXT
);
CREATE INDEX IF NOT EXISTS kb_save_place ON kb_save(place_id);

CREATE TABLE IF NOT EXISTS kb_verdict (
    verdict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT, class_target TEXT,
    member_id TEXT NOT NULL,
    kind TEXT NOT NULL,       -- chain | reported_score | price | room | delight | style | loved | chosen | rejected | closed | status
    value_json TEXT, text TEXT,
    at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'standing', trace_id TEXT
);
CREATE INDEX IF NOT EXISTS kb_verdict_place ON kb_verdict(place_id, at DESC);

CREATE TABLE IF NOT EXISTS kb_visit (
    visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT NOT NULL, at TEXT NOT NULL,
    members TEXT NOT NULL DEFAULT '[]', dishes TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'reported',   -- chosen | reported | planned
    trace_id TEXT
);

CREATE TABLE IF NOT EXISTS kb_search (
    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT, agent TEXT NOT NULL,
    frame_json TEXT, candidate_ids TEXT NOT NULL DEFAULT '[]', fact_ids TEXT NOT NULL DEFAULT '[]',
    queries_run INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0.0,
    kb_hits INTEGER NOT NULL DEFAULT 0, live_fetches INTEGER NOT NULL DEFAULT 0,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_leg (
    leg_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                         -- trip | home
    label TEXT NOT NULL,
    stay_name TEXT, stay_location_id TEXT,
    start_date TEXT, end_date TEXT, tz TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    minutes_mode TEXT NOT NULL DEFAULT 'drive', radius_min INTEGER,
    schedule_json TEXT NOT NULL DEFAULT '{{}}', party_default TEXT, note TEXT
);

CREATE TABLE IF NOT EXISTS kb_cuisine_family (cuisine TEXT PRIMARY KEY, family TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS kb_merge (
    from_id TEXT NOT NULL, into_id TEXT NOT NULL, at TEXT NOT NULL, reason TEXT,
    PRIMARY KEY (from_id, into_id)
);

CREATE VIEW IF NOT EXISTS kb_place_current AS
    SELECT f.* FROM kb_fact f
    WHERE f.subject_type = 'place' AND f.superseded_by IS NULL
      AND f.fact_id = (SELECT g.fact_id FROM kb_fact g
                       WHERE g.subject_type = f.subject_type AND g.subject_id = f.subject_id
                         AND g.claim_type = f.claim_type AND g.superseded_by IS NULL
                       ORDER BY g.observed_at DESC, g.fact_id DESC LIMIT 1);

CREATE VIEW IF NOT EXISTS kb_recent_meals AS
    SELECT v.visit_id, v.place_id, v.at, v.members, p.cuisine,
           COALESCE(cf.family, p.cuisine_family, p.cuisine) AS family
    FROM kb_visit v JOIN kb_place p ON p.place_id = v.place_id
    LEFT JOIN kb_cuisine_family cf ON cf.cuisine = p.cuisine;

CREATE VIEW IF NOT EXISTS kb_regulars AS
    SELECT p.place_id, p.name, MAX(vd.at) AS last_verdict,
           (SELECT COUNT(*) FROM kb_visit vi WHERE vi.place_id = p.place_id) AS visits
    FROM kb_place p JOIN kb_verdict vd ON vd.place_id = p.place_id AND vd.kind = 'loved'
    GROUP BY p.place_id, p.name;
"""

# Seed ontology: cuisines that count together for the variety rule.
CUISINE_FAMILIES = {
    "ethiopian": "ethiopian", "eritrean": "ethiopian",
    "thai": "thai", "lao": "thai",
    "burmese": "burmese",
    "korean": "korean",
    "mexican": "mexican", "oaxacan": "mexican", "taqueria": "mexican",
    "italian": "italian", "pizza": "italian", "pasta": "italian",
    "indian": "indian", "south indian": "indian", "north indian": "indian",
    "japanese": "japanese", "ramen": "japanese", "sushi": "japanese",
    "chinese": "chinese", "sichuan": "chinese", "cantonese": "chinese", "taiwanese": "chinese",
    "vietnamese": "vietnamese",
    "mediterranean": "mediterranean", "greek": "mediterranean", "israeli": "mediterranean",
    "middle eastern": "mediterranean", "lebanese": "mediterranean", "turkish": "mediterranean",
    "american": "american", "diner": "american", "breakfast": "american",
    "afghan": "afghan", "persian": "persian", "venezuelan": "latin", "peruvian": "latin",
    "spanish": "spanish", "french": "french",
}


def norm_name(s: str) -> str:
    """Accent-stripped, lower-cased, punctuation and articles removed."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in ("the", "a", "an", "of")]
    return " ".join(words)


_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lng: float, precision: int = 7) -> str:
    """Plain geohash (no dependency); 7 characters is about 150 m."""
    lat_r, lng_r = [-90.0, 90.0], [-180.0, 180.0]
    bits, bit, ch, even, out = [16, 8, 4, 2, 1], 0, 0, True, []
    while len(out) < precision:
        if even:
            mid = (lng_r[0] + lng_r[1]) / 2
            if lng > mid:
                ch |= bits[bit]; lng_r[0] = mid
            else:
                lng_r[1] = mid
        else:
            mid = (lat_r[0] + lat_r[1]) / 2
            if lat > mid:
                ch |= bits[bit]; lat_r[0] = mid
            else:
                lat_r[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_B32[ch]); bit, ch = 0, 0
    return "".join(out)


def place_id_for(name: str, city: str = "", *, address: str | None = None,
                 lat: float | None = None, lng: float | None = None) -> str:
    n = norm_name(name)
    if lat is not None and lng is not None:
        key = f"{n}|{geohash(lat, lng, 7)}"
    elif address:
        key = f"{n}|{(city or '').lower().strip()}|{norm_name(address)}"
    else:
        key = f"{n}|{(city or '').lower().strip()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def location_id_for(city: str, address: str | None, lat: float | None, lng: float | None,
                    name_hint: str = "") -> str:
    if lat is not None and lng is not None:
        key = f"geo|{geohash(lat, lng, 8)}"
    elif address:
        key = f"addr|{(city or '').lower().strip()}|{norm_name(address)}"
    else:
        key = f"name|{(city or '').lower().strip()}|{norm_name(name_hint)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    con = cio.db(db_path)
    ensure_schema(con)
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_DDL)
    con.execute("INSERT OR IGNORE INTO kb_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
    con.executemany("INSERT OR IGNORE INTO kb_cuisine_family (cuisine, family) VALUES (?, ?)",
                    list(CUISINE_FAMILIES.items()))
    con.commit()


def _now(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def add_fact(con: sqlite3.Connection, *, subject_type: str, subject_id: str,
             claim_type: str, value: Any, source_url: str | None = None,
             source_name: str | None = None, source_rank: str = "",
             method: str, observed_at: str, applies_to: str | None = None,
             freshness_hours_: float | None = None, trace_id: str | None = None,
             now: datetime | None = None) -> int:
    """Append one evidence record. Never updates an existing row."""
    if subject_type not in SUBJECT_TYPES:
        raise ValueError(f"unknown subject_type {subject_type!r}")
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if isinstance(value, (dict, list)):
        value_json, value_text = json.dumps(value, ensure_ascii=False), None
        if isinstance(value, dict) and "value" in value:
            value_text = str(value["value"])
    else:
        value_json, value_text = None, (None if value is None else str(value))
    # Freshness is a read-time policy (it differs by trip vs home), so a
    # fact stores a window only when the writer set one explicitly.
    fh = freshness_hours_
    if fh == float("inf"):
        fh = None
    cur = con.execute(
        "INSERT INTO kb_fact (subject_type, subject_id, claim_type, value_json, value_text, "
        "source_url, source_name, source_rank, method, observed_at, applies_to, "
        "freshness_hours, trace_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (subject_type, subject_id, claim_type, value_json, value_text, source_url,
         source_name, source_rank, method, observed_at, applies_to, fh, trace_id, _now(now)))
    return int(cur.lastrowid)


def current_fact(con: sqlite3.Connection, subject_type: str, subject_id: str,
                 claim_type: str) -> dict | None:
    """The newest non-superseded fact for (subject, claim), or None."""
    row = con.execute(
        "SELECT fact_id, claim_type, value_json, value_text, source_url, source_name, "
        "source_rank, method, observed_at, applies_to, freshness_hours FROM kb_fact "
        "WHERE subject_type = ? AND subject_id = ? AND claim_type = ? AND superseded_by IS NULL "
        "ORDER BY observed_at DESC, fact_id DESC LIMIT 1",
        (subject_type, subject_id, claim_type)).fetchone()
    if not row:
        return None
    keys = ["fact_id", "claim_type", "value_json", "value_text", "source_url", "source_name",
            "source_rank", "method", "observed_at", "applies_to", "freshness_hours"]
    d = dict(zip(keys, row))
    d["value"] = json.loads(d["value_json"]) if d["value_json"] else d["value_text"]
    return d


def is_fresh(fact: dict, now: datetime, mode: str = "home") -> bool:
    """True when the fact is inside its freshness window at `now`."""
    hours = fact.get("freshness_hours")
    if hours is None:
        hours = freshness_hours(fact["claim_type"], mode)
    if hours == float("inf"):
        return True
    try:
        observed = datetime.fromisoformat(str(fact["observed_at"])[:19])
    except ValueError:
        return False
    return (now - observed).total_seconds() <= hours * 3600


def stats(con: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """Counts for `/kb stats`: places by city and list, facts, stale facts,
    saves, verdicts, visits, landmarks, legs."""
    now = now or datetime.now()
    out: dict[str, Any] = {}
    out["places"] = con.execute("SELECT COUNT(*) FROM kb_place").fetchone()[0]
    out["places_by_city"] = dict(con.execute(
        "SELECT COALESCE(l.city, '?'), COUNT(*) FROM kb_place p "
        "LEFT JOIN kb_location l ON l.location_id = p.location_id GROUP BY 1").fetchall())
    out["places_by_list"] = dict(con.execute(
        "SELECT source_list, COUNT(*) FROM kb_place GROUP BY 1").fetchall())
    out["places_by_status"] = dict(con.execute(
        "SELECT status, COUNT(*) FROM kb_place GROUP BY 1").fetchall())
    out["facts"] = con.execute("SELECT COUNT(*) FROM kb_fact").fetchone()[0]
    out["facts_by_method"] = dict(con.execute(
        "SELECT method, COUNT(*) FROM kb_fact GROUP BY 1").fetchall())
    stale = 0
    for row in con.execute("SELECT claim_type, observed_at, freshness_hours FROM kb_place_current"):
        if not is_fresh({"claim_type": row[0], "observed_at": row[1], "freshness_hours": row[2]}, now):
            stale += 1
    out["stale_current_facts"] = stale
    for t in ("kb_save", "kb_verdict", "kb_visit", "kb_landmark", "kb_leg", "kb_alias",
              "kb_acclaim", "kb_menu_item", "kb_rating", "kb_search"):
        out[t[3:]] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    out["with_coordinates"] = con.execute(
        "SELECT COUNT(*) FROM kb_place p JOIN kb_location l ON l.location_id = p.location_id "
        "WHERE l.lat IS NOT NULL").fetchone()[0]
    return out


def render_stats(con: sqlite3.Connection, *, now: datetime | None = None) -> str:
    s = stats(con, now=now)
    cities = ", ".join(f"{c} {n}" for c, n in sorted(s["places_by_city"].items()))
    lists = ", ".join(f"{k or 'none'} {n}" for k, n in sorted(s["places_by_list"].items()))
    status = ", ".join(f"{k} {n}" for k, n in sorted(s["places_by_status"].items()))
    return "\n".join([
        f"Knowledge base: {s['places']} places ({cities})",
        f"Lists: {lists}. Status: {status}.",
        f"Facts: {s['facts']} ({', '.join(f'{k} {v}' for k, v in sorted(s['facts_by_method'].items()))}); "
        f"{s['stale_current_facts']} current facts past their freshness window.",
        f"Ratings {s['rating']}, menu items {s['menu_item']}, acclaim {s['acclaim']}, "
        f"saves {s['save']}, verdicts {s['verdict']}, visits {s['visit']}.",
        f"Landmarks {s['landmark']}, aliases {s['alias']}, legs {s['leg']}, searches {s['search']}.",
        f"Places with coordinates: {s['with_coordinates']} of {s['places']}.",
    ])
