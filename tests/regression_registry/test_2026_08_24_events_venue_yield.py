"""Feature pin (2026-08-24) — venue-yield rework: page-kind extraction,
productive-refresh suspect clock, fitness sources.

Owner, verbatim: "the genie isn't picking up events from sites like
linden, home depot, local libraries, Bay Area city sites, and linden
like private places. Can you please update genie's pipeline to pick up
those? I'd also like fitness events if available."

Live diagnosis (2026-08-24, vault/rahat.db): every source refreshed
3×/day on schedule, but grounded-SEARCH extraction had near-zero recall
on venue/library/city calendar sites (mv-libcal: zero rows ever;
linden-tree: 9 rows in two weeks) — and the two-any-refresh suspect
rule then erased what little arrived (1,123 suspect vs 571 active;
SJPL's entire future storytime calendar suspect; linden-tree, mv-city,
broadway-sj: zero active future events).

THE PINS.
  * "page" fetch kind: GET the source URL (events page or public RSS
    feed), strip to text, LLM-extract from THAT text — no search
    grounding, ~deterministic recall. Hermetic under tests (no wire
    without both the http and llm seams), tolerant (garbage → zero
    events, never raises).
  * Registry: the surfaces verified by fetch on 2026-08-24 are
    page-kind — Linden Tree events calendar, the three bibliocommons
    library RSS feeds (SJPL, Santa Clara County, Palo Alto), and the
    Mountain View city calendar. Fitness sources exist and carry the
    "fitness" category.
  * Suspect clock: only PRODUCTIVE refreshes (fetched > 0) advance it
    (pinned in detail in test_2026_08_10_events_inventory.py).
  * Fitness events flow to the digest with no extra plumbing: an
    active fitness event in the weekend window is advertised.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("RAHAT_EVENTS_DB", str(tmp_path / "events.db"))
    return tmp_path


_NOW = datetime(2026, 8, 12, 9, 0)          # Wed → weekend of Aug 15
_LINDEN_HTML = """<html><head><title>Events</title>
<script>var junk = 1;</script></head><body>
<h1>Events Calendar</h1>
<div class="event"><h3>Storytime with Alison Kim</h3>
<p>Saturday, August 15 at 10:30am — picture book reading.</p></div>
<div class="event"><h3>Middle Grade Book Club</h3>
<p>Sunday, August 16 at 4pm — ages 9+.</p></div>
</body></html>"""


def _llm(events):
    return lambda prompt: json.dumps({"events": events})


# ── the page kind ─────────────────────────────────────────────────────
def test_page_kind_fetches_extracts_and_stores(env):
    from bridges.events.ingest import refresh_source
    from bridges.events.store import query_window
    seen = {}

    def _http(url):
        seen["url"] = url
        return _LINDEN_HTML

    def _extract(prompt):
        seen["prompt"] = prompt
        return json.dumps({"events": [
            {"title": "Storytime with Alison Kim",
             "start_ts": "2026-08-15 10:30:00",
             "venue": "Linden Tree Books", "city": "Los Altos"}]})

    src = {"id": "linden-tree", "kind": "page", "name": "Linden Tree",
           "url": "https://www.lindentreebooks.com/events-calendar/",
           "city": "Los Altos", "categories": ["kids", "books"]}
    counts = refresh_source(src, today=_NOW, llm=_extract, http=_http)
    assert counts["fetched"] == 1 and counts["added"] == 1
    assert seen["url"] == src["url"]
    # The prompt carries the PAGE TEXT, stripped of markup/scripts.
    assert "Storytime with Alison Kim" in seen["prompt"]
    assert "<div" not in seen["prompt"] and "var junk" not in seen["prompt"]
    rows = query_window("2026-08-15", "2026-08-15")
    assert rows and rows[0]["title"] == "Storytime with Alison Kim"
    assert "kids" in rows[0]["categories"]


def test_page_kind_is_hermetic_and_tolerant(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "p", "kind": "page", "name": "P",
           "url": "https://x.example/events", "city": "Los Altos",
           "categories": []}
    # No seams under RAHAT_TEST_MODE → zero events, no wire, no raise.
    assert refresh_source(src, today=_NOW)["fetched"] == 0
    # Garbage LLM output → zero events, never raises.
    assert refresh_source(src, today=_NOW, llm=lambda p: "not json",
                          http=lambda u: "<p>hi</p>")["fetched"] == 0
    # Empty page → zero events without an LLM call.
    assert refresh_source(src, today=_NOW,
                          llm=lambda p: (_ for _ in ()).throw(
                              AssertionError("llm must not be called")),
                          http=lambda u: "")["fetched"] == 0


# ── the registry (the owner's named surfaces) ─────────────────────────
def test_verified_venue_sources_are_page_kind(env):
    from bridges.events.registry import load_sources
    by_id = {s["id"]: s for s in load_sources()}
    for sid, url_frag in (
            ("linden-tree", "lindentreebooks.com/events-calendar"),
            ("sjpl", "bibliocommons.com/v2/libraries/sjpl/rss/events"),
            ("sccl-library", "libraries/sccl/rss/events"),
            ("paloalto-library", "libraries/paloalto/rss/events"),
            ("mv-city", "mountainview.gov/whats-happening/events")):
        assert by_id[sid]["kind"] == "page", sid
        assert url_frag in by_id[sid]["url"], sid


def test_community_music_org_source_exists(env):
    """Owner, 2026-08-30: Genie missed the SF Indian Music Project's
    Saratoga acoustic jam. The org's site is JS-only with no feed
    (verified by fetch), so this is a dedicated grounded-search source
    whose hint names their real event series — pin it so the source
    can't be dropped in a registry cleanup."""
    from bridges.events.registry import load_sources
    by_id = {s["id"]: s for s in load_sources()}
    sfimp = by_id["sfimp"]
    assert sfimp["kind"] == "search"
    for series in ("Acoustic Jam", "Spark Social", "Saratoga"):
        assert series in sfimp["query_hint"], series
    assert "indian" in sfimp["categories"]


def test_fitness_sources_exist_with_fitness_category(env):
    from bridges.events.registry import load_sources
    fit = [s for s in load_sources()
           if "fitness" in (s.get("categories") or [])]
    assert len(fit) >= 2
    assert {"bayarea-races", "bayarea-fitness"} <= {s["id"] for s in fit}


# ── dedup hardening that rode along ───────────────────────────────────
def test_accented_city_variants_merge_not_duplicate(env):
    """"San José" vs "San Jose" were hashing to different event keys —
    every SJPL row arriving both ways duplicated (live, 08-24)."""
    from bridges.events.store import event_key
    assert (event_key("Baby Lapsit Storytime", "2026-08-25 11:00:00",
                      "San José")
            == event_key("Baby Lapsit Storytime", "2026-08-25 11:00:00",
                         "San Jose"))


# ── one-tap event links (owner, 2026-08-30: "just say here") ──────────
def test_digest_lines_end_in_a_here_link(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 11, 7), llm=_llm([
        {"title": "Kids Workshop", "start_ts": "2026-08-15 09:00:00",
         "city": "San Jose",
         "url": "https://homedepot.com/workshops?loc=SJ (South)"},
        {"title": "Berryessa Flea Market",
         "start_ts": "2026-08-15 00:00:00", "city": "San Jose"}]))
    from bridges.events.digest import build_digest
    out = build_digest(_NOW)
    # Linked event: short [here](url), with ')' and spaces encoded so
    # Telegram's legacy Markdown can't truncate the link.
    assert ("Kids Workshop (San Jose) — "
            "[here](https://homedepot.com/workshops?loc=SJ%20%28South"
            not in out)                       # '(' need not be encoded…
    assert "[here](https://homedepot.com/workshops?loc=SJ%20(South%29)"         in out
    # No url → no dangling link.
    assert "Flea Market (San Jose) — [here]" not in out


def test_whatson_lines_carry_the_same_link(env):
    # Fixed clock + fixed seed dates (the date-trap rule): _NOW is Wed
    # Aug 12, whose weekend is Aug 15-16 — same pattern as the 08-10
    # whats-on pins.
    from bridges.events.ingest import refresh_source
    src = {"id": "seed", "kind": "search", "name": "S", "url": "x",
           "city": "San Jose", "categories": []}
    refresh_source(src, today=datetime(2026, 8, 11, 7), llm=_llm([
        {"title": "Author visit", "start_ts": "2026-08-15 10:30:00",
         "city": "Los Altos",
         "url": "https://www.lindentreebooks.com/events-calendar/"}]))
    import importlib
    from agents.genie import handler
    importlib.reload(handler)
    out = handler.handle_whats_on(now=_NOW)
    assert ("[here](https://www.lindentreebooks.com/events-calendar/)"
            in out)


# ── fitness reaches the digest ────────────────────────────────────────
def test_fitness_event_shows_up_in_the_weekend_digest(env):
    from bridges.events.ingest import refresh_source
    src = {"id": "bayarea-races", "kind": "search", "name": "Races",
           "url": "x", "city": "Bay Area",
           "categories": ["fitness", "run"]}
    refresh_source(src, today=datetime(2026, 8, 11, 7), llm=_llm([
        {"title": "Los Gatos Dammit Run 5K",
         "start_ts": "2026-08-15 08:00:00", "city": "Los Gatos"}]))
    from bridges.events.digest import build_digest
    out = build_digest(_NOW)
    assert out and "Dammit Run" in out
