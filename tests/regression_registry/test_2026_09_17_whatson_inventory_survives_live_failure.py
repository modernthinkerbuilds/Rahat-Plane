"""Regression pin (2026-09-17) — the verified feeds answer even when the
live AI lookup is down, and an audience qualifier is honored.

LIVE (Wed 2026-09-17 01:05, owner screenshot + vault/genie_bot.log):
"Are there any infant friendly events this weekend?" → "Couldn't reach
live listings just now — try again in a bit" — twice. The log shows
Gemini answering 429 RESOURCE_EXHAUSTED ("Your project has exceeded its
monthly spending cap") to both the concierge and the live discovery.
That outage is the owner's to clear (AI Studio spend cap), but it
exposed two Rahat faults:
  1. handle_whats_on had ALREADY loaded the verified inventory for the
     weekend and threw it away when discover_options returned None.
     The feeds are the primary source; live search is the garnish.
  2. "infant friendly" was never read — no filter, no acknowledgement.

THE PINS.
  * Live discovery failing with inventory in hand → the inventory
    reply, with an honest "live search is unavailable" line; the old
    "couldn't reach" text only when the feeds are empty too.
  * audience_terms: infant/baby/newborn → infant; toddler; kid(s)/
    family → kids; teen; adult/date night → adults; none → [].
  * split_by_audience ranks by title / categories / the popularity
    table's audience column; family / all-ages / storytime rows count
    for the youngest asks.
  * With a match: only matching rows, a "Filtered for infants /
    babies: N of M" line, and the /whatson pointer. With no match:
    everything, and an honest "nothing tagged for …" line.
  * route() passes the message through, so the live phrasing is
    answered from the feeds with the filter applied.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

_WED = datetime(2026, 9, 16, 9, 0)            # → weekend Sat 09-19 / Sun 09-20
_SRC = {"id": "s", "kind": "search", "name": "S", "url": "x",
        "city": "San Jose", "categories": []}
_LIVE = "Are there any infant friendly events this weekend?"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "San Jose, CA")
    monkeypatch.setenv("RAHAT_GENIE_LIVE_PLAN", "1")
    monkeypatch.setenv("RAHAT_GENIE_CONCIERGE", "0")
    return tmp_path


def _seed(events, when=_WED):
    from bridges.events.ingest import refresh_source
    refresh_source(_SRC, today=when,
                   llm=lambda p: json.dumps({"events": events}))


def _weekend():
    _seed([{"title": "Baby Lapsit Storytime", "start_ts": "2026-09-19 10:00:00",
            "venue": "Linden Tree", "city": "Los Altos",
            "categories": ["library", "kids"], "url": "https://lt.org/a"},
           {"title": "Family Fun Day", "start_ts": "2026-09-20 11:00:00",
            "venue": "History Park", "city": "San Jose",
            "url": "https://hp.org/b"},
           {"title": "Craft Beer Crawl", "start_ts": "2026-09-19 18:00:00",
            "venue": "Downtown", "city": "San Jose",
            "categories": ["21+"], "url": "https://beer.org/c"}])


def _dead_llm(prompt):
    raise RuntimeError("429 RESOURCE_EXHAUSTED monthly spending cap")


# ── inventory survives a live failure ─────────────────────────────────
def test_live_failure_returns_the_verified_inventory(env):
    from agents.genie import handler
    _weekend()
    out = handler.handle_whats_on(now=_WED, llm=_dead_llm)
    assert "Couldn't reach live listings" not in out
    assert "From your event feeds" not in out       # offline shape
    assert "weekend of 2026-09-19" in out
    assert "Baby Lapsit Storytime" in out and "Craft Beer Crawl" in out
    assert "Live search is unavailable right now" in out


def test_live_failure_with_empty_feeds_still_says_so(env):
    from agents.genie import handler
    out = handler.handle_whats_on(now=_WED, llm=_dead_llm)
    assert "Couldn't reach live listings" in out and "try again" in out


# ── the audience qualifier ────────────────────────────────────────────
def test_audience_terms_vocabulary():
    from agents.genie.handler import audience_terms as a
    assert a(_LIVE) == ["infant"]
    assert a("baby friendly stuff sunday") == ["infant"]
    assert a("any toddler events?") == ["toddler"]
    assert a("kids events this weekend") == ["kids"]
    assert a("date night saturday") == ["adults"]
    assert a("what's on this weekend") == []
    assert a("teen and family things") == ["kids", "teens"]


def test_split_by_audience_uses_title_categories_and_reviews(env):
    from agents.genie import handler
    from bridges.events import store
    _weekend()
    store.set_popularity(store.series_key("Craft Beer Crawl", "Downtown"),
                         70, "popular", "adults", now=_WED)
    rows = store.query_window("2026-09-19", "2026-09-20")
    hit, rest = handler.split_by_audience(rows, ["infant"])
    assert [r["title"] for r in hit] == ["Baby Lapsit Storytime",
                                          "Family Fun Day"]
    assert [r["title"] for r in rest] == ["Craft Beer Crawl"]
    hit, _ = handler.split_by_audience(rows, ["adults"])
    assert [r["title"] for r in hit] == ["Craft Beer Crawl"]   # via reviews


def test_infant_ask_is_filtered_and_labelled(env):
    from agents.genie import handler
    _weekend()
    out = handler.handle_whats_on(now=_WED, llm=_dead_llm,
                                  audience_text=_LIVE)
    assert "Baby Lapsit Storytime" in out and "Family Fun Day" in out
    assert "Craft Beer Crawl" not in out
    assert "Filtered for infants / babies: 2 of 3 verified events" in out
    assert "`/whatson` shows everything" in out


def test_no_match_shows_everything_honestly(env):
    from agents.genie import handler
    _seed([{"title": "Craft Beer Crawl", "start_ts": "2026-09-19 18:00:00",
            "venue": "Downtown", "city": "San Jose"}])
    out = handler.handle_whats_on(now=_WED, llm=_dead_llm,
                                  audience_text="any teen events this weekend")
    assert "Nothing in the feeds is tagged for teens" in out
    assert "Craft Beer Crawl" in out


def test_route_passes_the_live_phrasing_through(env, monkeypatch):
    from agents.genie import handler
    _weekend()
    monkeypatch.setattr(handler, "datetime", _Fixed)
    monkeypatch.setattr(handler, "_hermetic", lambda: False)
    from agents.genie import live_plan
    monkeypatch.setattr(live_plan, "discover_options",
                        lambda **kw: None)              # the 429, in effect
    out = handler.route(_LIVE)
    assert "Baby Lapsit Storytime" in out
    assert "Craft Beer Crawl" not in out
    assert "Filtered for infants / babies" in out
    assert "Couldn't reach live listings" not in out


class _Fixed(datetime):
    @classmethod
    def now(cls, tz=None):
        return _WED
