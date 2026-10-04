"""Grounding with Google Maps on the one wire, and geocoding many
addresses at once (core.llm_chat, core.geocode). Hermetic: a stubbed
wire, a fake SDK response, stubbed geocoders. Coordinates are arbitrary."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import geocode as cgeo, httpcache, llm_chat  # noqa: E402

CHUNK = {"url": "https://maps.google.com/maps?cid=1", "title": "Lantern House - Google Maps",
         "text": "**Title:** Lantern House\n\n**About:**\n\n* **Address:** 1 Main St, Alpha City, CA 90001\n",
         "place_id": "ChIJ-test-1"}


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_TEST", "0")
    return tmp_path / "cassettes"


# ── the wire ────────────────────────────────────────────────────────────
def test_the_config_asks_for_google_maps_around_the_point():
    cfg = llm_chat._build_config({"maps": {"lat": 10.12, "lng": 20.34}, "messages": []})
    assert {"google_maps": {}} in cfg["tools"]
    assert cfg["tool_config"] == {"retrieval_config": {"lat_lng": {"latitude": 10.12, "longitude": 20.34}}}
    bare = llm_chat._build_config({"maps": True, "messages": []})
    assert {"google_maps": {}} in bare["tools"] and "tool_config" not in bare
    assert "tools" not in llm_chat._build_config({"messages": []})


def test_the_point_leaves_rounded_and_other_calls_keep_their_cassette_keys(cdir, tmp_path, monkeypatch):
    seen = []

    def wire(req):
        seen.append(req)
        return {"text": "1. Lantern House", "parts": [{"text": "1. Lantern House"}], "stop_reason": "stop",
                "citations": [], "usage": {}, "search_queries": 2, "error": None, "maps_places": [CHUNK]}

    monkeypatch.setattr(llm_chat, "_call_wire", wire)
    db = str(tmp_path / "w.db")
    res = llm_chat.chat("test", "test.maps", messages=[llm_chat.user_text("cafés")], maps={"lat": 10.123456, "lng": 20.987654},
                        db_path=db)
    assert seen[0]["maps"] == {"lat": 10.12, "lng": 20.99}          # about a kilometre, never the exact point
    assert res.ok and res.maps_places == [CHUNK] and res.search_queries == 2
    llm_chat.chat("test", "test.plain", messages=[llm_chat.user_text("hi")], db_path=db)
    assert "maps" not in seen[1]                                    # only set when used: other keys unchanged
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: pytest.fail("a recorded Maps answer is replayed"))
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD")
    again = llm_chat.chat("test", "test.maps", messages=[llm_chat.user_text("cafés")], maps={"lat": 10.123456, "lng": 20.987654},
                          db_path=db)
    assert again.from_cassette and again.maps_places == [CHUNK]


class _Maps:
    def __init__(self, uri, title, text, place_id):
        self.uri, self.title, self.text, self.place_id = uri, title, text, place_id


class _Chunk:
    def __init__(self, web=None, maps=None):
        self.web, self.maps = web, maps


def test_maps_chunks_are_kept_as_places_and_citations():
    gm = type("GM", (), {"grounding_chunks": [_Chunk(maps=_Maps(CHUNK["url"], CHUNK["title"], CHUNK["text"], CHUNK["place_id"]))],
                         "grounding_supports": [], "web_search_queries": ["cafés near Alpha City", "cafés"]})()
    content = type("C", (), {"parts": [type("P", (), {"text": "1. Lantern House", "function_call": None,
                                                     "thought_signature": None, "thought": False})()]})()
    resp = type("R", (), {"candidates": [type("K", (), {"content": content, "finish_reason": "STOP",
                                                        "grounding_metadata": gm})()], "usage_metadata": None})()
    ser = llm_chat._serialize_response(resp)
    assert ser["maps_places"] == [CHUNK]
    assert ser["citations"] == [{"url": CHUNK["url"], "title": CHUNK["title"]}] and ser["search_queries"] == 2
    assert json.loads(json.dumps(ser)) == ser                        # a cassette stores it whole


# ── geocoding many addresses ────────────────────────────────────────────
def test_unit_numbers_come_off_before_geocoding():
    assert cgeo.street_address("10 Elm Rd #106, Alpha City, CA 90001") == "10 Elm Rd, Alpha City, CA 90001"
    assert cgeo.street_address("40 Oak Blvd Suite #8, Alpha City, CA 90001") == "40 Oak Blvd, Alpha City, CA 90001"
    assert cgeo.street_address("2 Pine Ave Ste a, Alpha City, CA 90001") == "2 Pine Ave, Alpha City, CA 90001"


def test_census_first_side_by_side_then_a_few_nominatim(monkeypatch):
    census, nominatim = [], []

    def fake_census(a, **kw):
        census.append(a)
        return None if "miss" in a else {"lat": 10.0, "lng": 20.0, "source_name": "census"}

    def fake_nominatim(q, **kw):
        nominatim.append(q)
        return {"lat": 11.0, "lng": 21.0, "source_name": "osm"}

    monkeypatch.setattr(cgeo, "geocode_census", fake_census)
    monkeypatch.setattr(cgeo, "geocode", fake_nominatim)
    addrs = ["1 A St, Alpha City, CA 90001", "2 miss Rd #4, Alpha City, CA 90001", "3 B St, Alpha City, CA 90001",
             "12 Rue Exemple, 75004 Paris, France", "9 miss Way, Alpha City, CA 90001", "1 A St, Alpha City, CA 90001"]
    out = cgeo.geocode_many(addrs, fallback_cap=2)
    assert sorted(census) == sorted(set(a for a in addrs if "Paris" not in a))     # each US address once
    assert out["1 A St, Alpha City, CA 90001"]["source_name"] == "census"
    assert nominatim == ["2 miss Rd, Alpha City, CA 90001", "12 Rue Exemple, 75004 Paris, France"]   # at most two
    assert out["9 miss Way, Alpha City, CA 90001"] is None


def test_the_census_answer_is_read(monkeypatch):
    body = {"result": {"addressMatches": [{"coordinates": {"x": 20.5, "y": 10.5}, "matchedAddress": "1 A ST"}]}}

    class F:
        ok, status, body_bytes = True, 200, b""

    def fake_get(url, **kw):
        assert "geocoding.geo.census.gov" in url and "address=1+A+St" in url
        f = F()
        f.body = json.dumps(body).encode()
        return f

    monkeypatch.setattr(httpcache, "get", fake_get)
    g = cgeo.geocode_census("1 A St #2, Alpha City, CA 90001")
    assert (g["lat"], g["lng"]) == (10.5, 20.5) and g["source_name"] == "U.S. Census Bureau Geocoder"
    assert cgeo.geocode_census("12 Rue Exemple, 75004 Paris, France") is None      # not a US address: not asked
