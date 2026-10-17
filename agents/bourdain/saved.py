"""agents.bourdain.saved — `/list`: the household's saved places from the
knowledge base, ranked from where they are.

The list the harness serves is `kb_save` (the household's saves, by any
member, from any source: the owner's seed, a "save X" in chat, a Maps
link), joined to the place and its coordinates. It is shown nearest
first from the same origin the harness ranks an ask from — a location
override the household just set, the trip's stay, else home — with the
places farther than `NEAR_KM` folded into one "elsewhere" line by city,
reachable with `/list <city>`.

LIVE 2026-10-17: `/list` at home answered from the trip's tables with
the previous month's city, nearest the hotel; the household's own town
was not on it. Decision D13 (ADR-018): the grammar is code, but the
code reads the one memory the harness reads.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from bridges.events.digest import md_escape, md_url
from core import hours as chours
from core.kb import cities, geo, schema as kb

NEAR_KM = 80.0              # the Bay Area from end to end; farther is another trip
MAX_ROWS = 12


def saves(con: sqlite3.Connection) -> list[dict]:
    """One row per saved place (the newest save), with its city and coordinates."""
    rows = con.execute(
        "SELECT s.place_id, p.name, p.status, p.cuisine, l.city, l.neighborhood, l.lat, l.lng, "
        "MAX(s.saved_at) AS saved_at, MAX(COALESCE(s.heard_from, '')) AS heard_from "
        "FROM kb_save s JOIN kb_place p ON p.place_id = s.place_id "
        "LEFT JOIN kb_location l ON l.location_id = p.location_id "
        "GROUP BY s.place_id ORDER BY saved_at DESC").fetchall()
    keys = ("place_id", "name", "status", "cuisine", "city", "neighborhood", "lat", "lng", "saved_at", "heard_from")
    return [dict(zip(keys, r)) for r in rows]


def _fact_text(con: sqlite3.Connection, place_id: str, claim: str) -> tuple[str | None, str]:
    fact = kb.current_fact(con, "place", place_id, claim)
    if not fact:
        return None, ""
    v = fact.get("value")
    text = v.get("value") if isinstance(v, dict) else v
    return (text if isinstance(text, str) and text.strip() else None), str(fact.get("observed_at") or "")[:10]


def _hours_words(con: sqlite3.Connection, place_id: str, now: datetime) -> str:
    text, read = _fact_text(con, place_id, "hours")
    if not text:
        return ""
    try:
        read_s = datetime.strptime(read, "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        read_s = read
    h = chours.parse(text)
    if not h.parsed:
        word = "hours on file"
    else:
        res = chours.open_at(h, now.weekday(), now.hour * 60 + now.minute)
        if res.get("open"):
            word = f"open till {res.get('closes_at')}" if res.get("closes_at") else "open now"
        elif res.get("open") is False:
            word = "closed today" if res.get("why") == "closed that day" else (res.get("why") or "closed now")
        else:
            word = "hours unconfirmed"
    return f"{word} (read {read_s})" if read_s else word


def city_filter(args: str) -> str | None:
    """`/list new york` → "new_york"; `/list` → None. The trip cities' aliases are honoured."""
    text = (args or "").strip().strip("·-: ")
    if not text:
        return None
    from agents.bourdain import resolve
    return resolve.city_from_text(text) or cities.key(text)


def render(con: sqlite3.Connection, *, now: datetime, origin: tuple[float, float] | None, origin_label: str | None,
           minutes_mode: str = "drive", city: str | None = None) -> str:
    rows = [r for r in saves(con) if (r.get("status") or "open") != "closed"]
    if city:
        rows = [r for r in rows if (r.get("city") or "") == city]
        if not rows:
            return (f"Nothing saved in {md_escape(cities.label(city))} yet. `/list` shows everything near you; "
                    "`save <name>, <where you heard>` adds one.")
    if not rows:
        return "Your list is empty. `save <name>, <where you heard>` adds one, or paste a Maps link."
    for r in rows:
        if origin and r.get("lat") is not None and r.get("lng") is not None:
            r["km"] = geo.haversine_km(origin[0], origin[1], r["lat"], r["lng"])
            r["minutes"] = geo.about_minutes(origin[0], origin[1], r["lat"], r["lng"], mode=minutes_mode)
        else:
            r["km"], r["minutes"] = None, None
    if origin and not city:
        near = [r for r in rows if r["km"] is not None and r["km"] <= NEAR_KM]
        far = [r for r in rows if r["km"] is None or r["km"] > NEAR_KM]
    else:
        near, far = rows, []
    # Minutes mean something within reach; a trip city's list is shown as saved, newest first.
    ranked = bool(origin) and bool(near) and all(r["km"] is not None and r["km"] <= NEAR_KM for r in near)
    if ranked:
        near.sort(key=lambda r: (r["minutes"], r["name"].lower()))
    else:
        for r in near:
            r["minutes"] = None
    if city:
        where = md_escape(cities.label(city))
    elif origin and origin_label:
        where = f"near {md_escape(origin_label)}"
    else:
        where = "everywhere"
    lines = [f"*Your list · {where} · {'nearest first' if ranked else 'newest first'}*", ""]
    if not near:
        lines.append(f"Nothing saved within about {int(NEAR_KM)} km of {md_escape(origin_label or 'here')} yet.")
    for r in near[:MAX_ROWS]:
        bits = [f"★ {md_escape(r['name'])}"]
        place_bits = [b for b in (r.get("neighborhood"), cities.label(r.get("city"))) if b]
        if place_bits:
            bits.append(md_escape(" · ".join(dict.fromkeys(place_bits))))
        if r["minutes"] is not None:
            bits.append(f"~{r['minutes']} min {'walk' if minutes_mode == 'walk' else 'drive'}")
        if r.get("cuisine"):
            bits.append(md_escape(str(r["cuisine"])))
        hours = _hours_words(con, r["place_id"], now)
        if hours:
            bits.append(md_escape(hours))
        line = " · ".join(bits)
        url, _ = _fact_text(con, r["place_id"], "map_url")
        if url and url.startswith("http"):
            line += f" — [map]({md_url(url)})"
        lines.append(line)
    if len(near) > MAX_ROWS:
        lines.append(f"…and {len(near) - MAX_ROWS} more nearby.")
    if far:
        by_city: dict[str, int] = {}
        for r in far:
            key = r.get("city") or "elsewhere"
            by_city[key] = by_city.get(key, 0) + 1
        ranked = sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))
        parts = [f"{md_escape(cities.label(c))} {n}" for c, n in ranked]
        lines.append("")
        lines.append("Elsewhere: " + " · ".join(parts) + f" — `/list {cities.label(ranked[0][0]).lower()}` shows them.")
    return "\n".join(lines)
