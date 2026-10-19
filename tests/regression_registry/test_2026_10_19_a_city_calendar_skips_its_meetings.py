"""Regression (2026-10-19): a calendar's meetings and holds are not outings.

LIVE: five cities' calendars were read through their CivicPlus RSS,
which carries whatever the city pushes into it, dated by publishing day:
one was press releases, one a commissioners' dinner, two nothing. The fix
reads each city's iCal export, which is the calendar itself, but a city
calendar also holds the council, the commissions and the committees.
A library's LibCal list (the replacement for a site that refuses the
reader) holds its class visits and room holds, one marked "please do not
post on the web calendar".

Fix: a source's `skip` pattern (registry) drops a title in every code
reader and in the page kind's extraction; `fetch(report=…)` counts the
skipped, and a refresh where everything was skipped says "nothing
listed; N skipped".
"""
from __future__ import annotations

from datetime import datetime

from bridges.events import ingest, readers
from bridges.events.registry import load_sources
from bridges.events.sources_bayarea import _MEETINGS

TODAY = datetime(2026, 10, 19, 12, 0)


def _ics(*titles_and_starts: tuple[str, str]) -> str:
    body = "".join(f"BEGIN:VEVENT\r\nUID:{i}@city.example\r\nSUMMARY:{t}\r\n"
                   f"DTSTART;TZID=America/Los_Angeles:{s}\r\nEND:VEVENT\r\n"
                   for i, (t, s) in enumerate(titles_and_starts))
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + body + "END:VCALENDAR\r\n"


CITY = {"id": "alpha-city", "kind": "ical", "url": "https://alpha.example/iCalendar.aspx?catID=14&feed=calendar",
        "city": "Alpha Town", "categories": ["city"], "skip": _MEETINGS}


def test_a_city_calendar_keeps_the_outings_and_drops_the_meetings():
    text = _ics(("City Council Regular Meeting", "20261020T190000"), ("Planning Commission", "20261021T190000"),
                ("Parks and Recreation Commission Meeting", "20261022T180000"), ("Halloween Carnival", "20261024T110000"),
                ("Board Game Night", "20261023T180000"), ("Study Session: Budget", "20261026T170000"))
    report: dict = {}
    events = readers.fetch(CITY, today=TODAY, http=lambda url: text, report=report)
    assert [e["title"] for e in events] == ["Board Game Night", "Halloween Carnival"]
    assert report["skipped"] == 4
    assert "_skipped" not in CITY, "the registry's dict is left as it was"


def test_a_calendar_of_only_meetings_says_so():
    text = _ics(("City Council Regular Meeting", "20261020T190000"), ("Arts Committee", "20261021T190000"))
    r = ingest.refresh_source(dict(CITY), today=TODAY, http=lambda url: text, db_path=":memory:")
    assert r["fetched"] == 0 and r["outcome"] == "nothing listed; 2 skipped"


def test_the_page_kind_drops_them_too():
    raw = ('{"events": [{"title": "Library Board Meeting", "start_ts": "2026-10-22 18:00:00"},'
           ' {"title": "Pumpkin Patch Story Walk", "start_ts": "2026-10-24 10:00:00"}]}')
    events = ingest._typed_events(raw, dict(CITY, kind="page"))
    assert [e["title"] for e in events] == ["Pumpkin Patch Story Walk"]


def test_the_library_drops_its_class_visits_and_holds():
    lib = next(s for s in load_sources() if s["id"] == "sunnyvale-library")
    for title, kept in (("Class Visit - Ellis", False), ("Room Hold (Please do not post on the web calendar)", False),
                        ("Toddler Storytime", True), ("Baby Lapsit Storytime", True), ("Paws to Read", True)):
        assert readers.skipped_by_source(lib, title) is (not kept), title


def test_every_city_ical_source_skips_meetings():
    for s in load_sources():
        if s.get("kind") == "ical" and "iCalendar.aspx" in s["url"] and "catID=" in s["url"] and s.get("skip"):
            assert readers.skipped_by_source(s, "City Council Regular Meeting"), s["id"]
            assert not readers.skipped_by_source(s, "Holiday Tree Lighting"), s["id"]
