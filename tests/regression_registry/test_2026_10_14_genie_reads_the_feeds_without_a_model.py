"""Regression (2026-10-14): Genie reads the calendars' own feeds, in code.

LIVE (owner, 2026-10-11): "I look at the Palo Alto, Mountain View,
Berkeley, Oakland, Redwood City, San Carlos calendars … none of the
events show up. Just some San Jose weird events." Genie's inventory was
81% two library systems because its library sources read a 25-item RSS
(two days of events) through a model; twenty of its 25 sources were
grounded searches that surface a fraction of a calendar and, since the
September spend cap, ran twice a week; the Mountain View city page had
returned 403 on every refresh since 2026-08-24 (149 tries, 0 events) and
serves a browser a stale March grid. The research found whole calendars
published as JSON, iCal and RSS by every library system, the family
museums and the arts venues: Genie read none of them.

Fix (bridges/events/readers.py, sources_bayarea.py, ingest.py):
  * nine code kinds — bibliocommons, libcal, communico, tribe (The
    Events Calendar), localist, livewhale, spektrix, squarespace_rss and
    a rewritten ical — parse a platform's response into events with no
    model call: every event in the feed, times in the household's zone,
    cancellations, closures and opening-hours rows dropped, an audience
    tag from the feed's own labels ("Babies (under 2)" → infant);
  * the old iCal reader read "20261011T200000Z" as 8pm local (it was
    1pm), kept the feed's first 40 rows before dropping past ones (on a
    LibCal feed, a month of past storytimes) and left "\\," in titles;
  * URL tokens ({start}, {end}, {days}, {page}) and the platforms' own
    paging; {family} becomes a BiblioCommons library's own ids for the
    young audiences, read from the feed;
  * the code kinds are free kinds (every pass); a page kind is read by
    the model once a day.
Synthetic samples in the live shapes; invented titles and venues.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from bridges.events import ingest, readers as R

TODAY = datetime(2026, 10, 14, 9, 0)


def _src(**kw) -> dict:
    base = {"id": "t", "kind": "tribe", "name": "T", "url": "https://x.test/feed?page=1", "city": "Alpha Town",
            "categories": ["library"]}
    base.update(kw)
    return base


# ── BiblioCommons ──────────────────────────────────────────────────────
BC = {
    "events": {"results": ["e1", "e2", "e3"], "pagination": {"count": 3, "page": 1, "pages": 1, "limit": 50}},
    "entities": {
        "events": {
            "e1": {"id": "e1", "definition": {"title": "Baby Bounce", "start": "2026-10-17T10:30", "end": "2026-10-17T11:00",
                                              "branchLocationId": "1S", "audienceIds": ["a-baby", "a-tod"],
                                              "typeIds": ["t-story"], "isCancelled": False}},
            "e2": {"id": "e2", "definition": {"title": "Closure: Staff Day", "start": "2026-10-18", "end": "2026-10-18",
                                              "branchLocationId": "1S", "audienceIds": ["a-baby"], "typeIds": ["t-close"],
                                              "isCancelled": False}},
            "e3": {"id": "e3", "definition": {"title": "Tax Help", "start": "2026-10-19T14:00", "end": "2026-10-19T16:00",
                                              "branchLocationId": "1S", "audienceIds": ["a-adult"], "typeIds": [],
                                              "isCancelled": True}},
        },
        "eventAudiences": {"a-baby": {"name": "Babies (under 2)"}, "a-tod": {"name": "Toddlers (18 mos. to 3 yrs)"},
                           "a-adult": {"name": "Adults, ages 18+"}, "a-all": {"name": "All Ages"}},
        "eventTypes": {"t-story": {"name": "Storytime"}, "t-close": {"name": "Library Closures"}},
        "locations": {"1S": {"name": "Alpha Branch", "address": {"city": "Alpha Town"}}},
    },
}


def test_bibliocommons_reads_the_search_response_with_its_lookups():
    src = _src(kind="bibliocommons", url="https://gateway.bibliocommons.com/v2/libraries/alpha/events/search?startDate={start}&endDate={end}&limit=50&page=1")
    events, more = R.parse_bibliocommons(json.dumps(BC), src)
    assert [e["title"] for e in events] == ["Baby Bounce"]          # the closure and the cancelled one are gone
    e = events[0]
    assert e["start_ts"] == "2026-10-17 10:30:00" and e["venue"] == "Alpha Branch" and e["city"] == "Alpha Town"
    assert e["url"] == "https://alpha.bibliocommons.com/v2/events/e1"
    assert "infant" in e["categories"] and "toddler" in e["categories"] and "storytime" in e["categories"]
    assert more is False


def test_the_family_token_becomes_the_librarys_own_audience_ids():
    assert R.bibliocommons_family_ids(json.dumps(BC)) == "a-all,a-baby,a-tod"
    src = _src(kind="bibliocommons", url="https://gateway.bibliocommons.com/v2/libraries/alpha/events/search?startDate={start}&endDate={end}&audiences={family}&limit=50&page=1")
    seen: list[str] = []

    def http(url):
        seen.append(url)
        return json.dumps(BC)
    out = R.fetch(src, today=TODAY, http=http)
    assert len(seen) == 2 and "limit=20" in seen[0] and "audiences=&" in seen[0]      # the probe, unfiltered
    assert "audiences=a-all,a-baby,a-tod&limit=50&page=1" in seen[1]
    assert "startDate=2026-10-14&endDate=2026-11-18" in seen[1]                       # 35 days
    assert [e["title"] for e in out] == ["Baby Bounce"]


# ── LibCal ─────────────────────────────────────────────────────────────
LIBCAL = {"total_results": 100, "perpage": 48, "results": [
    {"id": 1, "title": "Baby Storytime", "startdt": "2026-10-14 10:30:00", "enddt": "2026-10-14 11:15:00", "all_day": False,
     "url": "https://alpha.libcal.com/event/1", "location": "Program Room",
     "audiences": [{"name": "Babies"}, {"name": "Families"}], "categories_arr": [{"name": "Storytime"}]},
    {"id": 2, "title": "Library Closed: Holiday", "startdt": "2026-10-15 00:00:00", "enddt": "", "all_day": True,
     "url": "", "audiences": [], "categories_arr": []},
    {"id": 3, "title": "Trivia Night 21+", "startdt": "2026-10-16 19:00:00", "enddt": "2026-10-16 21:00:00", "all_day": False,
     "url": "https://alpha.libcal.com/event/3", "audiences": [{"name": "Adults"}], "categories_arr": []},
]}


def test_libcal_reads_the_calendar_list_and_pages_it():
    src = _src(kind="libcal", venue="Alpha Library", url="https://alpha.libcal.com/ajax/calendar/list?c=1&page=1", _url="x?page=1")
    events, more = R.parse_libcal(json.dumps(LIBCAL), src)
    assert [e["title"] for e in events] == ["Baby Storytime", "Trivia Night 21+"]
    assert events[0]["venue"] == "Alpha Library" and "infant" in events[0]["categories"]
    assert events[1]["categories"][-2:] == ["adults", "adults-only"]
    assert more is True                                             # 48 of 100 seen
    src["_url"] = "x?page=3"
    assert R.parse_libcal(json.dumps(LIBCAL), src)[1] is False      # 144 ≥ 100


# ── Communico, The Events Calendar, Localist, LiveWhale ────────────────
def test_communico_reads_ages_and_the_branch():
    rows = [{"id": "1", "title": "Ballet & Books", "event_start": "2026-10-17 10:00:00", "event_end": "2026-10-17 12:00:00",
             "library": "Central Library ", "url": "https://alpha.libnet.info/event/1", "agesArray": ["Elementary", "Early Childhood"]},
            {"id": "2", "title": "Teen Night", "event_start": "2026-10-17 18:00:00", "event_end": "2026-10-17 20:00:00",
             "library": "West Branch", "url": "", "ages": "Teens"}]
    events, _ = R.parse_communico(json.dumps(rows), _src(kind="communico"))
    assert events[0]["venue"] == "Central Library" and {"infant", "toddler", "kids"} <= set(events[0]["categories"])
    assert "teens" in events[1]["categories"] and "infant" not in events[1]["categories"]


TRIBE = {"events": [
    {"title": "Toddler Storytime", "start_date": "2026-10-17 11:30:00", "end_date": "2026-10-17 12:00:00", "all_day": False,
     "timezone": "America/Los_Angeles", "url": "https://museum.test/event/toddler-storytime/2026-10-17/",
     "venue": {"venue": "Alpha Museum", "city": "Alpha Town"}, "categories": [{"name": "Programs"}], "tags": []},
    {"title": "Museum Weekend Hours 9:30 to 4:30", "start_date": "2026-10-17 09:30:00", "end_date": "2026-10-17 16:30:00",
     "all_day": False, "url": "", "venue": {}, "categories": [{"name": "Museum Hours"}], "tags": []},
    {"title": "Indoor Exhibits", "start_date": "2026-10-17 00:00:00", "end_date": "2026-10-17 23:59:59", "all_day": True,
     "url": "", "venue": {}, "categories": [{"name": "Museum Special"}], "tags": [{"name": "always on"}]},
    {"title": "Night Watch", "start_date": "2026-10-15 13:00:00", "end_date": "2026-11-14 16:00:00", "all_day": False,
     "url": "https://gallery.test/event/night-watch/", "venue": {"venue": "Alpha Gallery", "city": "Beta City"},
     "categories": [{"name": "Exhibition"}], "tags": []},
    {"title": "Night Watch", "start_date": "2026-10-16 13:00:00", "end_date": "2026-11-15 16:00:00", "all_day": False,
     "url": "https://gallery.test/event/night-watch/", "venue": {"venue": "Alpha Gallery", "city": "Beta City"},
     "categories": [{"name": "Exhibition"}], "tags": []},
], "next_rest_url": "https://museum.test/wp-json/tribe/events/v1/events/?page=2", "total": 5, "total_pages": 2}


def test_the_events_calendar_drops_hours_and_filler_and_lists_a_run_once():
    text = '<script>\r document.addEventListener("x", function(){ a(); });</script>\n' + json.dumps(TRIBE)
    src = _src(kind="tribe", url="https://museum.test/wp-json/tribe/events/v1/events?per_page=50&start_date={start}&end_date={end}&page=1")
    calls: list[str] = []

    def http(url):
        calls.append(url)
        if url.endswith("page=2"):
            return json.dumps({"events": [], "next_rest_url": None})
        return text
    out = R.fetch(src, today=TODAY, http=http)
    assert [e["title"] for e in out] == ["Night Watch", "Toddler Storytime"]
    assert out[0]["venue"] == "Alpha Gallery" and out[0]["city"] == "Beta City" and out[0]["end_ts"] == "2026-11-14 16:00:00"
    assert "toddler" in out[1]["categories"] and out[1]["venue"] == "Alpha Museum"
    assert calls[1].endswith("page=2")                               # the feed's own next link


LOCALIST = {"events": [
    {"event": {"title": "Museum Minis: Storytime", "location_name": "Alpha Collection", "status": "live",
               "localist_url": "https://events.alpha.test/event/minis", "first_date": "2026-10-19", "last_date": "2026-10-19",
               "filters": {"event_audience": [{"name": "Families with young children"}], "event_types": [{"name": "Family"}]},
               "event_instances": [{"event_instance": {"start": "2026-10-19T11:15:00-07:00", "end": "2026-10-19T12:00:00-07:00", "all_day": False}}]}},
    {"event": {"title": "Long Take", "location_name": "Alpha Museum", "status": "live",
               "localist_url": "https://events.alpha.test/event/long-take", "first_date": "2026-10-15", "last_date": "2027-02-01",
               "filters": {"event_audience": [{"name": "Everyone"}], "event_types": [{"name": "Exhibition"}]},
               "event_instances": [{"event_instance": {"start": "2026-10-15T00:00:00-07:00", "end": None, "all_day": True}},
                                   {"event_instance": {"start": "2026-10-16T00:00:00-07:00", "end": None, "all_day": True}}]}},
], "page": {"current": 1, "total": 1, "next_page": None}}


def test_localist_keeps_the_offset_and_lists_an_exhibition_once():
    src = _src(kind="localist", url="https://events.alpha.test/api/2/events?start={start}&days={days}&pp=100&page=1")
    out = R.fetch(src, today=TODAY, http=lambda u: json.dumps(LOCALIST))
    assert [(e["title"], e["start_ts"]) for e in out] == [("Long Take", "2026-10-15 00:00:00"), ("Museum Minis: Storytime", "2026-10-19 11:15:00")]
    assert "exhibition" in out[0]["categories"] and "kids" not in out[0]["categories"]      # "Everyone" is not an audience
    assert {"kids", "toddler"} <= set(out[1]["categories"])


def test_livewhale_reads_the_group_feed():
    d = {"meta": {"page": 1, "total_pages": 1}, "data": [
        {"id": 1, "title": "Exhibit Tour", "url": "https://events.beta.test/e/1", "date_iso": "2026-10-14T00:00:00-07:00",
         "date2_iso": None, "is_all_day": 1, "is_canceled": 0},
        {"id": 2, "title": "Film Screening &amp; Talk", "url": "https://events.beta.test/e/2", "date_iso": "2026-10-14T19:00:00-07:00",
         "date2_iso": "2026-10-14T21:00:00-07:00", "is_all_day": 0, "is_canceled": 0},
        {"id": 3, "title": "Gone", "url": "", "date_iso": "2026-10-15T19:00:00-07:00", "is_all_day": 0, "is_canceled": 1}]}
    events, more = R.parse_livewhale(json.dumps(d), _src(kind="livewhale"))
    assert [(e["title"], e["start_ts"]) for e in events] == [("Exhibit Tour", "2026-10-14 00:00:00"),
                                                             ("Film Screening & Talk", "2026-10-14 19:00:00")]
    assert more is False


# ── Spektrix, Squarespace ──────────────────────────────────────────────
def test_spektrix_lists_a_shows_next_instance():
    rows = [{"name": "Quartet", "firstInstanceDateTime": "2026-10-21T19:30:00", "lastInstanceDateTime": "2026-10-21T19:30:00",
             "webUrl": "https://live.alpha.test/quartet", "attribute_Genre": "Jazz/Blues"},
            {"name": "A Run", "firstInstanceDateTime": "2026-10-01T19:30:00", "lastInstanceDateTime": "2026-10-20T14:30:00",
             "webUrl": "", "attribute_Genre": "Theater"},
            {"name": "Past", "firstInstanceDateTime": "2026-09-01T19:30:00", "lastInstanceDateTime": "2026-09-02T19:30:00",
             "webUrl": "", "attribute_Genre": "Pop"}]
    events, _ = R.parse_spektrix(json.dumps(rows), _src(kind="spektrix", venue="Alpha Hall"), TODAY)
    assert [(e["title"], e["start_ts"]) for e in events] == [("Quartet", "2026-10-21 19:30:00"), ("A Run (last date)", "2026-10-20 14:30:00")]
    assert "jazz" in events[0]["categories"] and events[0]["venue"] == "Alpha Hall"


def test_squarespace_rss_takes_the_date_from_the_link():
    rss = """<rss><channel>
    <item><title>Clockwork</title><link>https://alpha.test/calendar/2026/11/8/clockwork</link>
      <pubDate>Tue, 08 Sep 2026 19:18:21 +0000</pubDate><category>Concerts</category>
      <description><![CDATA[A vocal quartet. Doors 7pm, music at 7:30 pm.]]></description></item>
    <item><title>Blog post</title><link>https://alpha.test/news/hello</link><pubDate>Tue, 08 Sep 2026 19:18:21 +0000</pubDate></item>
    </channel></rss>"""
    events, _ = R.parse_squarespace_rss(rss, _src(kind="squarespace_rss"))
    assert [(e["title"], e["start_ts"]) for e in events] == [("Clockwork", "2026-11-08 19:00:00")]


# ── iCalendar ──────────────────────────────────────────────────────────
ICS = ("BEGIN:VCALENDAR\r\nX-WR-TIMEZONE:America/Los_Angeles\r\n"
       "BEGIN:VEVENT\r\nDTSTART:20261011T200000Z\r\nDTEND:20261011T220000Z\r\nSUMMARY:Film Matinee\\, Part 1\r\n"
       "LOCATION:Alpha Theater\r\nURL:https://alpha.test/film\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART;TZID=America/New_York:20261017T130000\r\nDTEND;TZID=America/New_York:20261017T140000\r\n"
       "SUMMARY:Storytime for\r\n  babies\r\nDESCRIPTION:Ages 0-2 with a grown-up\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261018\r\nDTEND;VALUE=DATE:20261019\r\nSUMMARY:Open Studios\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART:20260912T173000Z\r\nSUMMARY:Last month's storytime\r\nEND:VEVENT\r\n"
       "BEGIN:VEVENT\r\nDTSTART:20261223T000000Z\r\nSUMMARY:Modified Hours: Closed\r\nEND:VEVENT\r\n"
       "END:VCALENDAR")


def test_ical_times_are_local_and_the_past_is_dropped_before_any_cap():
    events = R.parse_ical(ICS, _src(kind="ical"), today=TODAY, horizon_days=90)
    assert [(e["title"], e["start_ts"]) for e in events] == [
        ("Storytime for babies", "2026-10-17 10:00:00"),        # 1pm New York
        ("Open Studios", "2026-10-18 00:00:00")]                # all day
    assert "infant" in events[0]["categories"]
    everything = R.parse_ical(ICS, _src(kind="ical"))
    assert everything[0]["title"] == "Film Matinee, Part 1" and everything[0]["start_ts"] == "2026-10-11 13:00:00"   # Z is UTC
    assert ingest.parse_ical is R.parse_ical


# ── audiences, noise, windows ──────────────────────────────────────────
@pytest.mark.parametrize("labels,expected", [
    (("Babies (under 2)", "Toddlers"), ["infant", "toddler"]),
    (("Adults, ages 18+",), ["adults"]),                           # an ordinary adult program
    (("Adults", "Kids (6-11)"), ["kids"]),                         # a family program
    (("Young Children, ages 0-5",), ["infant", "toddler", "kids"]),
    (("Adults, ages 18+", "Pre-Teens, ages 10-12"), ["kids"]),
    (("NightLife (21+)",), ["adults", "adults-only"]),
    (("Family Fun Night", "Adults, ages 18+"), ["kids"]),
    (("Everyone",), []),
])
def test_audience_tags(labels, expected):
    assert R.audience_tags(*labels) == expected


@pytest.mark.parametrize("title,noise", [
    ("CANCELED: Mindfulness", True), ("Library Closed: Holiday", True), ("Museum Weekend Hours 9:30 to 4:30", True),
    ("Holiday Week 9:30 – 4:30", True), ("Birthday – Nov 1 ALL", True), ("Members Only: Pumpkin Bash", True),
    ("Baby Bounce", False), ("Open Studios Art Tour", False),
])
def test_noise_rows_are_dropped(title, noise):
    assert R.is_noise(title) is noise


def test_a_daily_offering_is_listed_once_a_week_and_a_weekly_one_every_time():
    daily = [{"title": "Playscape", "start_ts": f"2026-10-{d:02d} 09:30:00", "end_ts": f"2026-10-{d:02d} 13:00:00", "venue": "M", "categories": []}
             for d in range(14, 28)]
    weekly = [{"title": "Baby Storytime", "start_ts": f"2026-10-{d:02d} 10:30:00", "end_ts": f"2026-10-{d:02d} 11:00:00", "venue": "L", "categories": []}
              for d in (14, 21, 28)]
    out = R._window(daily + weekly, TODAY, 30)
    assert [e["start_ts"][:10] for e in out if e["title"] == "Playscape"] == ["2026-10-14", "2026-10-19", "2026-10-26"]
    assert len([e for e in out if e["title"] == "Baby Storytime"]) == 3


def test_url_tokens_and_the_page_counter():
    assert R.expand_url("https://x.test/a?start={start}&end={end}&days={days}&page={page}", TODAY, 35, page=2) == \
        "https://x.test/a?start=2026-10-14&end=2026-11-18&days=35&page=2"
    assert R._with_page("https://x.test/a?page=1&limit=5", 3) == "https://x.test/a?page=3&limit=5"
    assert R._with_page("https://x.test/a", 2) == "https://x.test/a?page=2"


# ── the ingest shell ───────────────────────────────────────────────────
def test_ingest_dispatches_the_code_kinds_and_is_hermetic_without_a_seam(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "ev.db"))
    src = _src(id="alpha-lib", kind="bibliocommons",
               url="https://gateway.bibliocommons.com/v2/libraries/alpha/events/search?startDate={start}&endDate={end}&limit=50&page=1")
    assert ingest.refresh_source(src, today=TODAY)["fetched"] == 0                 # no wire under test mode
    r = ingest.refresh_source(src, today=TODAY, http=lambda u: json.dumps(BC))
    assert r["fetched"] == 1 and r["added"] == 1
    from bridges.events.store import query_window
    rows = query_window("2026-10-17", "2026-10-17", path=str(tmp_path / "ev.db"))
    assert rows[0]["title"] == "Baby Bounce" and "infant" in rows[0]["categories"]
    assert set(R.KINDS) <= set(ingest.FREE_KINDS) and "page" in ingest.FREE_KINDS


def test_a_page_source_is_read_once_a_day(monkeypatch, tmp_path):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    db = str(tmp_path / "ev.db")
    monkeypatch.setenv("RAHAT_EVENTS_DB", db)
    page = _src(id="venue-page", kind="page", url="https://venue.test/season")
    feed = _src(id="alpha-lib", kind="bibliocommons", url="https://gateway.bibliocommons.com/v2/libraries/alpha/events/search?page=1")
    monkeypatch.setattr(ingest, "load_sources", lambda: [page, feed])
    first = ingest.refresh_all(today=TODAY, kinds=ingest.FREE_KINDS, db_path=db)
    assert [r["source_id"] for r in first] == ["venue-page", "alpha-lib"]
    noon = TODAY.replace(hour=12, minute=30)
    assert [r["source_id"] for r in ingest.refresh_all(today=noon, kinds=ingest.FREE_KINDS, db_path=db)] == ["alpha-lib"]
    tomorrow = TODAY.replace(day=15, hour=7)
    assert [r["source_id"] for r in ingest.refresh_all(today=tomorrow, kinds=ingest.FREE_KINDS, db_path=db)] == ["venue-page", "alpha-lib"]


# ── the registry ───────────────────────────────────────────────────────
def test_the_registry_carries_the_bay_areas_feeds_and_retired_the_dead_ones():
    from bridges.events.registry import load_sources
    by_id = {s["id"]: s for s in load_sources()}
    assert by_id["sjpl"]["kind"] == "bibliocommons" and "/events/search?" in by_id["sjpl"]["url"]
    assert by_id["mv-libcal"]["kind"] == "libcal" and "c=8800" in by_id["mv-libcal"]["url"]
    assert by_id["broadway-sj"]["kind"] == "page" and by_id["sj-city"]["kind"] == "page"
    for retired in ("mv-city", "sfimp", "monterey-bigsur"):
        assert retired not in by_id, retired
    for sid, kind in (("smcl", "bibliocommons"), ("oakland-library", "bibliocommons"), ("berkeley-library", "communico"),
                      ("cdm-sj", "tribe"), ("stanford-live", "spektrix"), ("cantor", "localist"), ("montereypl", "ical"),
                      ("india-currents", "tribe"), ("downtown-losaltos", "tribe"), ("freight", "page"), ("hakone", "page")):
        assert by_id[sid]["kind"] == kind, sid
    assert "indian" in by_id["india-currents"]["categories"]
    assert all("kids" not in s["categories"] for s in by_id.values() if s.get("kind") in ("bibliocommons", "libcal", "communico"))
    assert len({s["id"] for s in load_sources()}) == len(load_sources())
