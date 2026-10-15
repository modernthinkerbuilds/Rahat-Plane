"""bridges/food (2026-10-15): the feeds read in code, the places they name,
the mentions in the knowledge base, a place's own page for its hours,
the nightly job, and what the daily line says about it."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core.kb import gazetteer as gz, migrate, query, schema as kb  # noqa: E402
from bridges.food import nightly, ownsite, readers, store  # noqa: E402
from tests.test_kb import SYNTHETIC_SEED  # noqa: E402

NOW = datetime(2026, 10, 16, 5, 30)

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>A food feed</title>
<item><title>Bai Tong opens in Alpha City</title><link>https://feed.test/bai-tong</link>
<pubDate>Wed, 14 Oct 2026 22:38:59 GMT</pubDate><description>A Thai spot from the Lao Kitchen team.</description>
<content:encoded><![CDATA[<p>Bai Tong opens Friday. The Casa Pasta crew are regulars. Gone Grill has closed its doors for good.</p>]]></content:encoded></item>
<item><title>The best bagels in Alpha City</title><link>https://feed.test/bagels</link>
<pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate><description>Bagel Corner leads our list; Sweet Shop is a close second.</description></item>
<item><title>Shh: a semi-secret way to get burgers delivered</title><link>https://feed.test/burgers</link>
<pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate><description>No names here.</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>r/food</title>
<entry><title>Imperial Soup [Oakland Chinatown]</title><link rel="alternate" href="https://atom.test/1"/>
<published>2026-10-14T18:00:00Z</published><summary>spare ribs over rice</summary></entry></feed>"""

HO = json.dumps({"topic_list": {"topics": [
    {"id": 1, "slug": "about", "title": "About the SF Bay Area category", "pinned": True, "created_at": "2016-01-21T00:00:00Z"},
    {"id": 22, "slug": "junes-pizza", "title": "June's Pizza [West Oakland]", "created_at": "2026-10-10T00:00:00Z",
     "last_posted_at": "2026-10-14T03:00:00Z", "posts_count": 12, "views": 300, "excerpt": "A new slice window."},
    {"id": 23, "slug": "ludwigs", "title": "Ludwig‘s (Mountain View)", "created_at": "2026-10-12T00:00:00Z",
     "last_posted_at": "2026-10-15T03:00:00Z", "posts_count": 3, "views": 80},
]}})


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "food.db")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SYNTHETIC_SEED))
    migrate.migrate_seed(seed, now=NOW, db_path=path)
    con = kb.connect(path)
    gz.upsert(con, [
        gz.osm_row({"type": "node", "id": 1, "lat": 37.27, "lon": -122.02, "tags": {"name": "Bai Tong", "amenity": "restaurant",
                    "cuisine": "thai", "website": "https://baitong.test"}}),
        gz.osm_row({"type": "node", "id": 2, "lat": 37.80, "lon": -122.27, "tags": {"name": "Imperial Soup", "amenity": "restaurant",
                    "addr:city": "Oakland"}}),
        gz.osm_row({"type": "node", "id": 3, "lat": 37.27, "lon": -122.03, "tags": {"name": "Gone Grill", "amenity": "restaurant"}}),
    ], release="test", now=NOW)
    con.commit()
    con.close()
    return path


# ── readers ─────────────────────────────────────────────────────────────
def test_rss_and_atom_and_hungry_onion_parse_to_items():
    items = readers.parse_rss(RSS)
    assert [i.title for i in items][:2] == ["Bai Tong opens in Alpha City", "The best bagels in Alpha City"]
    assert items[0].published == "2026-10-14" and items[0].url == "https://feed.test/bai-tong"
    assert "opens Friday" in items[0].text() and "<p>" not in items[0].text()
    atom = readers.parse_rss(ATOM)
    assert atom[0].title == "Imperial Soup [Oakland Chinatown]" and atom[0].url == "https://atom.test/1" and atom[0].published == "2026-10-14"
    ho = readers.parse_hungryonion(HO)
    assert [i.title for i in ho] == ["June's Pizza [West Oakland]", "Ludwig‘s (Mountain View)"]
    assert ho[0].url.endswith("/t/junes-pizza/22") and ho[0].published == "2026-10-14" and ho[0].extra["posts"] == 12
    assert readers.parse_rss("not xml") == [] and readers.parse_hungryonion("{") == []


def test_mentions_come_from_titles_headlines_and_known_names():
    src = {"id": "ho", "kind": "hungryonion", "name": "Hungry Onion"}
    m = readers.mentions_from(readers.Item(title="June's Pizza [West Oakland]", url="u", summary="A new slice window."), source=src)
    assert [(x.name, x.city, x.neighborhood, x.kind, x.how) for x in m] == [("June's Pizza", "oakland", "West Oakland", "opening", "title_convention")]
    m = readers.mentions_from(readers.Item(title="Ludwig‘s (Mountain View)", url="u"), source=src)
    assert [(x.name, x.city, x.kind) for x in m] == [("Ludwig‘s", "mountain view", "mention")]
    heads = {
        "T&T Asian Market's SF Location at Geary and Masonic to Open November 5": [("T&T Asian Market", "opening")],
        "Mama Ji's is closing after 15 years in the Castro": [("Mama Ji's", "closing")],
        "Nightbird Has Closed Its Doors for Good": [("Nightbird", "closed")],
        "Inside Bentornato, the new North Beach pasta spot opening Friday": [("Bentornato", "opening")],
        "A Victorian-inspired cocktail bar opens in a longtime Nob Hill watering hole": [],
        "Two Snail Bar veterans are opening a Mexican wine bar in the Richmond": [],
    }
    for title, want in heads.items():
        assert [(x.name, x.kind) for x in readers.mentions_from(readers.Item(title=title, url="u"))] == want, title
    idx = readers.NameIndex([("Che Fico", "san francisco"), ("Kitchen", None), ("Spark Social", "san jose"), ("Nightbird", None)])
    it = readers.Item(title="This Week In Food", url="u",
                      content="<p>Che Fico has opened a new bakery. The Kitchen is fine. Spark Social ignites downtown. "
                              "Sadly, Nightbird closed its doors for good. Progress was slow; the progress bar stalled.</p>")
    got = {x.name: (x.kind, x.how) for x in readers.mentions_from(it, index=idx)}
    assert got == {"Che Fico": ("opening", "known_name"), "Spark Social": ("mention", "known_name"), "Nightbird": ("closed", "known_name")}
    # a one-word name at a sentence start, or in lower case, is prose, not a place
    idx2 = readers.NameIndex([("Progress", "san francisco"), ("Nightbird", None), ("Three", None), ("Union Square", None)])
    it2 = readers.Item(title="Three new spots", url="u", content="<p>Nightbird closed. Progress was slow. We walked to Union Square.</p>")
    assert readers.mentions_from(it2, index=idx2) == []
    lst = readers.mentions_from(readers.Item(title="The best bagels in Alpha City", url="u", summary="Bagel Corner leads."),
                                index=readers.NameIndex([("Bagel Corner", "alpha_city")]))
    assert [(x.name, x.kind) for x in lst] == [("Bagel Corner", "list")]


def test_where_parts_read_the_area_words():
    assert readers.where_parts("San Francisco, North Beach") == ("san francisco", "North Beach")
    assert readers.where_parts("Oakland Chinatown") == ("oakland", "Oakland Chinatown")
    assert readers.where_parts("SF") == ("san francisco", None)
    assert readers.where_parts("Somewhere Else") == (None, "Somewhere Else")


# ── the store ───────────────────────────────────────────────────────────
def test_mentions_resolve_promote_and_become_acclaim(db):
    con = kb.connect(db)
    src = {"id": "feed", "name": "A food feed", "kind": "rss"}
    items = readers.parse_rss(RSS)
    index = readers.NameIndex(store.known_names(con))
    r = store.write_mentions(con, src, items[0], readers.mentions_from(items[0], index=index, source=src), near=(37.3, -122.0), now=NOW)
    con.commit()
    assert r["written"] >= 3 and r["resolved"] >= 3 and r["promoted"] == 2 and r["closed"] == 1 and r["acclaim"] == 2
    bai = query.find_place(con, "Bai Tong")[0]
    assert bai["source_list"] == "gazetteer" and bai["status"] == "unknown"
    acclaim = con.execute("SELECT writer, url, quote FROM kb_acclaim a JOIN kb_place p USING(place_id) WHERE p.name = 'Bai Tong'").fetchall()
    assert acclaim and acclaim[0][0] == "A food feed" and acclaim[0][1] == "https://feed.test/bai-tong"
    assert kb.current_fact(con, "place", bai["place_id"], "acclaim")["source_rank"] == "critic"
    gone = query.find_place(con, "Gone Grill")[0]
    assert gone["status"] == "closed" and kb.current_fact(con, "place", gone["place_id"], "status")["value"] == "closed"
    casa = query.find_place(con, "Casa Pasta")[0]        # the seed already praised it twice; the feed adds one
    assert con.execute("SELECT COUNT(*) FROM kb_acclaim WHERE place_id = ? AND url = ?",
                       (casa["place_id"], "https://feed.test/bai-tong")).fetchone()[0] == 1
    # the same item again writes nothing twice
    again = store.write_mentions(con, src, items[0], readers.mentions_from(items[0], index=index, source=src), near=(37.3, -122.0), now=NOW)
    assert again["written"] == 0
    rows = store.recent(con, days=3, now=NOW)
    assert {x["name"] for x in rows} >= {"Bai Tong", "Casa Pasta", "Gone Grill"}
    assert [x["kind"] for x in rows if x["name"] == "Gone Grill"] == ["closed"]


def test_an_unknown_name_is_kept_unresolved(db):
    con = kb.connect(db)
    src = {"id": "feed", "name": "A food feed", "kind": "rss"}
    it = readers.Item(title="Nowhere Noodles opens in Alpha City", url="https://feed.test/nowhere", published="2026-10-14")
    r = store.write_mentions(con, src, it, readers.mentions_from(it, source=src), near=(37.3, -122.0), now=NOW)
    assert r == {"written": 1, "resolved": 0, "promoted": 0, "acclaim": 0, "closed": 0}
    row = con.execute("SELECT name, kind, place_id, gaz_id FROM kb_mention WHERE url = ?", (it.url,)).fetchone()
    assert row == ("Nowhere Noodles", "opening", None, None)


# ── a place's own page ──────────────────────────────────────────────────
LD = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebSite","name":"x"},
{"@type":"Restaurant","name":"Bai Tong","telephone":"+1 555 0100","priceRange":"$$","servesCuisine":["Thai"],
"hasMenu":{"@type":"Menu","url":"https://baitong.test/menu"},
"openingHoursSpecification":[{"@type":"OpeningHoursSpecification","dayOfWeek":["Monday","Tuesday","Wednesday"],"opens":"11:30","closes":"21:00"},
{"@type":"OpeningHoursSpecification","dayOfWeek":"https://schema.org/Sunday","opens":"17:00","closes":"22:00"}]}]}</script></head><body>hi</body></html>"""


def test_the_own_page_yields_hours_phone_price_and_menu():
    sf = ownsite.facts_from_html(LD, "https://baitong.test")
    assert sf.ok and sf.hours_text == "Monday: 11:30 AM–9:00 PM; Tuesday: 11:30 AM–9:00 PM; Wednesday: 11:30 AM–9:00 PM; Sunday: 5:00 PM–10:00 PM"
    assert sf.phone == "+1 555 0100" and sf.price_range == "$$" and sf.cuisine == "Thai" and sf.menu_url == "https://baitong.test/menu"
    assert not ownsite.facts_from_html("<html><body>no data</body></html>", "u").ok
    assert ownsite.hours_from_text("Mo-Fr 09:00-17:00").startswith("Monday: 9:00 AM–5:00 PM")


def test_read_site_goes_through_the_source_rules_and_is_hermetic(monkeypatch):
    from core import sources
    monkeypatch.setattr(sources, "policy_for", lambda url: {"policy": "blocked", "why": "test"})
    sf = ownsite.read_site("https://blocked.test", now=NOW)
    assert not sf.ok and sf.error.startswith("blocked")
    monkeypatch.setattr(sources, "policy_for", lambda url: {"policy": "check_robots", "why": ""})
    sf = ownsite.read_site("https://nocassette.test", now=NOW)
    assert not sf.ok and "hermetic" in (sf.error or "")


# ── the nightly job ─────────────────────────────────────────────────────
def test_the_nightly_job_reads_feeds_refreshes_sites_and_records_itself(db, tmp_path, monkeypatch):
    feeds = {"https://feed.test/rss": RSS, "https://ho.test/9.json": HO}
    sources = [{"id": "feed", "name": "A food feed", "kind": "rss", "url": "https://feed.test/rss"},
               {"id": "ho", "name": "Hungry Onion", "kind": "hungryonion", "url": "https://ho.test/9.json"},
               {"id": "dead", "name": "A dead feed", "kind": "rss", "url": "https://dead.test/rss"}]
    monkeypatch.setattr(nightly, "FOOD_SOURCES", sources)

    def http(url):
        if url in feeds:
            return feeds[url]
        raise RuntimeError("HTTP 404")

    def reader(url, *, now):
        sf = ownsite.facts_from_html(LD, url) if "baitong" in url else ownsite.SiteFacts(url=url, error="no schema.org facts on the page")
        sf.fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
        return sf
    logs: list[str] = []
    out = nightly.run(now=NOW, db_path=db, http=http, reader=reader, log=logs.append)
    f = out["feeds"]
    assert f["feeds"] == 3 and f["ok"] == 2 and f["mentions"] >= 5 and f["promoted"] >= 2
    con = kb.connect(db)
    meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM kb_meta WHERE key LIKE 'food_%'")}
    assert meta["food_feed:feed"]["ok"] is True and meta["food_feed:dead"]["ok"] is False and meta["food_feed:dead"]["fails"] == 1
    assert meta["food_nightly:last_run"]["at"] == "2026-10-16 05:30"
    # the refresh read Bai Tong's site (a website on file from the gazetteer) and wrote its hours
    bai = query.find_place(con, "Bai Tong")[0]
    h = kb.current_fact(con, "place", bai["place_id"], "hours")
    assert h and h["source_rank"] == "own_site" and h["value"]["value"].startswith("Monday: 11:30 AM")
    assert con.execute("SELECT COUNT(*) FROM kb_hours WHERE place_id = ?", (bai["place_id"],)).fetchone()[0] == 4
    assert kb.current_fact(con, "place", bai["place_id"], "menu_url")["value"]["value"] == "https://baitong.test/menu"
    r = out["refresh"]
    assert r["read"] >= 1 and r["with_facts"] == 1 and meta["food_refresh:last"]["hours"] == 1
    # a second run the same morning reads nothing twice
    again = nightly.run(now=NOW, db_path=db, http=http, reader=reader, log=logs.append)
    assert again["feeds"]["mentions"] == 0 and again["refresh"]["candidates"] < r["candidates"]


def test_refresh_candidates_are_the_stale_ones_with_a_site_oldest_first(db):
    con = kb.connect(db)
    gz.promote(con, gz.find(con, "Bai Tong")[0], now=NOW)
    con.commit()
    cands = nightly.refresh_candidates(con, now=NOW)
    assert [c["name"] for c in cands] == ["Bai Tong"]       # the seed's places have no website on file
    assert cands[0]["hours_at"] is None


def test_the_daily_line_says_when_a_feed_or_the_job_goes_quiet(db, monkeypatch):
    from core import health
    con = kb.connect(db)
    nightly._meta_set(con, "food_nightly:last_run", {"at": "2026-10-12 05:30"})
    nightly._meta_set(con, "food_feed:eater-sf", {"at": "2026-10-16 05:30", "ok": False, "fails": 3, "last_ok": "2026-10-12 05:30"})
    nightly._meta_set(con, "food_feed:sfist-food", {"at": "2026-10-16 05:30", "ok": False, "fails": 1})
    con.commit()
    lines = health.food_feeds_silent(NOW, path=db)
    assert lines == ["Food: the mentions job hasn't run since Oct 12.",
                     "Food: the Eater San Francisco feed has fetched nothing since Oct 12 (3 nights)."]
    con.execute("DELETE FROM kb_meta WHERE key LIKE 'food_%'")
    con.commit()
    assert health.food_feeds_silent(NOW, path=db) == [], "never run: nothing to say"


def test_kb_mentions_lists_the_week(db, monkeypatch):
    from agents.bourdain import handler
    from core import io as cio
    monkeypatch.setattr(cio, "DB_PATH", db)
    con = kb.connect(db)
    src = {"id": "sfist-food", "name": "SFist Food", "kind": "rss"}
    it = readers.Item(title="Bai Tong opens in Alpha City", url="https://feed.test/bt", published="2026-10-14")
    store.write_mentions(con, src, it, readers.mentions_from(it, source=src), near=(37.3, -122.0), now=NOW)
    con.commit()
    con.close()
    text = handler.handle_kb("/kb mentions", now=NOW)
    assert text.startswith("From the feeds, last 7 days:") and "Bai Tong" in text and "new" in text and "sfist food" in text
    assert "Openings" in handler.handle_kb("/kb new", now=NOW)
    assert "Nothing from the feeds" in handler.handle_kb("/kb mentions", now=datetime(2026, 12, 1))


def test_a_mentioned_gazetteer_row_comes_first_in_a_nearby_search(db):
    con = kb.connect(db)
    src = {"id": "feed", "name": "A food feed", "kind": "rss"}
    it = readers.Item(title="A hundred dishes to try", url="https://feed.test/100", published="2026-10-14",
                      content="<p>" + " ".join(f"Dish {i} at Gone Grill." for i in range(12)) + " Also Bai Tong is lovely.</p>")
    ms = readers.mentions_from(it, index=readers.NameIndex(store.known_names(con)), source=src)
    assert {m.name for m in ms} == {"Gone Grill", "Bai Tong"} and len(ms) <= 8
    # few names: promotable; so both are promoted here. Make the point with a direct, unpromoted mention instead.
    con.execute("INSERT INTO kb_mention (source_id, url, title, published, name, name_norm, kind, how, quote, gaz_id, found_at) "
                "VALUES ('feed','https://feed.test/x','x','2026-10-14','Gone Grill','gone grill','mention','known_name','', 'osm:node/3', '2026-10-16 05:30')")
    con.commit()
    near = gz.nearby(con, 37.27, -122.02, km=5)
    assert near[0]["name"] == "Gone Grill" and near[0]["mentions"] == 1 and near[1]["mentions"] == 0
