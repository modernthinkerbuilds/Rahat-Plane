"""Event source registry — maintained data, not code (PRD §6.3).

Defaults below are the owner's own list (2026-08-10) + the source-yield
spike's findings. The vault overlay (vault/event_sources.json,
gitignored) can add/disable/override sources without a commit:

    {"sources": [{...same shape...}], "disable": ["source-id", ...]}

Source shape:
    id          stable slug
    name        human label
    kind        "ical" (RFC 5545 feed URL — precise, preferred) |
                "search" (site-scoped grounded LLM extraction)
    url         feed URL (ical) or site/page to scope the search to
    city        home city label
    categories  coarse tags the inventory filters on
    query_hint  extra guidance for search-kind extraction

Upgrade path: when a LibCal/city iCal URL is known, flip that source's
kind to "ical" in the overlay — precision beats extraction.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SOURCES: list[dict] = [
    # ── Libraries (storytimes, author visits, kids workshops) ──
    {"id": "sjpl", "name": "San José Public Library",
     "kind": "search", "url": "https://sjpl.bibliocommons.com/v2/events",
     "city": "San Jose", "categories": ["kids", "library", "author"],
     "query_hint": "storytimes, author visits, kids programs, workshops"},
    {"id": "mv-libcal", "name": "Mountain View Library",
     "kind": "search",
     "url": "https://mountainview.libcal.com/calendar/libraryevents",
     "city": "Mountain View", "categories": ["kids", "library", "author"],
     "query_hint": "library events calendar"},
    {"id": "linden-tree", "name": "Linden Tree Books",
     "kind": "search", "url": "https://www.lindentreebooks.com",
     "city": "Los Altos", "categories": ["kids", "author", "books"],
     "query_hint": "author visits, storytimes, book signings"},
    # ── City calendars ──
    {"id": "mv-city", "name": "City of Mountain View events",
     "kind": "search", "url": "https://www.mountainview.gov/events/",
     "city": "Mountain View", "categories": ["city", "festival", "family"],
     "query_hint": "city special events, festivals, markets"},
    {"id": "sj-city", "name": "City of San José events",
     "kind": "search", "url": "https://www.sanjoseca.gov",
     "city": "San Jose", "categories": ["city", "festival", "family"],
     "query_hint": "city events, parks and recreation programs"},
    {"id": "pa-city", "name": "City of Palo Alto events",
     "kind": "search", "url": "https://www.cityofpaloalto.org",
     "city": "Palo Alto", "categories": ["city", "family", "theatre"],
     "query_hint": "city events, children's theatre productions"},
    # ── Retail workshops ──
    {"id": "home-depot-kids", "name": "Home Depot Kids Workshops",
     "kind": "search", "url": "https://www.homedepot.com/workshops",
     "city": "Bay Area", "categories": ["kids", "workshop"],
     "query_hint": "free kids workshop first Saturday, Bay Area stores"},
    # ── Live shows & performances ──
    {"id": "broadway-sj", "name": "Broadway San José",
     "kind": "search", "url": "https://broadwaysanjose.com",
     "city": "San Jose", "categories": ["show", "theatre", "family"],
     "query_hint": "touring Broadway shows and dates"},
    {"id": "pa-childrens-theatre", "name": "Palo Alto Children's Theatre",
     "kind": "search", "url": "https://www.cityofpaloalto.org",
     "city": "Palo Alto", "categories": ["kids", "theatre", "show"],
     "query_hint": "children's theatre productions like Three Little Pigs"},
    {"id": "indian-live", "name": "Indian music & desi live events (South Bay)",
     "kind": "search", "url": "https://www.sulekha.com",
     "city": "Bay Area", "categories": ["music", "indian", "show"],
     "query_hint": "Indian live music concerts, band performances, "
                   "desi cultural events in the South Bay"},
    # ── Markets ──
    {"id": "flea-markets", "name": "Bay Area flea markets",
     "kind": "search", "url": "https://www.sjfm.com",
     "city": "Bay Area", "categories": ["market", "family"],
     "query_hint": "flea markets: San Jose Flea Market, De Anza, Alameda "
                   "Point Antiques Faire dates"},
    # ── Aggregators ──
    {"id": "funcheap-sj", "name": "Funcheap South Bay",
     "kind": "search", "url": "https://sf.funcheap.com/region/san-jose/",
     "city": "Bay Area", "categories": ["free", "festival", "family"],
     "query_hint": "free and cheap events, kids & families category"},
]


def _overlay_path() -> Path:
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox) / "event_sources.json"
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")) / "event_sources.json"


def load_sources() -> list[dict]:
    """Defaults + vault overlay (add/override by id, disable by id).
    Never raises — a broken overlay logs and yields the defaults."""
    sources = {s["id"]: dict(s) for s in DEFAULT_SOURCES}
    path = _overlay_path()
    if path.exists():
        try:
            overlay = json.loads(path.read_text())
            for s in overlay.get("sources") or []:
                if isinstance(s, dict) and s.get("id"):
                    sources[s["id"]] = {**sources.get(s["id"], {}), **s}
            for sid in overlay.get("disable") or []:
                sources.pop(sid, None)
        except Exception as e:  # noqa: BLE001
            logger.warning("event_sources.json overlay broken (%s) — "
                           "using defaults", e)
    return list(sources.values())
