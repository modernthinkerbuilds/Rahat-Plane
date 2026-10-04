"""agents.bourdain.maps and the maps_search tool: Google Maps places, many
at once, into the knowledge base with Google Maps as the source.
Hermetic: synthetic Maps records (fictional places), a stubbed wire and
stubbed geocoders. Coordinates are arbitrary points."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import geocode as cgeo, llm_chat, manifest  # noqa: E402
from core.harness.evidence import Evidence  # noqa: E402
from core.kb import migrate, query, schema as kb  # noqa: E402
from agents.bourdain import maps as bmaps, tools as btools  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402

NOW = datetime(2026, 10, 4, 13, 40)        # a Sunday
ORIGIN = (10.0, 20.0)

WEEK = ["Sunday: 8 AM–6 PM", "Monday: Closed", "Tuesday: 8 AM–6 PM", "Wednesday: 8 AM–6 PM", "Thursday: 8 AM–9 PM",
        "Friday: 8 AM–10 PM", "Saturday: 8 AM–10 PM"]


def record(name, address, *, rating="4.6 (231 reviews)", price="$10-20", desc="Cozy board game cafe serving coffee.",
           attrs="Atmosphere: casual, cozy. Children: good for kids, high chairs. Crowd: family-friendly, college students. "
                 "Offerings: coffee, quick bite, vegetarian options. Service options: dine-in, takeout.",
           menu=("Matcha Latte", "Oat Waffle(vegetarian, gluten-free)"), hours=WEEK, extra=""):
    lines = [f"**Title:** {name}", "", "**About:**", "", f"* **Address:** {address}"]
    if rating:
        lines.append(f"* **Rating:** {rating}")
    if price:
        lines.append(f"* **Price Range:** {price}")
    lines += ["* **Phone:** +1 555-010-0100", f"* **Website:** https://{name.lower().replace(' ', '')}.test/",
              f"* **Description:** {desc}", f"* **Attributes:** Information about the place: {attrs}"]
    if menu:
        lines.append("* **Menu Highlight:**")
        lines += [f"  * {m}" for m in menu]
    if hours:
        lines.append("* **Opening Hours:**")
        lines += [f"  * {h}" for h in hours]
    if extra:
        lines.append(extra)
    pid = "ChIJ-" + name.lower().replace(" ", "-")
    return {"url": f"https://maps.google.com/maps?cid={abs(hash(name)) % 10**12}", "title": f"{name} - Google Maps",
            "text": "\n".join(lines), "place_id": pid}


# ── reading a Maps record ───────────────────────────────────────────────
def test_a_record_reads_into_a_place():
    mp = bmaps.parse_place(record("Lantern House Games", "10 Elm Rd #106, Alpha City, CA 90001"))
    assert (mp.name, mp.city, mp.rating, mp.rating_count) == ("Lantern House Games", "Alpha City", 4.6, 231)
    assert (mp.price_range, mp.price_level) == ("$10-20", "$$") and mp.status == "open"
    assert mp.hours_text.startswith("Sunday: 8 AM–6 PM; Monday: Closed")
    assert mp.menu == [{"name": "Matcha Latte", "tags": []}, {"name": "Oat Waffle", "tags": ["vegetarian", "gluten-free"]}]
    assert mp.attributes["children"] == ["good for kids", "high chairs"]
    assert {"cafe", "board_games", "board_game_cafe", "dine_in", "takeout", "quick"} <= set(mp.format_tags)
    assert {"cozy", "good_for_kids", "high_chairs", "family-friendly"} <= set(mp.room_tags)
    assert "college_students" not in mp.room_tags and not mp.bar_led and not mp.seafood_led


@pytest.mark.parametrize("desc,bar,seafood,cuisine", [
    ("Spacious gaming bar and event venue with board games.", True, False, None),
    ("Neighborhood coffee bar with an espresso bar and pastries.", False, False, "cafe"),
    ("Family-run Burmese café serving tea leaf salad.", False, False, "burmese"),
    ("Casual Indian cafe with chai and dosas.", False, False, "indian"),
    ("Lively board game cafe serving coffee and a breakfast menu.", False, False, "cafe"),
    ("Oyster bar and seafood counter.", False, True, None),
    ("Old-school gelato and cannoli shop.", False, False, "dessert"),
])
def test_rooms_and_cuisines_are_read_from_the_description(desc, bar, seafood, cuisine):
    mp = bmaps.parse_place(record("Some Place", "1 A St, Alpha City, CA 90001", desc=desc))
    assert (mp.bar_led, mp.seafood_led) == (bar, seafood)
    assert mp.cuisine == cuisine or (cuisine is None and mp.cuisine not in kb.CUISINE_FAMILIES)


@pytest.mark.parametrize("rng,level", [("$1-10", "$"), ("$10-20", "$$"), ("$20-30", "$$"), ("$30-50", "$$$"),
                                       ("$50-100", "$$$$"), ("$100+", "$$$$"), ("$$", "$$"), (None, None)])
def test_price_ranges_read_as_the_household_scale(rng, level):
    assert bmaps.price_level(rng) == level


def test_closed_places_say_so():
    mp = bmaps.parse_place(record("Gone Cafe", "1 A St, Alpha City, CA 90001", extra="* **Status:** Permanently closed"))
    assert mp.status == "permanently closed"


# ── into the knowledge base ─────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "maps.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=path)
    return path


def test_the_place_is_written_with_google_maps_as_the_source(db):
    con = kb.connect(db)
    mp = bmaps.parse_place(record("Lantern House Games", "10 Elm Rd, Alpha City, CA 90001"))
    w = bmaps.write_place(con, mp, now=NOW, trace_id="t1")
    con.commit()
    p = query.place(con, w["place_id"])
    assert p["source_list"] == "maps" and p["price_level"] == "$$" and p["status"] == "open"
    f = kb.current_fact(con, "place", w["place_id"], "hours")
    assert f["source_name"] == "Google Maps" and f["source_url"].startswith("https://maps.google.com/")
    assert query.rating(con, w["place_id"])["source"] == "Google Maps"
    gf = con.execute("SELECT name, gf FROM kb_menu_item WHERE place_id = ?", (w["place_id"],)).fetchall()
    assert ("Oat Waffle", "yes") in gf
    con.close()
    ev = Evidence(kb.connect(db), now=NOW, mode="home")
    pe = ev.place(w["place_id"])
    assert pe.status == "open" and pe.rating["value"] == 4.6 and pe.room["bar_led"] is False
    assert pe.value("map_url") == mp.url
    from core import hours as chours
    assert chours.open_at(pe.hours(), NOW.weekday(), 13 * 60 + 40)["open"] is True
    assert chours.open_at(pe.hours(), 0, 12 * 60)["open"] is False                  # closed Mondays
    ev.close()


def test_a_place_already_on_record_is_the_same_place(db):
    con = kb.connect(db)
    casa = query.find_place(con, "Casa Pasta")[0]["place_id"]
    mp = bmaps.parse_place(record("Casa Pasta", "235 W 23rd St, Alpha City, NY 10011", desc="Italian trattoria."))
    w = bmaps.write_place(con, mp, now=NOW)
    assert w["place_id"] == casa and len(query.find_place(con, "Casa Pasta")) == 1
    branch = bmaps.parse_place(record("Casa Pasta - Chelsea", "235 W 23rd St, Alpha City, NY 10011", desc="Italian trattoria."))
    assert bmaps.write_place(con, branch, now=NOW)["place_id"] == casa                # a branch label, the same street
    con.close()


def test_maps_facts_go_after_ninety_days_and_maps_only_places_with_them(db):
    con = kb.connect(db)
    old = NOW - timedelta(days=95)
    a = bmaps.write_place(con, bmaps.parse_place(record("Old Maps Cafe", "1 A St, Alpha City, CA 90001")), now=old)
    b = bmaps.write_place(con, bmaps.parse_place(record("Loved Maps Cafe", "2 B St, Alpha City, CA 90001")), now=old)
    con.execute("INSERT INTO kb_verdict (place_id, member_id, kind, text, at, scope) VALUES (?,?,?,?,?,?)",
                (b["place_id"], "primary", "loved", "we loved it", NOW.strftime("%Y-%m-%d %H:%M:%S"), "standing"))
    fresh = bmaps.write_place(con, bmaps.parse_place(record("New Maps Cafe", "3 C St, Alpha City, CA 90001")), now=NOW)
    con.commit()
    gone = bmaps.purge(con, now=NOW)
    con.commit()
    assert gone >= 10
    assert query.place(con, a["place_id"]) is None                                     # only Maps knew it
    assert query.place(con, b["place_id"]) is not None                                # the household's verdict keeps it
    assert kb.current_fact(con, "place", b["place_id"], "hours") is None              # but not Google's data
    assert kb.current_fact(con, "place", fresh["place_id"], "hours") is not None
    assert con.execute("SELECT COUNT(*) FROM kb_rating WHERE place_id = ?", (a["place_id"],)).fetchone()[0] == 0
    con.close()


# ── the tool ────────────────────────────────────────────────────────────
@pytest.fixture
def ctx(db, tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(tmp_path / "missing.json"))
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    return {"now": NOW, "chat_id": "c1", "db_path": db, "trace_id": "tr-maps", "mode": "home",
            "leg": {"leg_id": "home", "kind": "home", "city": "alpha_city", "minutes_mode": "drive"},
            "origin_latlng": ORIGIN, "origin_label": "home", "diners": ["primary"]}


def _wire(monkeypatch, by_area: dict[str, list[dict]], calls: list):
    def wire(req):
        text = req["messages"][0]["parts"][0]["text"]
        calls.append(text)
        area = next((a for a in by_area if f"near {a}" in text), None)
        chunks = by_area.get(area, [])
        return {"text": "\n".join(c["title"] for c in chunks), "parts": [{"text": "ok"}], "stop_reason": "stop",
                "citations": [], "usage": {"in": 50, "out": 40}, "search_queries": 2, "error": None,
                "maps_places": chunks}
    monkeypatch.setattr(llm_chat, "_call_wire", wire)


def _geocode(monkeypatch):
    def fake(addresses, **kw):
        out = {}
        for i, a in enumerate(addresses):
            out[a] = {"lat": ORIGIN[0] + 0.05 * (i + 1), "lng": ORIGIN[1], "source_url": "https://geo.test/" + str(i),
                      "source_name": "test geocoder", "observed_at": NOW.strftime("%Y-%m-%d %H:%M:%S")}
        return out
    monkeypatch.setattr(cgeo, "geocode_many", fake)


def _reg(ctx):
    return btools.build_registry(ctx, allow=manifest.load("bourdain").tools)


def test_maps_search_finds_many_places_with_what_the_checks_need(ctx, monkeypatch):
    calls: list = []
    places = [record(f"Board Cafe {i}", f"{i} Main St, Alpha City, CA 90001") for i in range(1, 8)]
    places.append(record("Gaming Bar", "99 Night St, Alpha City, CA 90001", desc="A gaming bar with cocktails."))
    _wire(monkeypatch, {"Alpha City": places}, calls)
    _geocode(monkeypatch)
    res = _reg(ctx).execute("maps_search", {"query": "board game cafés", "near": "Alpha City", "within_minutes": 50}, ctx)
    assert res.ok and res.value["found"] == 8 and res.value["source"] == "Google Maps" and len(calls) == 1
    first = res.value["places"][0]
    assert first["rating"] == "4.6 (231)" and first["price"] == "$$" and first["today"] == "8 AM–6 PM"
    assert first["open_now"] is True and isinstance(first["about_minutes"], int)
    assert set(first["facts"]) >= {"rating", "hours", "status"}
    bar = next(p for p in res.value["places"] if p["name"] == "Gaming Bar")
    assert bar["tags"][0] == "bar-led"
    assert len(json.dumps(res.payload())) < 6000                                     # fits the executor's payload limit
    assert res.search_queries == 1 and len(res.evidence_ids) > 40
    assert "Run at most three Google Maps searches" in calls[0] and "within about a 50-minute drive" in calls[0]


def test_a_spread_across_towns_includes_home(ctx, monkeypatch):
    calls: list = []
    _wire(monkeypatch, {"Alpha City": [record("Home Cafe", "1 H St, Alpha City, CA 90001")],
                        "Beta Town": [record("South Cafe", "1 S St, Beta Town, CA 90002")],
                        "Gamma Park": [record("East Cafe", "1 E St, Gamma Park, CA 90003")]}, calls)
    _geocode(monkeypatch)
    res = _reg(ctx).execute("maps_search", {"query": "cafés", "areas": ["Beta Town", "Gamma Park"]}, ctx)
    assert len(calls) == 3 and any("near Alpha City" in c for c in calls)
    assert {p["name"] for p in res.value["places"]} == {"Home Cafe", "South Cafe", "East Cafe"}


def test_several_areas_side_by_side_and_never_the_same_area_twice(ctx, monkeypatch):
    calls: list = []
    _wire(monkeypatch, {"Alpha City": [record("North Cafe", "1 N St, Alpha City, CA 90001")],
                        "Beta Town": [record("South Cafe", "1 S St, Beta Town, CA 90002")],
                        "Gamma Park": [record("East Cafe", "1 E St, Gamma Park, CA 90003")]}, calls)
    _geocode(monkeypatch)
    reg = _reg(ctx)
    res = reg.execute("maps_search", {"query": "board game cafe coffee", "areas": ["Alpha City", "Beta Town"]}, ctx)
    assert res.ok and {p["name"] for p in res.value["places"]} == {"North Cafe", "South Cafe"}
    assert len(calls) == 2 and res.search_queries == 2                                # one search per area on the brake
    again = reg.execute("maps_search", {"query": "board games coffee cafe", "areas": ["Alpha City", "Beta Town", "Gamma Park"]}, ctx)
    assert len(calls) == 3 and "Gamma Park" in calls[-1]                              # only the new area was searched
    assert again.value["already_searched"] == ["Alpha City", "Beta Town"]
    assert {p["name"] for p in again.value["places"]} == {"North Cafe", "South Cafe", "East Cafe"}
    other = reg.execute("maps_search", {"query": "gelato", "near": "Beta Town"}, ctx)
    assert len(calls) == 4 and other.ok                                              # new words: searched again


def test_a_search_snippet_does_not_overwrite_what_maps_just_said(ctx, monkeypatch):
    _wire(monkeypatch, {"Alpha City": [record("Lantern House Games", "10 Elm Rd, Alpha City, CA 90001")]}, [])
    _geocode(monkeypatch)
    res = _reg(ctx).execute("maps_search", {"query": "board games", "near": "Alpha City"}, ctx)
    pid = res.value["places"][0]["place_id"]
    con = kb.connect(ctx["db_path"])
    cand = btools.Candidate(name="Lantern House Games", city="Alpha City", address="10 Elm Rd", hours_text="Daily 7 AM–10 PM",
                            rating_value=4.9, rating_source="SomeSite", rating_count=12, status="open")
    w = btools._write_candidate(con, cand, ["https://snippet.test/"], ctx)
    con.commit()
    assert w["view"]["place_id"] == pid                                               # the same café
    hours = kb.current_fact(con, "place", pid, "hours")
    assert hours["source_name"] == "Google Maps" and query.rating(con, pid)["source"] == "Google Maps"
    con.close()


def test_maps_search_is_the_first_stop_in_the_playbook():
    m = manifest.load("bourdain")
    assert "maps_search" in m.tools and m.tools.index("maps_search") < m.tools.index("web_search")
    from pathlib import Path
    playbook = Path(__file__).resolve().parents[1].joinpath("agents/bourdain/system_prompt.md").read_text(encoding="utf-8")
    assert "`maps_search` first" in playbook
