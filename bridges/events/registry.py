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
     "query_hint": "free and cheap events, kids & families category",
     "region": "south-bay"},
    # ══ Region expansion (owner, 2026-08-12): "I want genie to
    # recommend plans everywhere — northern SF, SF, East Bay, South
    # Bay, from Marin to Walnut Creek, Moraga to Big Sur." One anchor
    # source per region; the vault overlay adds more without commits. ══
    # ── San Francisco ──
    {"id": "funcheap-sf", "name": "Funcheap San Francisco",
     "kind": "search", "url": "https://sf.funcheap.com",
     "city": "San Francisco", "categories": ["free", "festival", "family"],
     "query_hint": "free and cheap SF events, festivals, street fairs, "
                   "kids & families", "region": "sf"},
    {"id": "sf-rec-parks", "name": "SF Recreation & Parks + city events",
     "kind": "search", "url": "https://sfrecpark.org",
     "city": "San Francisco", "categories": ["city", "family", "outdoor"],
     "query_hint": "Golden Gate Park events, playland, family programs, "
                   "museum free days", "region": "sf"},
    # ── North Bay / Marin ──
    {"id": "marin-events", "name": "Marin County events",
     "kind": "search", "url": "https://marinmommies.com",
     "city": "Marin", "categories": ["family", "outdoor", "festival"],
     "query_hint": "Marin County family events: Sausalito, Mill Valley, "
                   "San Rafael, Point Reyes — festivals, farms, markets",
     "region": "north-bay"},
    # ── East Bay (Oakland/Berkeley → Walnut Creek/Moraga) ──
    {"id": "eastbay-510", "name": "East Bay events (Oakland/Berkeley)",
     "kind": "search", "url": "https://www.visitoakland.com",
     "city": "Oakland", "categories": ["family", "festival", "culture"],
     "query_hint": "Oakland and Berkeley events: First Fridays, Fairyland, "
                   "Chabot, Lawrence Hall of Science, waterfront festivals",
     "region": "east-bay"},
    {"id": "eastbay-diablo", "name": "Walnut Creek / Lamorinda events",
     "kind": "search", "url": "https://www.walnutcreekdowntown.com",
     "city": "Walnut Creek", "categories": ["family", "market", "show"],
     "query_hint": "Walnut Creek, Moraga, Lafayette, Danville events: "
                   "downtown festivals, Lesher Center shows, farmers "
                   "markets", "region": "east-bay"},
    # ── Peninsula ──
    {"id": "peninsula-events", "name": "Peninsula events (Burlingame–RWC)",
     "kind": "search", "url": "https://www.redwoodcity.org/events",
     "city": "Redwood City", "categories": ["city", "family", "music"],
     "query_hint": "Peninsula events: Redwood City courthouse square "
                   "concerts, Burlingame, San Mateo, Half Moon Bay",
     "region": "peninsula"},
    # ── Santa Cruz ──
    {"id": "santa-cruz", "name": "Santa Cruz events",
     "kind": "search", "url": "https://www.santacruz.org/events",
     "city": "Santa Cruz", "categories": ["family", "outdoor", "beach"],
     "query_hint": "Santa Cruz events: Boardwalk, wharf, downtown, "
                   "Capitola — festivals, beach events, family days",
     "region": "santa-cruz"},
    # ── Monterey / Carmel / Big Sur ──
    {"id": "monterey-bigsur", "name": "Monterey · Carmel · Big Sur events",
     "kind": "search", "url": "https://www.seemonterey.com/events",
     "city": "Monterey", "categories": ["family", "outdoor", "coastal"],
     "query_hint": "Monterey Bay, Carmel, Pacific Grove, Big Sur events: "
                   "aquarium programs, coastal festivals, whale season",
     "region": "monterey"},
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
