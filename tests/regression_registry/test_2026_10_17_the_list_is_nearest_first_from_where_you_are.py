"""Regression pin — 2026-10-17: `/list` is the household's saves from the
knowledge base, nearest first from where they are.

LIVE 2026-10-17 12:26, the household at home: `/list` answered from the
trip-era tables — "Your list · everywhere · nearest first" and fifteen
places in the previous month's city, "~6 min walk" from a hotel the
household had left three weeks before. The saves made at home (in the
knowledge base, where the harness writes them) were not on it.

Now `/list` reads `kb_save` and ranks from the harness's origin (a
location the household just set, the trip's stay, else home): the
places within reach, nearest first with "about" minutes; the rest in
one line by city, with `/list <city>` to see them. A trip city's list
is shown as saved, without minutes from home.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import schema as kb  # noqa: E402
from agents.bourdain import handler, harness as bh, harness_bridge, saved  # noqa: E402
from tests.test_verify_render import PROFILE  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 26)        # a Saturday at home
HOME = (37.0, -122.0)                       # synthetic
PLACES = [
    # name, city key, lat, lng, hours
    ("Near Bakery", "alpha_city", 37.05, -122.0, "Mon–Sun 7:00–16:00"),
    ("Next Town Cafe", "beta_town", 37.4, -122.1, "Sat: Closed; Sun: 8 AM–3 PM"),
    ("Far Deli", "gamma_city", 40.70, -74.00, None),
    ("Far Bagels", "gamma_city", 40.75, -73.99, None),
    ("Far Chowder", "delta_city", 42.36, -71.06, None),
]


@pytest.fixture
def saves_db(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    from core import io as cio
    db = tmp_path / "saves.db"
    monkeypatch.setattr(cio, "DB_PATH", db)
    con = kb.connect(str(db))
    now_s = NOW.strftime("%Y-%m-%d %H:%M:%S")
    ids = {}
    for i, (name, city, lat, lng, hours) in enumerate(PLACES):
        loc = kb.location_id_for(city, None, lat, lng)
        con.execute("INSERT OR IGNORE INTO kb_location (location_id, lat, lng, city, created_at) VALUES (?,?,?,?,?)",
                    (loc, lat, lng, city, now_s))
        pid = kb.place_id_for(name, city, lat=lat, lng=lng)
        ids[name] = pid
        con.execute("INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, status, format_tags, room_tags, "
                    "source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, name, kb.norm_name(name), loc, "bakery", "open", "[]", "[]", "yours", now_s, now_s))
        con.execute("INSERT INTO kb_save (place_id, member_id, saved_at, heard_from) VALUES (?,?,?,?)",
                    (pid, "primary", f"2026-09-2{i} 10:00:00", "a friend"))
        if hours:
            kb.add_fact(con, subject_type="place", subject_id=pid, claim_type="hours", value={"value": hours},
                        source_url="https://example.test/", source_name="own site", source_rank="own_site",
                        method="fetch", observed_at=now_s, now=NOW)
    kb.add_fact(con, subject_type="place", subject_id=ids["Near Bakery"], claim_type="map_url",
                value={"value": "https://maps.example.test/near"}, source_url="https://example.test/",
                source_name="own site", source_rank="own_site", method="fetch", observed_at=now_s, now=NOW)
    con.commit()
    con.close()
    prof = dict(PROFILE)
    prof["home"] = {"city": "Alpha City", "tz": "America/Los_Angeles", "radius_min": 25, "minutes_mode": "drive",
                    "lat": HOME[0], "lng": HOME[1]}
    tenant = bh.BourdainTenant(profile=prof)
    monkeypatch.setattr(bh, "tenant", lambda: tenant)
    monkeypatch.setattr(bh.BourdainTenant, "speaker_for", lambda self, chat_id: "primary")
    return str(db)


def test_at_home_the_saves_within_reach_come_first_and_the_trip_cities_are_one_line(saves_db):
    out = handler.handle_saved("111", "", now=NOW, db_path=saves_db)
    lines = out.splitlines()
    assert lines[0] == "*Your list · near home · nearest first*"
    assert lines[2].startswith("★ Near Bakery · Alpha City · ~15 min drive") and "open till 4 PM" in lines[2]
    assert "[map](https://maps.example.test/near)" in lines[2]
    assert lines[3].startswith("★ Next Town Cafe · Beta Town · ~47 min drive") and "closed today" in lines[3]
    assert "Far Deli" not in out and "Far Bagels" not in out and "Far Chowder" not in out
    assert lines[-1] == "Elsewhere: Gamma City 2 · Delta City 1 — `/list gamma city` shows them."


def test_a_trip_city_is_listed_on_request_without_minutes_from_home(saves_db):
    out = handler.handle_saved("111", "gamma city", now=NOW, db_path=saves_db)
    assert out.splitlines()[0] == "*Your list · Gamma City · newest first*"
    assert "Far Deli" in out and "Far Bagels" in out and "min drive" not in out
    assert "Near Bakery" not in out and "Elsewhere" not in out
    assert "Nothing saved in Omega City yet" in handler.handle_saved("111", "omega city", now=NOW, db_path=saves_db)


def test_a_location_the_household_set_moves_the_ranking(saves_db):
    from core import state_scopes as ss
    ss.put(bh.AGENT, "111", "location", {"label": "Beta Town", "lat": 37.4, "lng": -122.1, "city": "beta_town"},
           scope="session", now=NOW, replace=True, expires_in_hours=3, db_path=saves_db)
    out = handler.handle_saved("111", "", now=NOW, db_path=saves_db)
    lines = out.splitlines()
    assert lines[0] == "*Your list · near Beta Town · nearest first*"
    assert lines[2].startswith("★ Next Town Cafe") and lines[3].startswith("★ Near Bakery")


def test_in_serve_mode_slash_list_is_this_list_not_the_trips(saves_db, monkeypatch):
    monkeypatch.setenv("RAHAT_BOURDAIN_HARNESS", "serve")
    monkeypatch.setattr(harness_bridge, "answer", lambda *a, **k: pytest.fail("/list reached the harness"))
    out = handler.route("/list", chat_id="111", now=NOW)
    assert out.startswith("*Your list · near home · nearest first*") and "Near Bakery" in out
    assert "Far Deli" not in out
    out2 = handler.route("my list", chat_id="111", now=NOW)
    assert out2 == out
    assert "Far Chowder" in handler.route("/list delta city", chat_id="111", now=NOW)


def test_the_city_words_of_a_list_request():
    assert saved.city_filter("") is None and saved.city_filter("  ") is None
    assert saved.city_filter("gamma city") == "gamma_city"
    assert saved.city_filter("New York") == "new_york" and saved.city_filter("nyc") == "new_york"
    assert saved.city_filter("boston") == "boston"
