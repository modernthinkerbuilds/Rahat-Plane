"""core.kb.migrate — load a seed file into the knowledge base, with a
per-field accounting report (ADR-018 §2.6, Bourdain BR-K6).

The gate for Phase 1a is "zero unaccounted fields": every key on every
seed place and every block in the seed's `meta` lands somewhere, and the
report says where. Fields with a typed home (ratings, acclaim, dishes,
hours text, saves) are projected; every other field becomes a `kb_fact`
row with `method='seed'` and whatever source and check date the seed
recorded for it, so nothing the Head of Product wrote by hand is lost.

Seed conventions this reads (the Sep 2026 trip seed):
    places[].<field>            a string, a list, or {value, source, checked, note, url}
    places[].rating             "Google 4.6 (2,065)" or "UNVERIFIED — …"
    places[].praise[]           {source, rank, date, url}
    places[].dishes[]           {name, price, note}
    places[].grid               {x, st} on the Manhattan grid
    meta.stays.<leg>            {name, address?, dates, minutes_are, note, grid?}
    meta.rejected[]             {name, city, status?, reason, url?, checked?, rating_reported?}
    meta.landmarks[]            {name, city, aliases[], grid?, note, url, checked}
    meta.ambiguous[]            {alias, means[], rule, found}
    meta.overrides[], weather{}, routes[], and documentation strings

Members in code are roles, never names: the seed's "him"/"her" become
"primary"/"spouse" (platform PR-S8).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.kb import geo
from core.kb.schema import (CUISINE_FAMILIES, add_fact, connect, location_id_for,
                            norm_name, place_id_for)

MEMBER_ROLES = {"him": "primary", "her": "spouse", "both": "both", "baby": "infant",
                "wife": "spouse", "owner": "primary"}

CITY_TZ = {"new_york": "America/New_York", "boston": "America/New_York",
           "cambridge": "America/New_York", "worcester": "America/New_York",
           "home": "America/Los_Angeles"}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_RATING_RE = re.compile(r"([A-Za-z][A-Za-z .]*?)\s*(\d\.\d)\s*(?:\(([\d,]+)\+?\))?")
_PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")

# Fields with a dedicated projection. Everything else is a seed fact.
PROJECTED = {
    "name": "kb_place.name", "city": "kb_location.city", "area": "kb_location.neighborhood",
    "address": "kb_location.address", "grid": "kb_location.grid + lat/lng (about)",
    "list": "kb_place.source_list (+ kb_save when 'yours')", "added": "kb_place.created_at",
    "meals": "kb_place.cuisine/cuisine_family + kb_fact(meals)",
    "tags": "kb_place.format_tags + kb_fact(tags)", "format": "kb_place.format_tags + kb_fact(format)",
    "quick": "kb_place.format_tags + kb_fact(quick)",
    "seating": "kb_place.room_tags + kb_fact(seating)",
    "price_level": "kb_place.price_level + kb_fact(price)",
    "founded": "kb_place.founded + kb_fact(founded)",
    "locations": "kb_place.location_count + kb_fact(location_count)",
    "rating": "kb_rating + kb_fact(rating)", "rating_unverified": "kb_fact(rating:unverified)",
    "praise": "kb_list + kb_acclaim + kb_fact(acclaim)",
    "dishes": "kb_menu_item + kb_fact(menu)",
    "hours_text": "kb_fact(hours) [typed kb_hours come with the hours parser]",
    "hours_source": "source of kb_fact(hours)", "menu_source": "source of kb_fact(menu)",
    "menu_checked": "observed_at of kb_fact(menu)",
    "him": "kb_fact(dish_note:primary)", "her": "kb_fact(dish_note:spouse)",
    "him_veg_day": "kb_fact(dish_note:primary:vegetarian_day)", "ask": "kb_fact(dish_note:ask)",
    "saved": "kb_save", "why": "kb_acclaim.quote + kb_fact(why)", "why_url": "kb_acclaim.url",
    "minutes_from_stay": "kb_fact(minutes_from_stay, applies_to=leg)",
}


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _parse_dates(text: str, year: int) -> tuple[str | None, str | None]:
    found = re.findall(r"([A-Za-z]{3})\w*\s+(\d{1,2})", text or "")
    iso = []
    for mon, day in found:
        m = _MONTHS.get(mon.lower()[:3])
        if m:
            iso.append(f"{year:04d}-{m:02d}-{int(day):02d}")
    if not iso:
        return None, None
    return iso[0], iso[-1]


def _src(v: Any) -> tuple[str | None, str | None, str | None]:
    """(source_name, source_url, observed_at) from a {value, source, url, checked} dict."""
    if not isinstance(v, dict):
        return None, None, None
    src = v.get("source")
    url = v.get("url")
    if url is None and isinstance(src, str) and src.startswith("http"):
        url = src
    return (src if isinstance(src, str) else None, url, v.get("checked") or v.get("date"))


def _rank(source_name: str | None, url: str | None) -> str:
    s = (source_name or "").lower() + " " + (url or "").lower()
    if "own" in s or "ordering" in s or "its site" in s or "own site" in s:
        return "own_site"
    if any(k in s for k in ("tripadvisor", "restaurantji", "wanderlog", "google", "opentable",
                            "yelp", "uber", "doordash", "allmenus")):
        return "aggregator"
    if any(k in s for k in ("infatuation", "eater", "globe", "chronicle", "kqed", "magazine",
                            "michelin", "bib", "guide", "times")):
        return "critic"
    if "reported" in s or "household" in s:
        return "user_report"
    return "seed"


def _coords(grid: Any) -> tuple[float | None, float | None]:
    if isinstance(grid, dict) and "x" in grid and "st" in grid:
        try:
            return geo.grid_to_latlng(float(grid["x"]), float(grid["st"]))
        except (TypeError, ValueError):
            return None, None
    return None, None


def _ensure_location(con, *, city: str, area: str | None, address: str | None,
                     grid: Any, name_hint: str, now: str) -> str:
    lat, lng = _coords(grid)
    loc_id = location_id_for(city, address, lat, lng, name_hint=name_hint)
    con.execute(
        "INSERT OR IGNORE INTO kb_location (location_id, lat, lng, address, neighborhood, city, "
        "grid_x, grid_st, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (loc_id, lat, lng, address, area, city,
         (grid or {}).get("x") if isinstance(grid, dict) else None,
         (grid or {}).get("st") if isinstance(grid, dict) else None, now))
    return loc_id


def _cuisine_from(meals: Any) -> tuple[str | None, str | None]:
    for w in (meals or []):
        w = str(w).lower()
        if w in CUISINE_FAMILIES:
            return w, CUISINE_FAMILIES[w]
    return None, None


def _price_cents(text: Any) -> int | None:
    m = _PRICE_RE.search(str(text or ""))
    return int(round(float(m.group(1)) * 100)) if m else None


def _diet_tags(note: str) -> list[str]:
    n = (note or "").lower()
    tags = []
    for k in ("vegan", "vegetarian", "chicken", "lamb", "beef", "pork", "seafood", "egg", "tofu"):
        if k in n:
            tags.append(k)
    return tags


def _gf(note: str) -> str:
    n = (note or "").lower()
    if "not gf" in n or "no gf" in n or "wheat" in n and "gf" not in n:
        return "no"
    if "on request" in n or "ask" in n and "gf" in n:
        return "on_request"
    if "gf" in n or "gluten-free" in n or "gluten free" in n:
        return "yes"
    return "unknown"


def migrate_seed(path: str | Path, *, now: datetime | None = None,
                 db_path: str | Path | None = None, trace_id: str | None = None,
                 primary_member: str = "primary") -> dict:
    """Load one seed file. Idempotent for entities (places, legs,
    landmarks, aliases); facts are appended, so a second run adds a
    second observation dated the same — harmless, and visible."""
    now = now or datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    meta: dict = data.get("meta", {}) or {}
    places: list[dict] = data.get("places", []) or []
    built = str(meta.get("built") or now.strftime("%Y-%m-%d"))[:10]
    year = int(built[:4])
    seed_id = f"seed:{_sha(str(meta.get('for') or path))}"
    trace = trace_id or f"seed-migrate-{now.strftime('%Y%m%d%H%M%S')}"

    report: dict[str, Any] = {
        "source": str(path), "built": built, "seed_id": seed_id,
        "places": 0, "facts": 0, "saves": 0, "verdicts": 0, "ratings": 0, "menu_items": 0,
        "acclaim": 0, "landmarks": 0, "aliases": 0, "legs": 0,
        "field_map": {}, "generic_fields": [], "meta_map": {}, "unaccounted": [],
    }
    seen_fields: set[str] = set()
    con = connect(db_path)

    def fact(**kw) -> int:
        fid = add_fact(con, trace_id=trace, now=now, **kw)
        report["facts"] += 1
        return fid

    try:
        # ── legs from meta.stays ───────────────────────────────────────
        party_default = meta.get("party_default")
        for key, stay in (meta.get("stays") or {}).items():
            start, end = _parse_dates(str(stay.get("dates", "")), year)
            minutes_mode = "walk" if "walk" in str(stay.get("minutes_are", "")).lower() else "drive"
            loc_id = _ensure_location(con, city=key, area=None, address=stay.get("address"),
                                      grid=stay.get("grid"), name_hint=stay.get("name", key), now=now_s)
            con.execute(
                "INSERT OR REPLACE INTO kb_leg (leg_id, kind, label, stay_name, stay_location_id, "
                "start_date, end_date, tz, minutes_mode, schedule_json, party_default, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, "trip", key.replace("_", " ").title(), stay.get("name"), loc_id, start, end,
                 CITY_TZ.get(key, "America/New_York"), minutes_mode,
                 json.dumps(stay.get("schedule") or {}, ensure_ascii=False),
                 party_default, stay.get("note")))
            con.execute(
                "INSERT OR IGNORE INTO kb_landmark (landmark_id, name, name_norm, kind, location_id, "
                "note, created_at) VALUES (?,?,?,?,?,?,?)",
                (f"stay:{key}", stay.get("name", key), norm_name(stay.get("name", key)), "stay",
                 loc_id, stay.get("note"), now_s))
            fact(subject_type="leg", subject_id=key, claim_type="stay", value=stay,
                 source_name="seed", method="seed", observed_at=built)
            report["legs"] += 1
        report["meta_map"]["stays"] = "kb_leg + kb_landmark(stay) + kb_location + kb_fact(leg:stay)"
        report["meta_map"]["party_default"] = "kb_leg.party_default"

        # ── landmarks ──────────────────────────────────────────────────
        for lm in meta.get("landmarks") or []:
            city = lm.get("city", "")
            loc_id = _ensure_location(con, city=city, area=None, address=lm.get("address"),
                                      grid=lm.get("grid"), name_hint=lm.get("name", ""), now=now_s)
            lid = f"lm:{_sha(norm_name(lm.get('name', '')) + '|' + city)}"
            con.execute(
                "INSERT OR IGNORE INTO kb_landmark (landmark_id, name, name_norm, kind, location_id, "
                "note, created_at) VALUES (?,?,?,?,?,?,?)",
                (lid, lm.get("name"), norm_name(lm.get("name", "")), lm.get("kind", "landmark"),
                 loc_id, lm.get("note"), now_s))
            for a in lm.get("aliases") or []:
                con.execute(
                    "INSERT OR IGNORE INTO kb_alias (alias_norm, target_type, target_id, kind, "
                    "created_at) VALUES (?,?,?,?,?)", (norm_name(a), "landmark", lid, "alias", now_s))
                report["aliases"] += 1
            fact(subject_type="landmark", subject_id=lid, claim_type="note", value=lm,
                 source_url=lm.get("url"), source_name="seed", method="seed",
                 observed_at=str(lm.get("checked") or built))
            report["landmarks"] += 1
        report["meta_map"]["landmarks"] = "kb_landmark + kb_location + kb_alias + kb_fact(landmark:note)"

        # ── ambiguous aliases ──────────────────────────────────────────
        for amb in meta.get("ambiguous") or []:
            con.execute(
                "INSERT OR REPLACE INTO kb_alias (alias_norm, target_type, target_id, kind, readings, "
                "rule, created_at) VALUES (?,?,?,?,?,?,?)",
                (norm_name(amb.get("alias", "")), "area", amb.get("alias", ""), "ambiguous",
                 json.dumps(amb.get("means") or [], ensure_ascii=False), amb.get("rule"), now_s))
            report["aliases"] += 1
        report["meta_map"]["ambiguous"] = "kb_alias(kind=ambiguous, readings, rule)"

        # ── places ─────────────────────────────────────────────────────
        for p in places:
            seen_fields.update(p.keys())
            city = p.get("city", "") or ""
            grid = p.get("grid")
            lat, lng = _coords(grid)
            loc_id = _ensure_location(con, city=city, area=p.get("area"), address=p.get("address"),
                                      grid=grid, name_hint=p.get("name", ""), now=now_s)
            pid = place_id_for(p.get("name", ""), city, address=p.get("address"), lat=lat, lng=lng)
            cuisine, family = _cuisine_from(p.get("meals"))
            format_tags = list(p.get("tags") or [])
            if p.get("format"):
                format_tags.append(str(p["format"]))
            if p.get("quick"):
                format_tags.append("quick")
            room_tags = []
            seating = p.get("seating")
            if isinstance(seating, dict):
                room_tags = [f"seating:{k}" for k, v in seating.items() if v is True]
            price = p.get("price_level")
            price_text = price.get("value") if isinstance(price, dict) else price
            founded = p.get("founded")
            founded_v = founded.get("value") if isinstance(founded, dict) else founded
            try:
                founded_i = int(str(founded_v)[:4]) if founded_v else None
            except ValueError:
                founded_i = None
            created = str(p.get("added") or built)[:10]
            con.execute(
                "INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, cuisine_family, "
                "price_level, status, founded, location_count, format_tags, room_tags, source_list, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(place_id) DO UPDATE SET name=excluded.name, location_id=excluded.location_id, "
                "cuisine=COALESCE(excluded.cuisine, kb_place.cuisine), "
                "cuisine_family=COALESCE(excluded.cuisine_family, kb_place.cuisine_family), "
                "price_level=COALESCE(excluded.price_level, kb_place.price_level), "
                "founded=COALESCE(excluded.founded, kb_place.founded), "
                "location_count=COALESCE(excluded.location_count, kb_place.location_count), "
                "format_tags=excluded.format_tags, room_tags=excluded.room_tags, "
                "source_list=excluded.source_list, updated_at=excluded.updated_at",
                (pid, p.get("name"), norm_name(p.get("name", "")), loc_id, cuisine, family,
                 price_text, "open", founded_i, p.get("locations"),
                 json.dumps(sorted(set(format_tags))), json.dumps(room_tags),
                 p.get("list", ""), created, now_s))
            report["places"] += 1

            def pf(claim: str, value: Any, *, source_name=None, source_url=None, observed=None,
                   rank=None, applies_to=None) -> int:
                sn, su, ob = _src(value)
                sn = source_name or sn or "seed"
                su = source_url or su
                return fact(subject_type="place", subject_id=pid, claim_type=claim, value=value,
                            source_url=su, source_name=sn, source_rank=rank or _rank(sn, su),
                            method="seed", observed_at=str(observed or ob or created),
                            applies_to=applies_to)

            # projected fields
            if p.get("rating"):
                rtext = str(p["rating"])
                if rtext.upper().startswith("UNVERIFIED"):
                    pf("rating", rtext, rank="unverified")
                else:
                    for m in _RATING_RE.finditer(rtext):
                        src, val, cnt = m.group(1).strip(" ;,"), float(m.group(2)), m.group(3)
                        count = int(cnt.replace(",", "")) if cnt else None
                        fid = pf("rating", {"value": val, "source": src, "count": count,
                                            "checked": created}, source_name=src)
                        con.execute("INSERT OR REPLACE INTO kb_rating (fact_id, place_id, source, value, "
                                    "count, observed_at) VALUES (?,?,?,?,?,?)",
                                    (fid, pid, src, val, count, created))
                        report["ratings"] += 1
            if "rating_unverified" in p:
                pf("rating:unverified", bool(p["rating_unverified"]), rank="unverified")
            for pr in p.get("praise") or []:
                list_id = f"list:{_sha(str(pr.get('source')) + '|' + str(pr.get('url')))}"
                con.execute("INSERT OR IGNORE INTO kb_list (list_id, name, publisher, date, url, city, "
                            "fetched_at) VALUES (?,?,?,?,?,?,?)",
                            (list_id, pr.get("source"), (pr.get("source") or "").split(" — ")[0],
                             pr.get("date"), pr.get("url"), city, created))
                fid = pf("acclaim", pr, source_name=pr.get("source"), source_url=pr.get("url"),
                         observed=pr.get("date") or created, rank="critic")
                con.execute("INSERT INTO kb_acclaim (place_id, list_id, writer, rank, date, url, quote, "
                            "fact_id) VALUES (?,?,?,?,?,?,?,?)",
                            (pid, list_id, pr.get("writer"), pr.get("rank"), pr.get("date"),
                             pr.get("url"), pr.get("quote"), fid))
                report["acclaim"] += 1
            if p.get("why"):
                fid = pf("why", {"value": p["why"], "url": p.get("why_url")},
                         source_url=p.get("why_url"))
                if p.get("why_url"):
                    con.execute("INSERT INTO kb_acclaim (place_id, writer, url, quote, fact_id) "
                                "VALUES (?,?,?,?,?)", (pid, None, p.get("why_url"), p["why"], fid))
                    report["acclaim"] += 1
            if p.get("dishes"):
                menu_src = p.get("menu_source")
                menu_checked = p.get("menu_checked")
                checked = menu_checked.get("checked") if isinstance(menu_checked, dict) else menu_checked
                fid = pf("menu", {"value": p["dishes"], "source": menu_src, "checked": checked},
                         source_name=menu_src, observed=checked or created)
                for d in p["dishes"]:
                    if not isinstance(d, dict):
                        d = {"name": str(d)}
                    con.execute("INSERT INTO kb_menu_item (place_id, fact_id, name, price_cents, price_text, "
                                "diet_tags, gf, note) VALUES (?,?,?,?,?,?,?,?)",
                                (pid, fid, d.get("name", "?"), _price_cents(d.get("price")),
                                 d.get("price"), json.dumps(_diet_tags(d.get("note", ""))),
                                 _gf(d.get("note", "")), d.get("note")))
                    report["menu_items"] += 1
            if p.get("hours_text"):
                hs = p.get("hours_source")
                hs_name, hs_url, hs_checked = _src(hs) if isinstance(hs, dict) else (None, None, None)
                if isinstance(hs, dict) and hs.get("value"):
                    hs_name = hs.get("value")
                pf("hours", {"value": p["hours_text"], "source": hs_name, "checked": hs_checked},
                   source_name=hs_name or "seed", source_url=hs_url, observed=hs_checked or created)
            for fld, member in (("him", "primary"), ("her", "spouse")):
                if p.get(fld):
                    pf(f"dish_note:{member}", p[fld])
            if p.get("him_veg_day"):
                pf("dish_note:primary:vegetarian_day", p["him_veg_day"])
            if p.get("ask"):
                ask = p["ask"]
                if isinstance(ask, dict):
                    ask = {MEMBER_ROLES.get(k, k): v for k, v in ask.items()}
                pf("dish_note:ask", ask)
            if p.get("minutes_from_stay") is not None:
                pf("minutes_from_stay", p["minutes_from_stay"], applies_to=city)
            if p.get("list") == "yours":
                con.execute("INSERT INTO kb_save (place_id, member_id, saved_at, heard_from, note, trace_id) "
                            "VALUES (?,?,?,?,?,?)", (pid, primary_member, created, "owner's list",
                                                     "seed list=yours", trace))
                report["saves"] += 1
            if isinstance(p.get("saved"), dict):
                sv = p["saved"]
                con.execute("INSERT INTO kb_save (place_id, member_id, saved_at, heard_from, occasion, "
                            "for_date, note, trace_id) VALUES (?,?,?,?,?,?,?,?)",
                            (pid, primary_member, str(sv.get("date") or created), sv.get("by"),
                             sv.get("for"), sv.get("for"), sv.get("words"), trace))
                report["saves"] += 1
            for fld in ("price_level", "founded", "locations", "meals", "tags", "format", "quick",
                        "seating"):
                if fld in p and p[fld] is not None:
                    pf(fld if fld != "locations" else "location_count", p[fld])
            # every remaining field → a generic seed fact
            handled = set(PROJECTED) | {"rating", "rating_unverified", "praise", "dishes", "hours_text",
                                        "hours_source", "menu_source", "menu_checked", "him", "her",
                                        "him_veg_day", "ask", "minutes_from_stay", "saved", "why",
                                        "why_url", "list", "added", "name", "city", "area", "address",
                                        "grid"}
            for fld, val in p.items():
                if fld in handled or val is None:
                    continue
                pf(fld, val)
                if fld not in report["generic_fields"]:
                    report["generic_fields"].append(fld)

        for fld in sorted(seen_fields):
            report["field_map"][fld] = PROJECTED.get(fld) or f"kb_fact({fld}) [generic seed fact]"

        # ── rejected entries → places with status + verdicts ───────────
        for r in meta.get("rejected") or []:
            city = r.get("city", "") or ""
            loc_id = _ensure_location(con, city=city, area=None, address=r.get("address"),
                                      grid=r.get("grid"), name_hint=r.get("name", ""), now=now_s)
            pid = place_id_for(r.get("name", ""), city, address=r.get("address"))
            status = r.get("status") or "open"
            checked = str(r.get("checked") or built)
            con.execute(
                "INSERT INTO kb_place (place_id, name, name_norm, location_id, status, format_tags, "
                "room_tags, source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(place_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (pid, r.get("name"), norm_name(r.get("name", "")), loc_id, status, "[]", "[]",
                 "rejected", checked[:10], now_s))
            fid = fact(subject_type="place", subject_id=pid, claim_type="status", value=status,
                       source_url=r.get("url"), source_name=r.get("found_by") or "seed",
                       source_rank=_rank(r.get("found_by"), r.get("url")), method="seed",
                       observed_at=checked)
            con.execute("UPDATE kb_place SET status_fact_id = ? WHERE place_id = ?", (fid, pid))
            fact(subject_type="place", subject_id=pid, claim_type="rejection", value=r,
                 source_url=r.get("url"), source_name="seed", method="seed", observed_at=checked)
            kind = "closed" if status in ("closed", "moved", "seasonal_closed", "coming_soon") else "rejected"
            con.execute("INSERT INTO kb_verdict (place_id, member_id, kind, value_json, text, at, scope, "
                        "trace_id) VALUES (?,?,?,?,?,?,?,?)",
                        (pid, primary_member, kind, json.dumps({"status": status,
                                                                "scope": r.get("scope")}),
                         r.get("reason") or r.get("verdict"), checked, "standing", trace))
            report["verdicts"] += 1
            if r.get("rating_reported"):
                con.execute("INSERT INTO kb_verdict (place_id, member_id, kind, value_json, text, at, "
                            "scope, trace_id) VALUES (?,?,?,?,?,?,?,?)",
                            (pid, primary_member, "reported_score",
                             json.dumps(r["rating_reported"], ensure_ascii=False),
                             str(r["rating_reported"]), checked, "standing", trace))
                report["verdicts"] += 1
            report["places"] += 1
        report["meta_map"]["rejected"] = "kb_place(status) + kb_fact(status, rejection) + kb_verdict"

        # ── the rest of meta → typed or documented facts ───────────────
        for ov in meta.get("overrides") or []:
            fact(subject_type="household", subject_id="household", claim_type="override",
                 value={**ov, "who": [MEMBER_ROLES.get(w, w) for w in ov.get("who", [])]},
                 source_name=ov.get("source"), method="user_report",
                 observed_at=str(ov.get("from") or built), applies_to=ov.get("until"))
        report["meta_map"]["overrides"] = "kb_fact(household:override) [historic; live overrides use scoped state]"
        for key, w in (meta.get("weather") or {}).items():
            fact(subject_type="weather", subject_id=key, claim_type="forecast", value=w,
                 source_name=w.get("source") if isinstance(w, dict) else None, method="seed",
                 observed_at=str((w or {}).get("issued") or built) if isinstance(w, dict) else built)
        report["meta_map"]["weather"] = "kb_fact(weather:forecast, with issuance)"
        for rt in meta.get("routes") or []:
            fact(subject_type="route", subject_id=f"route:{_sha(str(rt.get('name')))}",
                 claim_type="plan", value=rt, source_name="seed", method="seed",
                 observed_at=str(rt.get("checked") or built))
        report["meta_map"]["routes"] = "kb_fact(route:plan)"
        for key in ("closures", "praise", "rules", "grid", "by", "for", "built", "updated", "caveats"):
            if key in meta:
                fact(subject_type="seed", subject_id=seed_id, claim_type=key, value=meta[key],
                     source_name="seed", method="seed", observed_at=built)
                report["meta_map"][key] = f"kb_fact(seed:{key}) [documentation]"
        report["unaccounted"] = sorted(set(meta.keys()) - set(report["meta_map"]))
        con.execute("INSERT OR REPLACE INTO kb_meta (key, value) VALUES (?, ?)",
                    (f"migrated:{seed_id}", json.dumps({"at": now_s, "built": built,
                                                        "places": report["places"]})))
        con.commit()
    finally:
        con.close()
    return report


def render_report(report: dict) -> str:
    lines = [f"Seed {report['source']} (built {report['built']}) → knowledge base",
             f"places {report['places']} · facts {report['facts']} · ratings {report['ratings']} · "
             f"menu items {report['menu_items']} · acclaim {report['acclaim']} · saves {report['saves']} · "
             f"verdicts {report['verdicts']} · landmarks {report['landmarks']} · aliases {report['aliases']} · "
             f"legs {report['legs']}",
             "", "Place fields:"]
    for k, v in report["field_map"].items():
        lines.append(f"  {k:<20} → {v}")
    lines += ["", "Meta blocks:"]
    for k, v in report["meta_map"].items():
        lines.append(f"  {k:<20} → {v}")
    lines += ["", f"Unaccounted: {report['unaccounted'] or 'none'}"]
    return "\n".join(lines)
