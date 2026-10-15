"""agents.bourdain.tools — the executor's tools over the knowledge base
and the wire, hermetic (synthetic seed, stubbed model)."""
from __future__ import annotations

import io
import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import llm_chat, manifest, sources  # noqa: E402
from core import state_scopes as ss  # noqa: E402
from core.kb import geo, migrate, query, schema as kb  # noqa: E402
from agents.bourdain import profile, tools as btools  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402

NOW = datetime(2026, 9, 22, 18, 40)   # inside the synthetic alpha_city leg


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db = str(tmp_path / "tools.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=db)
    monkeypatch.setenv("RAHAT_BOURDAIN_PROFILE_JSON", str(tmp_path / "missing.json"))   # the PII-free default
    monkeypatch.setenv("RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN", "0")
    monkeypatch.setenv("RAHAT_CASSETTE_DIR", str(tmp_path / "cassettes"))
    monkeypatch.delenv("RAHAT_FIXTURE_RECORD", raising=False)
    stay = geo.grid_to_latlng(0.07, 24)
    return {"now": NOW, "chat_id": "c1", "db_path": db, "trace_id": "tr-tools",
            "leg": {"leg_id": "alpha_city", "kind": "trip", "city": "alpha_city", "tz": "America/New_York",
                    "minutes_mode": "walk", "stay_lat": stay[0], "stay_lng": stay[1]},
            "origin_latlng": stay, "origin_label": "the stay", "diners": ["primary", "spouse", "infant"]}


def _reg(ctx):
    m = manifest.load("bourdain")
    return btools.build_registry(ctx, allow=m.tools)


def test_manifest_tools_all_exist(ctx):
    reg = btools.build_registry(ctx)
    missing = [t for t in manifest.load("bourdain").tools if reg.get(t) is None]
    assert missing == [], missing
    assert len(reg.declarations()) == len(manifest.load("bourdain").tools) or reg.allow is None


def test_profile_default_is_pii_free_and_reads(ctx):
    reg = _reg(ctx)
    res = reg.execute("get_profile", {}, ctx)
    assert res.ok and set(res.value["members"]) == {"primary", "spouse", "infant"}
    assert res.value["members"]["primary"]["rules"] == "unknown"
    assert profile.diners_default(ctx["profile"]) == ["primary", "spouse", "infant"]


def test_kb_search_saved_first_within_budget_excluding_rejected(ctx):
    reg = _reg(ctx)
    res = reg.execute("kb_search_places", {"within_minutes": 15, "mode": "walk"}, ctx)
    assert res.ok
    names = [p["name"] for p in res.value["places"]]
    assert names[0] == "Casa Pasta" and names[0:2] == ["Casa Pasta", "Bagel Corner"]
    assert res.value["places"][0]["saved"] and "about_minutes" in res.value["places"][0]
    assert "Sweet Shop" not in names and "Old Diner" not in names
    assert res.value["places"][0]["facts"]["hours"]["value"] == "Mon–Sun 11:30–22:00"
    fam = reg.execute("kb_search_places", {"family": "thai", "city": "beta_city"}, ctx)
    assert [p["name"] for p in fam.value["places"]] == ["Harbor Thai"]


def test_resolve_name_uses_last_answer_landmarks_and_aliases(ctx):
    reg = _reg(ctx)
    ss.put("bourdain", "c1", "last_answer", {"picks": [{"place_id": "p-danbi", "name": "Danbi"}]}, scope="session",
           now=NOW, replace=True, db_path=ctx["db_path"])
    r = reg.execute("resolve_name", {"text": "Dan B"}, ctx)
    assert r.ok and r.value["resolved"]["name"] == "Danbi"
    lm = reg.execute("resolve_name", {"text": "pier 55"}, ctx)
    assert lm.value["resolved"]["kind"] == "landmark" and lm.value["resolved"]["name"] == "Small Island"
    amb = reg.execute("resolve_name", {"text": "river"}, ctx)
    assert any(c["kind"] == "ambiguous" for c in amb.value["candidates"])
    kbhit = reg.execute("resolve_name", {"text": "casa pasta"}, ctx)
    assert kbhit.value["resolved"]["why"] == "knowledge base"


def test_state_writes_land_in_scoped_state_and_kb(ctx):
    reg = _reg(ctx)
    con = kb.connect(ctx["db_path"])
    pid = query.find_place(con, "Harbor Thai")[0]["place_id"]
    con.close()
    assert reg.execute("save_place", {"place_id": pid, "heard_from": "a friend", "for_date": "2026-09-27"}, ctx).ok
    assert reg.execute("record_verdict", {"kind": "chain", "place_id": pid, "text": "that's a chain"}, ctx).ok
    assert reg.execute("set_override", {"members": ["spouse"], "rule": "no egg"}, ctx).ok
    assert reg.execute("set_override", {"members": ["spouse"], "rule": "no chicken"}, ctx).ok
    ov = reg.execute("get_overrides", {}, ctx).value["overrides"]
    assert [o["rule"] for o in ov] == ["no egg", "no chicken"] and ov[0]["scope"] == "day"
    assert reg.execute("set_override", {"members": ["spouse"], "rule": "no egg", "withdraw": True}, ctx).value["withdrawn"] == 1
    assert [o["rule"] for o in reg.execute("get_overrides", {}, ctx).value["overrides"]] == ["no chicken"]
    loc = reg.execute("set_location", {"label": "Small Island"}, ctx)
    assert loc.ok and loc.value["resolved"] and ctx["origin_label"] == "Small Island"
    plan = reg.execute("edit_plan", {"stops": ["museum", "market"]}, ctx)
    assert plan.ok and reg.execute("get_leg_context", {}, ctx).value["plan_today"]["stops"] == ["museum", "market"]
    kinds = [w["kind"] for w in ctx["state_writes"]]
    assert kinds == ["save", "verdict", "override", "override", "override_withdrawn", "location", "plan_edit"]
    con = kb.connect(ctx["db_path"])
    assert con.execute("SELECT COUNT(*) FROM kb_save WHERE place_id = ?", (pid,)).fetchone()[0] == 1
    assert query.excluded_ids(con, now=NOW)[pid] == "verdict:chain"
    con.close()


def test_route_time_and_yelp_link_and_clock(ctx):
    reg = _reg(ctx)
    r = reg.execute("route_time", {"origin": "stay", "destination": "Casa Pasta", "mode": "walk"}, ctx)
    assert r.ok and r.value["label"] == "about" and 1 <= r.value["about_minutes"] <= 15
    assert "yelp.com/search" in reg.execute("yelp_link", {"name": "Casa Pasta", "city": "alpha"}, ctx).value["url"]
    assert reg.execute("clock", {}, ctx).value["weekday"] == "Tuesday"
    bad = reg.execute("route_time", {"origin": "nowhere", "destination": "Casa Pasta"}, ctx)
    assert not bad.ok and "no coordinates" in bad.error


def test_web_search_writes_live_places_with_citations(ctx, monkeypatch):
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    answer = json.dumps({"candidates": [
        {"name": "Yoma Burmese", "address": "5 Allston St", "city": "alpha_city", "cuisine": "burmese",
         "rating_source": "Restaurantji", "rating_value": 4.7, "rating_count": 256, "hours_text": "Tue–Sun 12–21",
         "status": "open", "price_level": "$$", "url": "https://yoma.test/", "is_bar_led": False, "location_count": 1},
        {"name": "Mystery Spot", "cuisine": "thai", "rating_value": 4.9}]})
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: {
        "text": answer, "parts": [{"text": answer}], "stop_reason": "stop",
        "citations": [{"url": "https://yoma.test/", "title": "Yoma"}], "search_queries": 3,
        "usage": {"in": 500, "out": 200, "thought": 0, "tool_prompt": 2000}, "error": None})
    reg = _reg(ctx)
    res = reg.execute("web_search", {"query": "Burmese", "city": "alpha city", "want": 3}, ctx)
    assert res.ok and res.search_queries == 3 and res.cost_usd > 0
    cands = res.value["candidates"]
    assert cands[0]["evidence_rank"] == "search_citation" and cands[0]["fact_ids"]
    assert cands[1]["evidence_rank"] == "search_citation"           # a citation URL stood in for the missing page
    con = kb.connect(ctx["db_path"])
    yoma = query.find_place(con, "Yoma Burmese")[0]
    assert yoma["source_list"] == "live" and yoma["cuisine_family"] == "burmese"
    assert query.rating(con, yoma["place_id"])["count"] == 256
    assert query.fact(con, yoma["place_id"], "hours")["source_url"] == "https://yoma.test/"
    con.close()
    # the registry's trajectory carries the query count for the brake
    assert reg.calls[-1]["search_queries"] == 3


class _Resp(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status, self.url, self.headers = 200, "https://casa.test/hours", {"Content-Type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_read_page_facts_records_hours_rating_and_menu(ctx, monkeypatch):
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    monkeypatch.setattr(sources, "policy_for", lambda url, fetch_robots=None: {"policy": "allowed", "why": "t", "trust_tier": ""})
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: _Resp(b"<html><title>Casa</title><body>Mon-Sun 11:30-22:00. Google 4.6 (2,065). GF penne $21</body></html>"))
    extracted = json.dumps({"hours_text": "Mon–Sun 11:30–22:00", "status": "open", "rating_source": "Google",
                            "rating_value": 4.6, "rating_count": 2065, "price_level": "$$",
                            "menu_items": ["GF penne — $21 — gluten-free pasta"], "room_notes": "quiet", "phone": None,
                            "location_count": 1, "acclaim": []})
    monkeypatch.setattr(llm_chat, "_call_wire", lambda req: {
        "text": extracted, "parts": [{"text": extracted}], "stop_reason": "stop", "citations": [], "search_queries": 0,
        "usage": {"in": 900, "out": 100, "thought": 0, "tool_prompt": 0}, "error": None})
    reg = _reg(ctx)
    con = kb.connect(ctx["db_path"])
    pid = query.find_place(con, "Casa Pasta")[0]["place_id"]
    con.close()
    res = reg.execute("read_page_facts", {"url": "https://casa.test/hours", "place_id": pid}, ctx)
    assert res.ok and len(res.evidence_ids) >= 5
    con = kb.connect(ctx["db_path"])
    hrs = query.fact(con, pid, "hours")
    assert hrs["method"] == "fetch" and hrs["source_url"] == "https://casa.test/hours"
    assert con.execute("SELECT COUNT(*) FROM kb_hours WHERE place_id = ?", (pid,)).fetchone()[0] == 7
    assert con.execute("SELECT name FROM kb_menu_item WHERE place_id = ? ORDER BY item_id DESC LIMIT 1", (pid,)).fetchone()[0] == "GF penne"
    con.close()


def test_blocked_host_is_refused_before_any_request(ctx, monkeypatch):
    monkeypatch.setenv("RAHAT_FIXTURE_RECORD", "1")
    reg = _reg(ctx)
    res = reg.execute("read_page_facts", {"url": "https://www.yelp.com/biz/casa"}, ctx)
    assert not res.ok and "link_only" in res.error and res.denied_by == "_deny_blocked_hosts"


# ── the gazetteer under the tools (2026-10-15) ──────────────────────────
def _gazetteer_rows(ctx, rows):
    from core.kb import gazetteer as gz
    con = kb.connect(ctx["db_path"])
    gz.upsert(con, rows, release="test", now=NOW)
    con.commit()
    con.close()


def test_a_thin_search_is_filled_from_the_gazetteer_as_unvetted(ctx):
    from core.kb import gazetteer as gz
    lat, lng = ctx["origin_latlng"]
    _gazetteer_rows(ctx, [
        gz.osm_row({"type": "node", "id": 1, "lat": lat + 0.003, "lon": lng, "tags": {"name": "Bai Tong", "amenity": "restaurant",
                    "cuisine": "thai", "website": "https://baitong.test"}}),
        gz.osm_row({"type": "node", "id": 2, "lat": lat + 0.004, "lon": lng, "tags": {"name": "Lao Kitchen", "amenity": "restaurant",
                    "cuisine": "lao"}}),
        gz.osm_row({"type": "node", "id": 3, "lat": lat + 0.9, "lon": lng, "tags": {"name": "Far Thai", "amenity": "restaurant",
                    "cuisine": "thai"}}),
    ])
    reg = _reg(ctx)
    res = reg.execute("kb_search_places", {"family": "thai", "within_minutes": 15, "mode": "walk"}, ctx)
    assert res.ok and res.value["places"] == []                      # nothing vetted in walking range
    names = [p["name"] for p in res.value["unvetted"]]
    assert names == ["Bai Tong", "Lao Kitchen"], names                # lao counts as thai; Far Thai is far
    first = res.value["unvetted"][0]
    assert first["unvetted"] is True and first["source"] == "OpenStreetMap" and first["status"] == "unknown"
    assert first["facts"]["website"]["value"] == "https://baitong.test"
    assert "hours" not in first["facts"] and "no hours or rating" in res.value["note"]
    con = kb.connect(ctx["db_path"])
    p = query.place(con, first["place_id"])
    assert p["source_list"] == "gazetteer" and p["status"] == "unknown"
    # a vetted search is not padded
    full = reg.execute("kb_search_places", {"within_minutes": 15, "mode": "walk"}, ctx)
    assert "unvetted" not in full.value or full.value["places"]


def test_a_name_the_knowledge_base_lacks_resolves_from_the_gazetteer(ctx):
    from core.kb import gazetteer as gz
    lat, lng = ctx["origin_latlng"]
    _gazetteer_rows(ctx, [gz.osm_row({"type": "node", "id": 7, "lat": lat + 0.01, "lon": lng,
                                      "tags": {"name": "Pho Bowl", "amenity": "restaurant", "cuisine": "vietnamese"}})])
    reg = _reg(ctx)
    r = reg.execute("resolve_name", {"text": "pho bowl"}, ctx)
    cand = r.value["candidates"][0]
    assert cand["name"] == "Pho Bowl" and cand["unvetted"] is True and "gazetteer" in cand["why"]
    assert cand["confidence"] == 0.7 and r.value["resolved"]["name"] == "Pho Bowl"   # the only reading
    con = kb.connect(ctx["db_path"])
    assert query.find_place(con, "Pho Bowl")[0]["source_list"] == "gazetteer"
    # the knowledge base still wins when it knows the name
    assert reg.execute("resolve_name", {"text": "casa pasta"}, ctx).value["resolved"]["why"] == "knowledge base"
