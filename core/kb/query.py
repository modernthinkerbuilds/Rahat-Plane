"""core.kb.query — the tested questions the harness's tools ask the
knowledge base (ADR-018 §2.6, Bourdain BR-K9). No ad hoc SQL in agents.

Phase 1a ships the lookups the migration gate and the first tools need:
find a place by name or alias, list places by city or cuisine family,
places within a radius or near a landmark, the household's saves,
rejected and recent places to exclude, and the newest rating. Hours at
an arrival time arrive with the hours parser in Phase 1b.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from core.kb import geo
from core.kb.schema import current_fact, norm_name

_PLACE_COLS = ("place_id", "name", "name_norm", "brand_id", "location_id", "cuisine",
               "cuisine_family", "price_level", "status", "founded", "location_count",
               "format_tags", "room_tags", "source_list", "created_at", "updated_at")


def _row(r: tuple) -> dict:
    d = dict(zip(_PLACE_COLS, r[:len(_PLACE_COLS)]))
    d["format_tags"] = json.loads(d["format_tags"] or "[]")
    d["room_tags"] = json.loads(d["room_tags"] or "[]")
    if len(r) > len(_PLACE_COLS):
        d["lat"], d["lng"], d["city"], d["neighborhood"], d["address"] = r[len(_PLACE_COLS):len(_PLACE_COLS) + 5]
    return d


_SELECT = ("SELECT " + ", ".join("p." + c for c in _PLACE_COLS)
           + ", l.lat, l.lng, l.city, l.neighborhood, l.address "
           "FROM kb_place p LEFT JOIN kb_location l ON l.location_id = p.location_id ")


def place(con: sqlite3.Connection, place_id: str) -> dict | None:
    r = con.execute(_SELECT + "WHERE p.place_id = ?", (place_id,)).fetchone()
    return _row(r) if r else None


def places(con: sqlite3.Connection, *, city: str | None = None, status: str = "open",
           source_list: str | None = None) -> list[dict]:
    where, args = [], []
    if city:
        where.append("l.city = ?"); args.append(city)
    if status:
        where.append("p.status = ?"); args.append(status)
    if source_list:
        where.append("p.source_list = ?"); args.append(source_list)
    sql = _SELECT + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY p.name"
    return [_row(r) for r in con.execute(sql, args).fetchall()]


def find_place(con: sqlite3.Connection, text: str, *, city: str | None = None) -> list[dict]:
    """Exact normalized name, then alias, then substring. Returns
    candidates best-first; the caller decides whether to ask."""
    n = norm_name(text)
    if not n:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def add(rows):
        for r in rows:
            d = _row(r)
            if d["place_id"] not in seen:
                seen.add(d["place_id"]); out.append(d)

    city_sql, city_args = (" AND l.city = ?", [city]) if city else ("", [])
    add(con.execute(_SELECT + "WHERE p.name_norm = ?" + city_sql, [n] + city_args).fetchall())
    add(con.execute(_SELECT + "JOIN kb_alias a ON a.target_type = 'place' AND a.target_id = p.place_id "
                    "WHERE a.alias_norm = ?" + city_sql, [n] + city_args).fetchall())
    add(con.execute(_SELECT + "WHERE p.name_norm LIKE ?" + city_sql, [f"%{n}%"] + city_args).fetchall())
    return out


def landmark(con: sqlite3.Connection, text: str) -> dict | None:
    n = norm_name(text)
    r = con.execute(
        "SELECT k.landmark_id, k.name, k.kind, l.lat, l.lng, l.city FROM kb_landmark k "
        "LEFT JOIN kb_location l ON l.location_id = k.location_id "
        "WHERE k.name_norm = ? OR k.landmark_id IN (SELECT target_id FROM kb_alias "
        "WHERE target_type = 'landmark' AND alias_norm = ?) LIMIT 1", (n, n)).fetchone()
    if not r:
        return None
    return {"landmark_id": r[0], "name": r[1], "kind": r[2], "lat": r[3], "lng": r[4], "city": r[5]}


def by_family(con: sqlite3.Connection, family: str, *, city: str | None = None) -> list[dict]:
    rows = places(con, city=city)
    fam = family.lower()
    return [p for p in rows if (p.get("cuisine_family") or p.get("cuisine") or "").lower() == fam]


def within(con: sqlite3.Connection, lat: float, lng: float, *, minutes: int,
           mode: str = "drive", city: str | None = None) -> list[dict]:
    """Places within an estimated travel budget from a point, nearest
    first, each with `about_minutes`. Places without coordinates are
    left out (and the caller may say so)."""
    out = []
    for p in places(con, city=city):
        if p.get("lat") is None or p.get("lng") is None:
            continue
        m = geo.about_minutes(lat, lng, p["lat"], p["lng"], mode=mode)
        if m <= minutes:
            p["about_minutes"] = m
            out.append(p)
    return sorted(out, key=lambda d: d["about_minutes"])


def saved_ids(con: sqlite3.Connection, *, member_id: str | None = None) -> set[str]:
    sql = "SELECT DISTINCT place_id FROM kb_save" + (" WHERE member_id = ?" if member_id else "")
    return {r[0] for r in con.execute(sql, [member_id] if member_id else []).fetchall()}


def excluded_ids(con: sqlite3.Connection, *, now: datetime, recent_days: int = 14) -> dict[str, str]:
    """Places to leave out and why: rejected or closed by verdict, status
    not open, visited inside the variety window, loved regulars (which
    are exemplars, not answers)."""
    out: dict[str, str] = {}
    for pid, kind in con.execute(
            "SELECT place_id, kind FROM kb_verdict WHERE place_id IS NOT NULL "
            "AND kind IN ('rejected', 'closed', 'chain', 'reported_score', 'price', 'room')"):
        out.setdefault(pid, f"verdict:{kind}")
    for (pid,) in con.execute("SELECT place_id FROM kb_place WHERE status != 'open'"):
        out.setdefault(pid, "status")
    since = (now - timedelta(days=recent_days)).strftime("%Y-%m-%d")
    for (pid,) in con.execute("SELECT place_id FROM kb_visit WHERE at >= ?", (since,)):
        out.setdefault(pid, "recent")
    for (pid,) in con.execute("SELECT place_id FROM kb_regulars"):
        out.setdefault(pid, "regular")
    return out


def recent_families(con: sqlite3.Connection, *, now: datetime, days: int = 14) -> set[str]:
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return {r[0] for r in con.execute(
        "SELECT family FROM kb_recent_meals WHERE at >= ? AND family IS NOT NULL", (since,))}


def rating(con: sqlite3.Connection, place_id: str) -> dict | None:
    """The newest counted rating, or None. An 'unverified' fact never
    counts as a rating."""
    r = con.execute("SELECT source, value, count, observed_at FROM kb_rating WHERE place_id = ? "
                    "ORDER BY observed_at DESC, fact_id DESC LIMIT 1", (place_id,)).fetchone()
    if not r:
        return None
    return {"source": r[0], "value": r[1], "count": r[2], "observed_at": r[3]}


def fact(con: sqlite3.Connection, place_id: str, claim_type: str) -> dict | None:
    return current_fact(con, "place", place_id, claim_type)


def legs(con: sqlite3.Connection) -> list[dict]:
    cols = ("leg_id", "kind", "label", "stay_name", "stay_location_id", "start_date", "end_date",
            "tz", "minutes_mode", "radius_min", "schedule_json", "party_default", "note")
    return [dict(zip(cols, r)) for r in con.execute(
        "SELECT " + ", ".join(cols) + " FROM kb_leg ORDER BY start_date").fetchall()]


def leg_for(con: sqlite3.Connection, now: datetime) -> dict | None:
    """The trip leg whose dates contain today, else the home leg, else None."""
    today = now.strftime("%Y-%m-%d")
    home = None
    for lg in legs(con):
        if lg["kind"] == "home":
            home = lg
            continue
        if lg["start_date"] and lg["end_date"] and lg["start_date"] <= today <= lg["end_date"]:
            return lg
    return home
