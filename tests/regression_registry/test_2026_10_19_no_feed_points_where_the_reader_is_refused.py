"""Regression (2026-10-19): no source points where the reader is refused or gone.

LIVE: the Oct 14 research read every URL with a research tool, whose
requests some sites let through; Genie's reader got 403 from six of
them from the first try (Akamai, Cloudflare, a city firewall), whatever
its User-Agent. Two addresses were wrong outright (a museum's look-alike
domain, a railroad's name that does not resolve) and two pages had
nothing left to give (one festival, over; a cinema whose special
screenings play elsewhere). In food, a paper refused the reader and a
section's feed looped on redirects.

Fix: the refusing sites are read by the paid search, which reads
Google's copy, and nothing tries to get past them (REFUSING_HOSTS); the
dead pages are retired (RETIRED, FOOD_RETIRED). These pins keep a page
or a code reader from being pointed back at a refusing host, and the
retired ids out.
"""
from __future__ import annotations

from urllib.parse import urlparse

from bridges.events.registry import load_sources
from bridges.events.sources_bayarea import REFUSING_HOSTS, RETIRED
from bridges.food.sources import FOOD_RETIRED, FOOD_SOURCES


def test_a_source_on_a_refusing_host_is_read_by_search_only():
    on_refusing = [s for s in load_sources() if urlparse(s["url"]).hostname in REFUSING_HOSTS]
    assert on_refusing, "the refusing hosts are ours"
    for s in on_refusing:
        assert s["kind"] == "search", s["id"]


def test_the_retired_feeds_stay_retired():
    ids = {s["id"] for s in load_sources()}
    assert not ids & set(RETIRED)
    assert not {s["id"] for s in FOOD_SOURCES} & set(FOOD_RETIRED)
    assert all(RETIRED.values()) and all(FOOD_RETIRED.values()), "each says why"


def test_a_quiet_calendar_looks_further_ahead():
    for s in load_sources():
        if s.get("quiet"):
            assert int(s.get("horizon_days") or 0) >= 90, s["id"]


def test_no_two_sources_share_an_address():
    urls = [s["url"] for s in load_sources()]
    assert len(urls) == len(set(urls))
    food = [s["url"] for s in FOOD_SOURCES]
    assert len(food) == len(set(food))
