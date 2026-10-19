"""Regression pin — 2026-10-15: a recurring calendar event is listed on
its dates, not once at a start months ago.

Health line, Oct 15: two public Google Calendars "fetched nothing ever
(3 tries)". Both answered 200. Their events are recurring (a museum's
open afternoons, a show's run), exported by Google as one VEVENT with an
RRULE whose DTSTART is months old; `parse_ical` listed an RRULE master
once, at DTSTART, so every occurrence fell before the window and the
feeds parsed to nothing. The same silently thinned every iCal feed with
weekly storytimes or recurring screenings.

Now RRULE masters are expanded inside the window (DAILY, WEEKLY with
BYDAY, MONTHLY by day-of-month or ordinal weekday, YEARLY; INTERVAL,
COUNT, UNTIL), EXDATEs are skipped, and an instance with a
RECURRENCE-ID replaces its occurrence. The log says how many of a feed's
events were recurring, so "fetched nothing" never hides "every event
was a repeat".
"""
from __future__ import annotations

from datetime import datetime

import pytest

from bridges.events import readers

SRC = {"id": "cal", "name": "A calendar", "kind": "ical", "url": "https://example.test/basic.ics",
       "city": "Saratoga", "venue": "The Museum", "categories": ["museum"]}

TODAY = datetime(2026, 10, 15, 9, 0)


def _ics(*vevents: str) -> str:
    body = "\n".join(vevents)
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nX-WR-TIMEZONE:America/Los_Angeles\n" + body + "\nEND:VCALENDAR\n")


def _vevent(uid: str, summary: str, dtstart: str, dtend: str, *extra: str) -> str:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", dtstart, dtend, *extra, "END:VEVENT"]
    return "\n".join(lines)


def test_a_weekly_museum_afternoon_that_started_in_march_is_listed_this_month():
    text = _ics(_vevent("open@cal", "Museum open afternoon",
                        "DTSTART;TZID=America/Los_Angeles:20260306T130000",
                        "DTEND;TZID=America/Los_Angeles:20260306T160000",
                        "RRULE:FREQ=WEEKLY;BYDAY=FR,SA,SU"))
    events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=14)
    days = sorted(e["start_ts"][:10] for e in events)
    # Oct 15 is a Thursday: Fri 16 is the first date in the window; nothing before today.
    assert days, "a recurring event parsed to nothing"
    assert days[0] == "2026-10-16"
    assert all(d >= "2026-10-15" for d in days)
    assert "13:00" in events[0]["start_ts"] and "16:00" in events[0]["end_ts"]


def test_a_shows_run_thursday_to_sunday_lists_every_performance_in_the_window():
    text = _ics(_vevent("show@cal", "The Play",
                        "DTSTART;TZID=America/Los_Angeles:20261022T200000",
                        "DTEND;TZID=America/Los_Angeles:20261022T223000",
                        "RRULE:FREQ=WEEKLY;BYDAY=TH,FR,SA,SU;UNTIL=20261115T065959Z"))
    events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=35)
    days = sorted(e["start_ts"][:10] for e in events)
    # Four shows a week is a run, not a daily offering (five or more days in one week): every show is kept
    # (2026-10-19; before, one row a week).
    assert days[0] == "2026-10-22"
    assert len({datetime.strptime(d, "%Y-%m-%d").strftime("%G-%V") for d in days}) >= 3
    assert all(d <= "2026-11-15" for d in days)
    assert all(datetime.strptime(d, "%Y-%m-%d").weekday() in (3, 4, 5, 6) for d in days)
    assert all("20:00" in e["start_ts"] for e in events)


def test_an_exdate_is_skipped_and_a_moved_instance_replaces_its_occurrence():
    text = _ics(
        _vevent("talk@cal", "History talk",
                "DTSTART;TZID=America/Los_Angeles:20260903T190000",
                "DTEND;TZID=America/Los_Angeles:20260903T203000",
                "RRULE:FREQ=WEEKLY;BYDAY=TH",
                "EXDATE;TZID=America/Los_Angeles:20261022T190000"),
        _vevent("talk@cal", "History talk (moved)",
                "DTSTART;TZID=America/Los_Angeles:20261030T180000",
                "DTEND;TZID=America/Los_Angeles:20261030T193000",
                "RECURRENCE-ID;TZID=America/Los_Angeles:20261029T190000"),
    )
    events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=21)
    by_day = {e["start_ts"][:10]: e for e in events}
    assert "2026-10-15" in by_day and "2026-11-05" in by_day
    assert "2026-10-22" not in by_day, "the EXDATE was listed"
    assert "2026-10-29" not in by_day, "the moved instance's old date was listed"
    assert by_day["2026-10-30"]["title"] == "History talk (moved)"
    assert "18:00" in by_day["2026-10-30"]["start_ts"]


def test_a_monthly_second_sunday_and_a_count_are_honoured():
    text = _ics(
        _vevent("tour@cal", "Docent tour",
                "DTSTART;VALUE=DATE:20260712", "DTEND;VALUE=DATE:20260713",
                "RRULE:FREQ=MONTHLY;BYDAY=2SU"),
        _vevent("series@cal", "Three-part lecture",
                "DTSTART;TZID=America/Los_Angeles:20261016T190000",
                "DTEND;TZID=America/Los_Angeles:20261016T200000",
                "RRULE:FREQ=WEEKLY;COUNT=3"),
    )
    events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=40)
    tours = sorted(e["start_ts"][:10] for e in events if e["title"] == "Docent tour")
    assert tours == ["2026-11-08"], tours          # the second Sunday of November; October's was the 11th
    lectures = sorted(e["start_ts"][:10] for e in events if e["title"] == "Three-part lecture")
    assert lectures == ["2026-10-16", "2026-10-23", "2026-10-30"]


def test_a_feed_of_only_past_recurrences_says_so_in_the_reader_note(caplog):
    text = _ics(_vevent("old@cal", "Old series",
                        "DTSTART;TZID=America/Los_Angeles:20250101T190000",
                        "DTEND;TZID=America/Los_Angeles:20250101T200000",
                        "RRULE:FREQ=WEEKLY;COUNT=4"))
    with caplog.at_level("INFO", logger="bridges.events.readers"):
        events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=35)
    assert events == []
    assert any("recurring" in r.getMessage() for r in caplog.records), \
        "a feed whose events are all repeats should say so in the log"


def test_without_a_window_a_master_is_still_listed_once():
    text = _ics(_vevent("open@cal", "Museum open afternoon",
                        "DTSTART;TZID=America/Los_Angeles:20260306T130000",
                        "DTEND;TZID=America/Los_Angeles:20260306T160000",
                        "RRULE:FREQ=WEEKLY;BYDAY=FR,SA,SU"))
    events = readers.parse_ical(text, SRC)
    assert len(events) == 1 and events[0]["start_ts"].startswith("2026-03-06")


@pytest.mark.parametrize("rule", ["FREQ=WEEKLY;BYDAY=XX", "FREQ=NOPE", ""])
def test_an_unreadable_rule_lists_the_master_once_and_never_raises(rule):
    text = _ics(_vevent("odd@cal", "Odd", "DTSTART;VALUE=DATE:20261020", "DTEND;VALUE=DATE:20261021",
                        f"RRULE:{rule}" if rule else "X-NOTE:none"))
    events = readers.parse_ical(text, SRC, today=TODAY, horizon_days=35)
    assert [e["start_ts"][:10] for e in events] == ["2026-10-20"]


def test_the_east_bay_family_calendar_is_read_from_its_ical_export_from_today():
    """The same health line: "510families has fetched nothing ever". The site answers 404 on The Events
    Calendar's REST route; its iCal export (a list view from today) is the feed it offers."""
    from bridges.events.registry import DEFAULT_SOURCES
    src = next(s for s in DEFAULT_SOURCES if s["id"] == "510families")
    assert src["kind"] == "ical"
    assert "ical=1" in src["url"] and "{start}" in src["url"]
    url = readers.expand_url(src["url"], TODAY, 35)
    assert "tribe-bar-date=2026-10-15" in url and "/wp-json/" not in url
