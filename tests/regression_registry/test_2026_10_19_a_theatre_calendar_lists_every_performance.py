"""Regression (2026-10-19): a theatre's calendar lists every performance.

LIVE: a community theatre's public Google Calendar had 68 events and
none in the next 35 days, so the feed "fetched nothing ever". The
theatre's own site exports its calendar as iCal, with every production
back to 2013 and every performance after a run's first one listed as an
RDATE period (zero-length, no DTEND). The reader knew RRULE, EXDATE and
RECURRENCE-ID but not RDATE, so each production came back as its
opening night only. Then the window rule took a run with performances
on five or more days in the window for a daily offering and kept one
row a week, each week's Friday: a weekend digest never saw a matinee.

Fix (bridges/events/readers): RDATE values (DATE, DATE-TIME or PERIOD,
comma-separated, folded) are occurrences, with the same EXDATE and
RECURRENCE-ID rules; a daily offering is five or more days in one week.
`report` says how many events the feed held and its newest date, so an
abandoned calendar reads "nothing upcoming; newest 2025-06-14".
"""
from __future__ import annotations

from datetime import datetime

from bridges.events import readers

TODAY = datetime(2026, 10, 19, 12, 0)
SRC = {"id": "hilltop-players", "kind": "ical", "city": "Alpha Town", "venue": "Hilltop Hall", "categories": ["theatre"]}


def _ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


SHOW = ("BEGIN:VEVENT\r\nUID:101-events@hilltop.example\r\n"
        "DTSTART;TZID=America/Los_Angeles:20261023T190000\r\n"
        "SUMMARY:The Lantern\r\n"
        "RDATE;TZID=America/Los_Angeles;VALUE=PERIOD:20261024T140000/20261024T140000\r\n"
        " ,20261024T190000/20261024T190000,20261025T140000/20261025T140000,20261030T\r\n"
        " 190000/20261030T190000,20261031T140000/20261031T140000,20261031T190000/202\r\n"
        " 61031T190000,20261101T140000/20261101T140000\r\n"
        "EXDATE;TZID=America/Los_Angeles:20261030T190000\r\n"
        "URL:https://hilltop.example/events/the-lantern/\r\nEND:VEVENT\r\n")
OLD_SHOW = ("BEGIN:VEVENT\r\nUID:7-events@hilltop.example\r\n"
            "DTSTART;TZID=America/Los_Angeles:20140307T200000\r\nSUMMARY:An Old Comedy\r\n"
            "RDATE;TZID=America/Los_Angeles;VALUE=PERIOD:20140308T200000/20140308T200000\r\nEND:VEVENT\r\n")
DROP_IN = ("BEGIN:VEVENT\r\nUID:play@hilltop.example\r\n"
           "DTSTART;TZID=America/Los_Angeles:20261019T100000\r\nSUMMARY:Open Play\r\n"
           "RRULE:FREQ=DAILY;UNTIL=20261115T235959Z\r\nEND:VEVENT\r\n")


def test_every_performance_of_a_run_is_listed_and_an_exdate_is_skipped():
    events = readers.parse_ical(_ics(SHOW, OLD_SHOW), SRC, today=TODAY, horizon_days=35)
    starts = [e["start_ts"] for e in events if e["title"] == "The Lantern"]
    assert starts == ["2026-10-23 19:00:00", "2026-10-24 14:00:00", "2026-10-24 19:00:00", "2026-10-25 14:00:00",
                      "2026-10-31 14:00:00", "2026-10-31 19:00:00", "2026-11-01 14:00:00"]
    assert not any(e["title"] == "An Old Comedy" for e in events), "a 2014 run is history"


def test_a_weekend_sees_the_matinees():
    events = readers.parse_ical(_ics(SHOW), SRC, today=TODAY, horizon_days=35)
    weekend = [e["start_ts"] for e in events if "2026-10-24" <= e["start_ts"][:10] <= "2026-10-25"]
    assert weekend == ["2026-10-24 14:00:00", "2026-10-24 19:00:00", "2026-10-25 14:00:00"]


def test_a_daily_drop_in_is_still_one_row_a_week():
    events = readers.parse_ical(_ics(DROP_IN), SRC, today=TODAY, horizon_days=35)
    weeks = [datetime.strptime(e["start_ts"][:10], "%Y-%m-%d").strftime("%G-%V") for e in events]
    assert len(weeks) == len(set(weeks)) and len(weeks) >= 4


def test_the_report_says_what_the_feed_held():
    report: dict = {}
    readers.parse_ical(_ics(SHOW, OLD_SHOW), SRC, today=TODAY, horizon_days=35, report=report)
    assert report == {"seen": 9, "newest": "2026-11-01"}
    report = {}
    assert readers.parse_ical(_ics(OLD_SHOW), SRC, today=TODAY, horizon_days=35, report=report) == []
    assert report == {"seen": 2, "newest": "2014-03-08"}
    from bridges.events.ingest import empty_outcome
    assert empty_outcome(report, TODAY) == "nothing upcoming; newest 2014-03-08"


def test_fetch_reads_a_quiet_calendar_further_ahead():
    later = ("BEGIN:VEVENT\r\nUID:talk@hist.example\r\nDTSTART;TZID=America/Los_Angeles:20270115T190000\r\n"
             "SUMMARY:Winter lecture\r\nEND:VEVENT\r\n")
    src = {"id": "quiet-cal", "kind": "ical", "url": "https://hist.example/cal.ics", "quiet": True, "horizon_days": 120}
    events = readers.fetch(src, today=TODAY, http=lambda url: _ics(later))
    assert [e["title"] for e in events] == ["Winter lecture"]
    assert readers.fetch(dict(src, horizon_days=None), today=TODAY, http=lambda url: _ics(later)) == []
