"""core.kb — schema, geo, the seed migration with zero unaccounted
fields, and the query functions (ADR-018 §2.6). Synthetic seed only:
no household data in the public repo."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import geo, migrate, query, schema  # noqa: E402

NOW = datetime(2026, 10, 3, 18, 0, 0)

SYNTHETIC_SEED = {
    "meta": {
        "for": "synthetic trip — two cities",
        "built": "2026-09-20",
        "updated": "2026-09-26",
        "by": "a test",
        "party_default": "member + member + infant",
        "stays": {
            "alpha_city": {"name": "Alpha Stay", "address": "140 W 24th St", "dates": "Mon Sep 21 – Thu Sep 24",
                           "minutes_are": "walking", "note": "stairs", "grid": {"x": 0.07, "st": 24}},
            "beta_city": {"name": "Beta Stay", "dates": "Thu Sep 24 – Sun Sep 27", "minutes_are": "driving"},
        },
        "rejected": [
            {"name": "Sweet Shop", "city": "alpha_city", "address": "1 Main St", "reason": "Site 3.9 (403) below the floor",
             "checked": "2026-09-22", "url": "https://example.test/sweet", "status": None,
             "rating_reported": {"source": "their site", "value": 3.9}},
            {"name": "Old Diner", "city": "beta_city", "status": "closed", "reason": "closed in June", "checked": "2026-09-24"},
        ],
        "landmarks": [
            {"name": "Small Island", "city": "alpha_city", "aliases": ["small island", "pier 55"],
             "grid": {"x": 0.95, "st": 13}, "note": "a park", "checked": "2026-09-22", "url": "https://example.test/island"},
        ],
        "ambiguous": [{"alias": "river", "means": ["River Park", "River Yards"], "rule": "ask once", "found": "2026-09-24"}],
        "overrides": [{"who": ["him", "her"], "diet": "vegetarian", "from": "2026-09-26 00:00", "until": "2026-09-26 23:59",
                       "source": "him, Sat"}],
        "closures": "closed_days are hard filters",
        "weather": {"beta_2026-09-25": {"source": "a forecast office", "issued": "2026-09-24 19:21", "hazards": []}},
        "praise": "praise[] = ranked lists",
        "routes": [{"name": "early walk", "city": "alpha_city", "checked": "2026-09-22", "legs": []}],
        "rules": "list='yours' means the household saved it",
        "grid": "x miles west of the avenue; st street number",
        "caveats": "hours are text",
    },
    "places": [
        {"name": "Casa Pasta", "list": "yours", "city": "alpha_city", "area": "Chelsea", "address": "235 W 23rd St",
         "meals": ["dinner", "italian", "pasta"], "hours_text": "Mon–Sun 11:30–22:00",
         "hours_source": {"value": "own site", "checked": "2026-09-20"}, "minutes_from_stay": 6,
         "him": "GF penne", "her": "margherita", "map_url": "https://maps.example.test/casa",
         "tags": ["gf_menu"], "note": "quiet", "transit": "walk", "grid": {"x": 0.25, "st": 23},
         "rating": "Google 4.6 (2,065); Tripadvisor 4.5 (310)", "yelp_url": "https://yelp.example.test/casa",
         "praise": [{"source": "Local Mag — best pasta", "rank": 2, "date": "2026-05-01", "url": "https://example.test/best-pasta"}],
         "dishes": [{"name": "GF Penne", "price": "$21", "note": "gluten-free pasta on request"},
                    {"name": "Lasagna", "price": "$19.50", "note": "beef"}],
         "menu_source": "own ordering page", "menu_checked": "2026-09-20", "locations": 1,
         "price_level": {"value": "$$", "source": "own ordering page", "checked": "2026-09-20"},
         "founded": {"value": 1994, "source": "own site", "checked": "2026-09-20"},
         "phone": {"value": "(555) 010-0000", "source": "own site", "checked": "2026-09-20"},
         "saved": {"by": "household", "date": "2026-09-25", "for": "Sun Sep 27", "words": "saved it for Sunday"},
         "added": "2026-09-20", "why": "Local Mag calls the penne the best in the city", "why_url": "https://example.test/best-pasta"},
        {"name": "Bagel Corner", "list": "mine", "city": "alpha_city", "area": "Chelsea", "address": "100 8th Ave",
         "meals": ["breakfast", "bagels"], "hours_text": "Daily 8–15", "minutes_from_stay": 9, "him": "GF bagel",
         "her": "egg sandwich", "map_url": "https://maps.example.test/bagel", "tags": [], "grid": {"x": 0.32, "st": 20},
         "rating": "UNVERIFIED — no rating checked as of 2026-09-22", "rating_unverified": True,
         "quick": True, "format": "counter", "opens": "08:00", "closed_days": ["Mon"],
         "seating": {"day": True, "evening": False, "source": "the location page, checked 2026-09-24"}},
        {"name": "Harbor Thai", "list": "mine", "city": "beta_city", "area": "Central", "address": "7 Pike Pl",
         "meals": ["lunch", "dinner", "thai"], "hours_text": "Tue–Sun 11–21", "minutes_from_stay": 25,
         "him": "chicken basil", "her": "tofu curry", "map_url": "https://maps.example.test/thai", "tags": ["small"],
         "rating": "Restaurantji 4.3 (186)", "locations": 2, "small_group_note": "two shops",
         "alt": {"backup": "Harbor Thai Express"}, "time_limit": {"minutes": 60, "when": "peak"},
         "ask": {"her": ["fish sauce in most dishes; ask to leave it out"]}, "payment": {"value": "cash only"}},
    ],
}


@pytest.fixture
def seed_path(tmp_path):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(SYNTHETIC_SEED), encoding="utf-8")
    return p


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "kb_test.db")


# ── geo ─────────────────────────────────────────────────────────────────
def test_haversine_known_distance():
    km = geo.haversine_km(40.7128, -74.0060, 42.3601, -71.0589)   # two east-coast cities
    assert 300 < km < 310


def test_grid_anchor_and_direction():
    lat, lng = geo.grid_to_latlng(0.0, 23.0)
    assert (lat, lng) == pytest.approx((40.7425, -73.9925), abs=1e-6)
    north_lat, _ = geo.grid_to_latlng(0.0, 43.0)       # twenty streets uptown ≈ a mile north-ish
    assert north_lat > lat
    _, west_lng = geo.grid_to_latlng(1.0, 23.0)         # a mile west
    assert west_lng < lng


def test_about_minutes_by_mode():
    a = (40.7425, -73.9925)
    b = geo.grid_to_latlng(0.0, 43.0)
    walk = geo.about_minutes(*a, *b, mode="walk")
    drive = geo.about_minutes(*a, *b, mode="drive")
    assert 15 <= walk <= 40 and drive < walk


# ── migration ───────────────────────────────────────────────────────────
def test_migration_accounts_for_every_field_and_block(seed_path, db):
    report = migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    assert report["unaccounted"] == []
    seen = {k for p in SYNTHETIC_SEED["places"] for k in p}
    assert set(report["field_map"]) == seen
    assert set(report["meta_map"]) == set(SYNTHETIC_SEED["meta"])
    assert report["places"] == 3 + 2 and report["legs"] == 2 and report["landmarks"] == 1
    assert report["ratings"] == 3            # two sources on the first place, one on the third
    assert report["menu_items"] == 2 and report["acclaim"] == 2
    assert report["saves"] == 2              # list=yours + the saved{} block
    assert report["verdicts"] == 3           # rejected ×2 + a reported score
    assert "GATE PASSED" not in migrate.render_report(report)   # the script adds that line, not the renderer
    assert "Unaccounted: none" in migrate.render_report(report)


def test_migration_projections(seed_path, db):
    migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    con = schema.connect(db)
    try:
        casa = query.find_place(con, "Casa Pasta")[0]
        assert casa["cuisine"] == "italian" and casa["cuisine_family"] == "italian"
        assert casa["price_level"] == "$$" and casa["founded"] == 1994 and casa["location_count"] == 1
        assert casa["lat"] is not None and casa["city"] == "alpha_city"
        r = query.rating(con, casa["place_id"])
        assert r["source"] in ("Google", "Tripadvisor") and r["count"] in (2065, 310)
        hours = query.fact(con, casa["place_id"], "hours")
        assert hours["value_text"] == "Mon–Sun 11:30–22:00" and hours["source_name"] == "own site"
        assert hours["observed_at"] == "2026-09-20" and hours["source_rank"] == "own_site"
        phone = query.fact(con, casa["place_id"], "phone")
        assert phone["value"]["value"] == "(555) 010-0000"
        bagel = query.find_place(con, "Bagel Corner")[0]
        assert query.rating(con, bagel["place_id"]) is None          # unverified never counts
        assert "quick" in bagel["format_tags"] and "counter" in bagel["format_tags"]
        assert "seating:day" in bagel["room_tags"]
        thai = query.find_place(con, "Harbor Thai")[0]
        ask = query.fact(con, thai["place_id"], "dish_note:ask")
        assert "spouse" in ask["value"] and "him" not in ask["value"]   # roles, not names
        old = query.find_place(con, "Old Diner")[0]
        assert old["status"] == "closed"
        assert query.landmark(con, "pier 55")["name"] == "Small Island"
        legs = query.legs(con)
        assert [lg["leg_id"] for lg in legs] == ["alpha_city", "beta_city"]
        assert legs[0]["start_date"] == "2026-09-21" and legs[0]["end_date"] == "2026-09-24"
        assert legs[0]["minutes_mode"] == "walk" and legs[1]["minutes_mode"] == "drive"
        assert query.leg_for(con, datetime(2026, 9, 25, 12)) ["leg_id"] == "beta_city"
        assert query.leg_for(con, datetime(2026, 10, 3, 12)) is None    # no home leg in this seed
        amb = con.execute("SELECT readings, rule FROM kb_alias WHERE kind = 'ambiguous'").fetchone()
        assert json.loads(amb[0]) == ["River Park", "River Yards"]
    finally:
        con.close()


def test_migration_is_idempotent_for_entities(seed_path, db):
    migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    con = schema.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM kb_place").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM kb_leg").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM kb_landmark").fetchone()[0] == 3   # 1 landmark + 2 stays
    finally:
        con.close()


def test_stats_and_render(seed_path, db):
    migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    con = schema.connect(db)
    try:
        s = schema.stats(con, now=NOW)
        assert s["places"] == 5 and s["places_by_city"] == {"alpha_city": 3, "beta_city": 2}
        assert s["with_coordinates"] == 2
        text = schema.render_stats(con, now=NOW)
        assert "Knowledge base: 5 places" in text and "Places with coordinates: 2 of 5" in text
    finally:
        con.close()


# ── facts and queries ───────────────────────────────────────────────────
def test_newest_fact_wins_and_freshness(db):
    con = schema.connect(db)
    try:
        schema.add_fact(con, subject_type="place", subject_id="p1", claim_type="status", value="open",
                        method="fetch", observed_at="2026-09-01 10:00:00", source_url="https://a.test")
        schema.add_fact(con, subject_type="place", subject_id="p1", claim_type="status", value="closed",
                        method="fetch", observed_at="2026-10-02 10:00:00", source_url="https://b.test")
        con.commit()
        cur = schema.current_fact(con, "place", "p1", "status")
        assert cur["value"] == "closed" and cur["source_url"] == "https://b.test"
        assert schema.is_fresh(cur, datetime(2026, 10, 2, 20), mode="trip")        # 10 h old, 24 h window
        assert not schema.is_fresh(cur, datetime(2026, 10, 4, 20), mode="trip")    # 58 h old
        assert schema.is_fresh(cur, datetime(2026, 10, 4, 20), mode="home")        # 7 d window at home
    finally:
        con.close()


def test_within_and_exclusions(seed_path, db):
    migrate.migrate_seed(seed_path, now=NOW, db_path=db)
    con = schema.connect(db)
    try:
        stay = geo.grid_to_latlng(0.07, 24)
        near = query.within(con, *stay, minutes=15, mode="walk", city="alpha_city")
        assert [p["name"] for p in near] == ["Casa Pasta", "Bagel Corner"]
        assert near[0]["about_minutes"] <= near[1]["about_minutes"]
        excl = query.excluded_ids(con, now=NOW)
        names = {query.place(con, pid)["name"]: why for pid, why in excl.items()}
        assert names["Old Diner"] in ("verdict:closed", "status")
        assert names["Sweet Shop"].startswith("verdict")
        assert query.saved_ids(con) == {query.find_place(con, "Casa Pasta")[0]["place_id"]}
    finally:
        con.close()


def test_place_identity_rules():
    a = schema.place_id_for("The Café Rosé", "alpha_city", address="1 Main St")
    b = schema.place_id_for("cafe rose", "alpha_city", address="1 Main Street")
    assert a != b                                   # address differs after normalization ("st" vs "street")
    c = schema.place_id_for("The Café Rosé", "alpha_city", address="1 Main St")
    assert a == c
    g1 = schema.place_id_for("Same Name", "x", lat=40.7425, lng=-73.9925)
    g2 = schema.place_id_for("Same Name", "x", lat=40.7426, lng=-73.9926)   # ~15 m apart
    assert g1 == g2
    assert schema.norm_name("The Broad Institute") == "broad institute"
