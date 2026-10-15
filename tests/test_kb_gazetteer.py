"""The gazetteer (2026-10-15): every eating place that exists in an area,
from open data, under the knowledge base. Rows come from OpenStreetMap
(Overpass) or Foursquare OS Places; a row is promoted into kb_place only
when an ask or a mention touches it, as an unvetted place."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import gazetteer as gz, migrate, query, schema as kb  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0)
ORIGIN = (37.264, -122.023)       # a point in the synthetic "Alpha City"


def osm_el(id_, name, lat, lng, **tags):
    t = {"name": name}
    t.update(tags)
    return {"type": "node", "id": id_, "lat": lat, "lon": lng, "tags": t}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "gaz.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=path)
    con = kb.connect(path)
    rows = [gz.osm_row(osm_el(1, "Bai Tong", 37.265, -122.024, amenity="restaurant", cuisine="thai",
                              website="https://baitong.test", phone="+1 555 0100", **{"diet:vegetarian": "yes"})),
            gz.osm_row(osm_el(2, "Big Basin Cafe", 37.266, -122.025, amenity="cafe", opening_hours="Mo-Su 07:00-17:00",
                              **{"addr:city": "Alpha City"})),
            gz.osm_row(osm_el(3, "Burger Barn", 37.30, -122.10, amenity="fast_food", cuisine="burger", brand="Burger Barn")),
            gz.osm_row(osm_el(4, "Far Pho", 38.9, -121.3, amenity="restaurant", cuisine="vietnamese")),
            gz.fsq_row({"fsq_place_id": "abc", "name": "Casa Pasta", "latitude": 37.2641, "longitude": -122.0231,
                        "address": "1 Main St", "locality": "Alpha City", "region": "CA", "postcode": "95000",
                        "tel": "+1 555 0101", "website": "https://casapasta.test",
                        "fsq_category_labels": ["Dining and Drinking > Restaurant > Italian Restaurant"],
                        "date_refreshed": "2026-09-01", "date_closed": None}),
            gz.fsq_row({"fsq_place_id": "shut", "name": "Gone Grill", "latitude": 37.2645, "longitude": -122.0235,
                        "fsq_category_labels": ["Dining and Drinking > Restaurant"], "date_closed": "2025-02-01"}),
            gz.fsq_row({"fsq_place_id": "notfood", "name": "Tyre Shop", "latitude": 37.2645, "longitude": -122.0235,
                        "fsq_category_labels": ["Retail > Automotive"]})]
    n = gz.upsert(con, rows, release="test", now=NOW)
    con.commit()
    assert n == 6, "the tyre shop is not an eating place"
    return path


def test_rows_read_the_sources_fields():
    r = gz.osm_row(osm_el(9, "Bai Tong", 37.1, -122.1, amenity="restaurant", cuisine="thai;asian",
                          **{"addr:housenumber": "10", "addr:street": "Big Basin Way", "addr:city": "Alpha City",
                             "addr:postcode": "95070", "contact:website": "https://bt.test", "diet:gluten_free": "yes",
                             "irrelevant": "x"}))
    assert r["gaz_id"] == "osm:node/9" and r["categories"] == "restaurant" and r["cuisine"] == "thai;asian"
    assert r["address"] == "10 Big Basin Way, Alpha City, 95070" and r["website"] == "https://bt.test"
    assert r["tags"] == {"diet:gluten_free": "yes"}
    assert gz.cuisine_family("thai;asian") == "thai" and gz.cuisine_family("burger") is None
    assert gz.osm_row({"type": "node", "id": 10, "lat": 0, "lon": 0, "tags": {"amenity": "restaurant", "name": ""}}) is None
    assert gz.osm_row(osm_el(11, "A Bank", 0, 0, amenity="bank")) is None
    f = gz.fsq_row({"fsq_place_id": "x", "name": "Pho 99", "latitude": 1, "longitude": 2,
                    "fsq_category_labels": '["Dining and Drinking > Restaurant > Vietnamese Restaurant"]'})
    assert f["cuisine"] == "vietnamese" and f["categories"] == "Vietnamese Restaurant" and f["gaz_id"] == "fsq:x"


def test_find_by_name_near_the_origin_and_nearby_by_family(db):
    con = kb.connect(db)
    hits = gz.find(con, "bai tong", near=ORIGIN, km=10)
    assert [h["name"] for h in hits] == ["Bai Tong"] and hits[0]["km"] < 1
    assert gz.find(con, "Gone Grill") == [], "a closed row never resolves"
    near = gz.nearby(con, ORIGIN[0], ORIGIN[1], km=5)
    assert [n["name"] for n in near] == ["Casa Pasta", "Bai Tong", "Big Basin Cafe"]
    assert [n["name"] for n in gz.nearby(con, ORIGIN[0], ORIGIN[1], km=5, family="thai")] == ["Bai Tong"]
    assert [n["name"] for n in gz.nearby(con, ORIGIN[0], ORIGIN[1], km=5, categories=["cafe"])] == ["Big Basin Cafe"]
    assert gz.nearby(con, ORIGIN[0], ORIGIN[1], km=5, family="vietnamese") == []


def test_promote_makes_an_unvetted_place_with_open_data_facts(db):
    con = kb.connect(db)
    g = gz.find(con, "Bai Tong")[0]
    w = gz.promote(con, g, now=NOW, trace_id="t1")
    con.commit()
    assert w["promoted"] and {"geo", "website", "phone", "note"} <= set(w["facts"])
    p = query.place(con, w["place_id"])
    assert p["status"] == "unknown" and p["source_list"] == "gazetteer" and p["cuisine_family"] == "thai"
    assert p["lat"] == pytest.approx(37.265) and p["city"] == ""   # OSM gave no city; the loader fills one
    f = kb.current_fact(con, "place", w["place_id"], "website")
    assert f["source_name"] == "OpenStreetMap" and f["value"]["value"] == "https://baitong.test"
    assert kb.current_fact(con, "place", w["place_id"], "hours") is None
    again = gz.promote(con, gz.find(con, "Bai Tong")[0], now=NOW)
    assert again["promoted"] is False and again["place_id"] == w["place_id"]
    assert con.execute("SELECT place_id FROM kb_gazetteer WHERE gaz_id = 'osm:node/1'").fetchone()[0] == w["place_id"]


def test_promote_recognises_a_place_already_on_file_at_that_spot(db):
    con = kb.connect(db)
    casa = query.find_place(con, "Casa Pasta")[0]
    con.execute("UPDATE kb_location SET lat = 37.2641, lng = -122.0231 WHERE location_id = ?", (casa["location_id"],))
    g = gz.find(con, "Casa Pasta")[0]
    w = gz.promote(con, g, now=NOW)
    assert w["promoted"] is False and w["place_id"] == casa["place_id"]


def test_osm_hours_come_along_as_an_unverified_fact_and_a_chain_is_tagged(db):
    con = kb.connect(db)
    w = gz.promote(con, gz.find(con, "Big Basin Cafe")[0], now=NOW)
    h = kb.current_fact(con, "place", w["place_id"], "hours")
    assert h and h["value"]["value"].startswith("Monday: 7:00 AM–5:00 PM") and "unverified" in h["value"]["note"]
    assert con.execute("SELECT COUNT(*) FROM kb_hours WHERE place_id = ?", (w["place_id"],)).fetchone()[0] == 7
    assert query.place(con, w["place_id"])["format_tags"] == ["cafe"]
    b = gz.promote(con, gz.find(con, "Burger Barn")[0], now=NOW)
    assert "chain" in query.place(con, b["place_id"])["format_tags"]


def test_stats_count_the_table(db):
    con = kb.connect(db)
    s = gz.stats(con)
    assert s["rows"] == 6 and s["by_source"] == {"osm": 4, "foursquare": 2} and s["with_website"] == 2


# ── the loader script ───────────────────────────────────────────────────
def test_the_loader_tiles_the_box_and_fills_a_city(monkeypatch):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("food_gazetteer", pathlib.Path("scripts/food_gazetteer.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t = mod.tiles((36.9, -123.0, 38.9, -121.0), 2)
    assert len(t) == 4 and t[0] == (36.9, -123.0, 37.9, -122.0)
    q = mod.overpass_query((37.0, -122.0, 37.5, -121.5))
    assert "amenity" in q and "(37.0,-122.0,37.5,-121.5)" in q and "out center tags" in q
    assert mod.nearest_city(37.2638, -122.0230) == "Saratoga"
    assert mod.nearest_city(40.0, -120.0) is None
    row = mod.fill_city({"name": "x", "city": None, "lat": 37.3861, "lng": -122.0839})
    assert row["city"] == "Mountain View"

    class R:
        status_code = 200

        def json(self):
            return {"elements": [osm_el(5, "Tile Cafe", 37.2, -122.0, amenity="cafe")]}
    rows = mod.fetch_osm((37.0, -122.5, 37.5, -121.5), n_tiles=1, pause_s=0, post=lambda data: R(), log=lambda *a: None)
    assert [r["name"] for r in rows] == ["Tile Cafe"] and rows[0]["city"] == "Los Gatos"
    sql = mod.fsq_sql("dt=2026-09-15", (36.9, -123.0, 38.9, -121.0), limit=10)
    assert "dt=2026-09-15" in sql and "Dining and Drinking" in sql and "LIMIT 10" in sql


@pytest.mark.parametrize("spec, want", [
    ("Mo-Fr 11:00-21:00; Sa,Su 12:00-22:00", "Monday: 11:00 AM–9:00 PM; Tuesday: 11:00 AM–9:00 PM; Wednesday: 11:00 AM–9:00 PM; "
                                            "Thursday: 11:00 AM–9:00 PM; Friday: 11:00 AM–9:00 PM; Saturday: 12:00 PM–10:00 PM; "
                                            "Sunday: 12:00 PM–10:00 PM"),
    ("Mo-Su 07:00-17:00", "Monday: 7:00 AM–5:00 PM; Tuesday: 7:00 AM–5:00 PM; Wednesday: 7:00 AM–5:00 PM; Thursday: 7:00 AM–5:00 PM; "
                          "Friday: 7:00 AM–5:00 PM; Saturday: 7:00 AM–5:00 PM; Sunday: 7:00 AM–5:00 PM"),
    ("Tu-Su 11:30-14:30,17:00-21:30; Mo off", "Monday: Closed; Tuesday: 11:30 AM–2:30 PM, 5:00 PM–9:30 PM; Wednesday: 11:30 AM–2:30 PM, "
                                              "5:00 PM–9:30 PM; Thursday: 11:30 AM–2:30 PM, 5:00 PM–9:30 PM; Friday: 11:30 AM–2:30 PM, "
                                              "5:00 PM–9:30 PM; Saturday: 11:30 AM–2:30 PM, 5:00 PM–9:30 PM; Sunday: 11:30 AM–2:30 PM, 5:00 PM–9:30 PM"),
    ("24/7", "Monday: Open 24 hours; Tuesday: Open 24 hours; Wednesday: Open 24 hours; Thursday: Open 24 hours; Friday: Open 24 hours; "
             "Saturday: Open 24 hours; Sunday: Open 24 hours"),
    ("Mo-Fr 09:00-17:00; PH off", "Monday: 9:00 AM–5:00 PM; Tuesday: 9:00 AM–5:00 PM; Wednesday: 9:00 AM–5:00 PM; Thursday: 9:00 AM–5:00 PM; "
                                  "Friday: 9:00 AM–5:00 PM"),
    ("Mo-Su sunrise-sunset", None), ("Jan-Mar Mo-Fr 10:00-16:00", None), ("", None), (None, None),
])
def test_osm_opening_hours_become_the_text_the_hours_parser_reads(spec, want):
    assert gz.osm_hours_to_text(spec) == want
    if want:
        from core import hours as chours
        assert chours.parse(want).intervals
