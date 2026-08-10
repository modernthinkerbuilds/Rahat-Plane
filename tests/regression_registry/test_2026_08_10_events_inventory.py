"""Feature pin (2026-08-10) — the events inventory (PRD §6.3 pipeline v1).

Owner review: "does Genie have calendars of events of every Bay Area
city?" — it didn't; discovery was one live search per plan. This is the
greenlit source-registry pipeline, seeded with the owner's own list
(city sites, Linden Tree, Home Depot kids workshops, touring shows,
Indian live music, flea markets).

THE PINS.
  * Registry: defaults ship the owner's categories; the vault overlay
    can add/override/disable WITHOUT a commit; a broken overlay
    degrades to defaults.
  * Ingest: search-kind extraction is hermetic (no wire without a
    seam), tolerant (garbage → zero events, never raises), and
    idempotent — refreshing twice converges.
  * Dedup: the same event from two sources merges on (title, date,
    city) — the PRD's blocking key.
  * Freshness: a FUTURE event that disappears from its source for two
    consecutive refreshes flips to 'suspect' and leaves default
    queries — "seen before, gone on re-crawl → mark suspect".
  * iCal parser handles folded lines + DTSTART/DTEND/SUMMARY/LOCATION.
  * Genie reads inventory FIRST: /whatson leads with the verified feed
    section; the concierge context carries the inventory block.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime

import pytest

_NOW = datetime(2026, 8, 12, 9, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("RAHAT_GENIE_LOCATION", "Testville, CA")
    monkeypatch.delenv("RAHAT_FAMILY_PROFILE_JSON", raising=False)
    monkeypatch.delenv("RAHAT_GENIE_STORE_JSON", raising=False)
    return tmp_path


def _search_llm(events):
    return lambda prompt: json.dumps({"events": events})


_LINDEN = {"title": "Author visit: picture-book reading",
           "start_ts": "2026-08-15 10:30:00",
           "venue": "Linden Tree Books", "city": "Los Altos",
           "url": "https://www.lindentreebooks.com/events/x"}
_DEPOT = {"title": "Kids Workshop: build a race car",
          "start_ts": "2026-08-15 09:00:00",
          "venue": "Home Depot San Jose", "city": "San Jose",
          "url": "https://homedepot.com/workshops"}


# ─────────────────────────── registry ───────────────────────────
def test_registry_ships_the_owners_categories(env):
    from bridges.events import registry
    importlib.reload(registry)
    sources = {s["id"] for s in registry.load_sources()}
    assert {"linden-tree", "home-depot-kids", "broadway-sj",
            "indian-live", "flea-markets", "mv-city",
            "sjpl"} <= sources


def test_overlay_adds_and_disables_without_commit(env, tmp_path):
    from bridges.events import registry
    overlay = {"sources": [{"id": "my-temple", "name": "Temple events",
                            "kind": "search", "url": "https://x.org",
                            "city": "San Jose", "categories": ["indian"]}],
               "disable": ["flea-markets"]}
    (tmp_path / "vault").mkdir(exist_ok=True)
    (tmp_path / "vault" / "event_sources.json").write_text(
        json.dumps(overlay))
    ids = {s["id"] for s in registry.load_sources()}
    assert "my-temple" in ids and "flea-markets" not in ids


def test_broken_overlay_degrades_to_defaults(env, tmp_path):
    from bridges.events import registry
    (tmp_path / "vault").mkdir(exist_ok=True)
    (tmp_path / "vault" / "event_sources.json").write_text("{not json")
    assert len(registry.load_sources()) == len(registry.DEFAULT_SOURCES)


# ─────────────────────────── ingest + store ───────────────────────────
def test_ingest_and_dedup_across_sources(env):
    from bridges.events.ingest import refresh_source
    from bridges.events.store import query_window
    src_a = {"id": "linden-tree", "kind": "search", "name": "LT",
             "url": "x", "city": "Los Altos", "categories": ["kids"]}
    src_b = {"id": "funcheap-sj", "kind": "search", "name": "FC",
             "url": "y", "city": "Bay Area", "categories": ["free"]}
    refresh_source(src_a, today=_NOW, llm=_search_llm([_LINDEN]))
    # Same event surfaces via the aggregator too — must MERGE.
    dup = dict(_LINDEN, city="Los Altos", url="https://funcheap/dup")
    refresh_source(src_b, today=_NOW, llm=_search_llm([dup, _DEPOT]))
    rows = query_window("2026-08-15", "2026-08-16")
    titles = [r["title"] for r in rows]
    assert titles.count(_LINDEN["title"]) == 1      # deduped
    assert _DEPOT["title"] in titles


def test_refresh_is_idempotent(env):
    from bridges.events.ingest import refresh_source
    from bridges.events.store import query_window
    src = {"id": "s", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=_NOW, llm=_search_llm([_DEPOT]))
    refresh_source(src, today=_NOW, llm=_search_llm([_DEPOT]))
    assert len(query_window("2026-08-15", "2026-08-15")) == 1


def test_garbage_and_hermetic_yield_zero_not_crash(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "s", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    assert refresh_source(src, today=_NOW,
                          llm=lambda p: "not json")["fetched"] == 0
    assert refresh_source(src, today=_NOW,
                          llm=None)["fetched"] == 0   # hermetic no-wire


def test_silent_cancellation_marks_suspect(env):
    from bridges.events.ingest import refresh_source
    from bridges.events.store import query_window
    src = {"id": "s", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 12, 7),
                   llm=_search_llm([_DEPOT]))
    # Two later refreshes where the (still-future) event is GONE.
    refresh_source(src, today=datetime(2026, 8, 12, 12),
                   llm=_search_llm([]))
    refresh_source(src, today=datetime(2026, 8, 12, 18),
                   llm=_search_llm([]))
    assert query_window("2026-08-15", "2026-08-15") == []
    assert len(query_window("2026-08-15", "2026-08-15",
                            include_suspect=True)) == 1


def test_ical_parser_minimal(env):
    from bridges.events.ingest import parse_ical
    ics = ("BEGIN:VCALENDAR\r\n"
           "BEGIN:VEVENT\r\n"
           "DTSTART;TZID=America/Los_Angeles:20260815T103000\r\n"
           "DTEND;TZID=America/Los_Angeles:20260815T113000\r\n"
           "SUMMARY:Toddler storytime with a very lo\r\n ng folded line\r\n"
           "LOCATION:Main Library\r\n"
           "END:VEVENT\r\n"
           "END:VCALENDAR")
    events = parse_ical(ics, {"city": "Mountain View",
                              "categories": ["library"]})
    assert len(events) == 1
    e = events[0]
    assert e["start_ts"] == "2026-08-15 10:30:00"
    assert "folded line" in e["title"]
    assert e["venue"] == "Main Library"


# ─────────────────────────── genie reads inventory ───────────────────────────
def test_whats_on_leads_with_verified_inventory(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "linden-tree", "kind": "search", "name": "LT", "url": "x",
           "city": "Los Altos", "categories": ["kids"]}
    refresh_source(src, today=_NOW, llm=_search_llm([_LINDEN]))
    from agents.genie import state, handler
    importlib.reload(state)
    importlib.reload(handler)
    fam = {"weather": {"saturday": "s", "sunday": "s"},
           "options": {"saturday": [
               {"time": "morning", "activity": "Farmers market",
                "place": "Main St", "why": "x", "source": "cm"}],
               "sunday": []}}
    out = handler.handle_whats_on(now=_NOW, llm=lambda p: json.dumps(fam))
    assert "From your event feeds" in out
    assert "Author visit" in out
    assert out.index("Author visit") < out.index("Farmers market"), (
        "verified inventory must lead; search extras follow")


def test_concierge_context_carries_inventory(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "s", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=_NOW, llm=_search_llm([_DEPOT]))
    from agents.genie import state, handler, concierge
    importlib.reload(state)
    importlib.reload(handler)
    importlib.reload(concierge)
    seen = {}

    def _spy(prompt):
        seen["prompt"] = prompt
        return json.dumps({"mode": "chat", "reply": "hi", "slots": {}})

    concierge.step("111", "hello", now=_NOW, llm=_spy)
    assert "VERIFIED LOCAL EVENTS" in seen["prompt"]
    assert "Kids Workshop" in seen["prompt"]
