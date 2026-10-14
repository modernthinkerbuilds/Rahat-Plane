"""Event source registry — maintained data, not code (PRD §6.3).

Defaults below are the owner's own list (2026-08-10) + the source-yield
spike's findings. The vault overlay (vault/event_sources.json,
gitignored) can add/disable/override sources without a commit:

    {"sources": [{...same shape...}], "disable": ["source-id", ...]}

Source shape:
    id          stable slug
    name        human label
    kind        "ical" (RFC 5545 feed URL — precise, preferred) |
                "page" (fetch the URL directly — an events page or RSS
                feed — and LLM-extract from its actual text; recall is
                ~deterministic because the whole page is in-context) |
                "search" (site-scoped grounded LLM extraction — last
                resort; recall flaps per refresh, see 2026-08-24)
    url         feed URL (ical/page) or site/page to scope the search to
    city        home city label
    categories  coarse tags the inventory filters on
    query_hint  extra guidance for search/page-kind extraction

2026-08-24 (owner: "genie isn't picking up events from sites like
linden, home depot, local libraries, bay area city sites"): the live
yield table showed why — grounded SEARCH extraction has near-zero
recall on small venue/library/city calendar sites (mv-libcal: zero
rows ever; linden-tree: 9 rows in two weeks; sjpl's real storytimes
all decayed to suspect). Every source below whose events live on a
server-rendered page or a public RSS feed (verified by fetch on
2026-08-24) is now kind "page"; search remains only where the surface
is JS-only or genuinely needs discovery across a whole region.

Upgrade path: when a LibCal/city iCal URL is known, flip that source's
kind to "ical" in the overlay — precision beats extraction.

2026-10-14 (owner: "none of the events show up … find the right set of
feeds"): the upgrade happened. The library sources read their platforms'
own calendars in code (bridges/events/readers.py) instead of a 25-item
RSS through a model; sources_bayarea.py adds the libraries, family
venues, city pages and culture venues of the whole Bay, each verified by
fetch; the dead sources (the Mountain View city page, 403 since August;
Visit San José and Broadway San José by search; the Monterey search; the
SF Indian Music Project search) are replaced by direct reads of what
works. The research is claude/genie_event_feeds_2026-10-11.md.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bridges.events.sources_bayarea import BAY_AREA_SOURCES, _bc, _libcal

logger = logging.getLogger(__name__)

DEFAULT_SOURCES: list[dict] = [
    # ── Libraries (storytimes, author visits, kids workshops) ──
    # bibliocommons gateway RSS feeds: live, dated, server-rendered —
    # each verified by fetch 2026-08-24. One feed covers every branch.
    # 2026-10-14: the whole calendar through the platform's JSON (the RSS
    # held 25 items, two days), family audiences only for the big systems.
    {"id": "sjpl", "name": "San José Public Library",
     "kind": "bibliocommons", "url": _bc("sjpl"),
     "city": "San Jose", "categories": ["library", "author"],
     "query_hint": "storytimes, author visits, kids programs, workshops"},
    {"id": "sccl-library", "name": "Santa Clara County Library District",
     "kind": "bibliocommons", "url": _bc("sccl"),
     "city": "Los Altos", "categories": ["library", "author"],
     "query_hint": "Los Altos, Cupertino, Campbell, Saratoga, Milpitas, "
                   "Gilroy branches — storytimes, kids programs, "
                   "workshops"},
    {"id": "paloalto-library", "name": "Palo Alto City Library",
     "kind": "bibliocommons", "url": _bc("paloalto"),
     "city": "Palo Alto", "categories": ["library", "author"],
     "query_hint": "storytimes, LEGO days, STEAM workshops, author "
                   "visits"},
    {"id": "mv-libcal", "name": "Mountain View Library",
     "kind": "libcal",       # 2026-10-14: the calendar's own JSON (cid
                             # 8800); search found 5 events in 119 tries.
     "url": _libcal("mountainview", "8800"),
     "city": "Mountain View", "venue": "Mountain View Public Library",
     "categories": ["library", "author"],
     "query_hint": "Mountain View Public Library events: storytimes, "
                   "kids programs, maker workshops"},
    {"id": "linden-tree", "name": "Linden Tree Books",
     "kind": "page",         # events calendar is server-rendered with
                             # 15+ dated events (verified 08-24)
     "url": "https://www.lindentreebooks.com/events-calendar/",
     "city": "Los Altos", "categories": ["kids", "author", "books"],
     "query_hint": "author visits, storytimes, book clubs, writing "
                   "workshops, book signings"},
    # ── City calendars ──
    # mv-city (the City of Mountain View page) was retired 2026-10-14: 403
    # on every refresh since 2026-08-24 (149 tries, 0 events), and the
    # page it serves a browser is a stale March grid. The library's own
    # calendar (mv-libcal) carries the city's family programs.
    {"id": "sj-city", "name": "Visit San José upcoming events",
     "kind": "page",         # 2026-10-14: the page's own "Upcoming
                             # Events" block (the search found nothing
                             # after 2026-09-16)
     "url": "https://www.sanjose.org/events",
     "city": "San Jose", "categories": ["city", "festival", "family", "show"],
     "query_hint": "the Upcoming Events block: downtown festivals, city "
                   "theatre shows, Christmas in the Park, VivaCalleSJ"},
    {"id": "pa-city", "name": "City of Palo Alto events directory",
     "kind": "page",         # 2026-10-14: the directory's first page is
                             # server-rendered (about five days)
     "url": "https://www.paloalto.gov/Events-Directory",
     "city": "Palo Alto", "categories": ["city", "family", "theatre"],
     "query_hint": "city events, Junior Museum & Zoo events, children's "
                   "theatre, community festivals; skip meetings"},
    # ── Retail workshops ──
    {"id": "home-depot-kids", "name": "Home Depot Kids Workshops",
     "kind": "search",       # workshops page is a JS app; the program
                             # itself is well-indexed
     "url": "https://www.homedepot.com/workshops",
     "city": "Bay Area", "categories": ["kids", "workshop"],
     "query_hint": "free kids workshop — traditionally the FIRST "
                   "SATURDAY of each month, 9am-noon, at Bay Area "
                   "stores (San Jose, Mountain View, Sunnyvale, East "
                   "Palo Alto); find the next dates and the month's "
                   "build project"},
    # ── Live shows & performances ──
    {"id": "broadway-sj", "name": "Broadway San José",
     "kind": "page",         # 2026-10-14: the homepage lists every show
                             # with its run dates (the search found 3
                             # shows in 119 tries)
     "url": "https://broadwaysanjose.com/",
     "city": "San Jose", "venue": "San José Center for the Performing Arts",
     "categories": ["show", "theatre", "family", "culture"],
     "query_hint": "touring shows and their run dates (one event per show, "
                   "on its first date)"},
    {"id": "pa-childrens-theatre", "name": "Palo Alto Children's Theatre",
     "kind": "search", "url": "https://www.cityofpaloalto.org",
     "city": "Palo Alto", "categories": ["kids", "theatre", "show"],
     "query_hint": "children's theatre productions like Three Little Pigs"},
    {"id": "indian-live", "name": "Indian music & desi live events (South Bay)",
     "kind": "search", "url": "https://www.sulekha.com",
     "city": "Bay Area", "categories": ["music", "indian", "show"],
     "query_hint": "Indian live music concerts, band performances, desi "
                   "cultural events, community acoustic jams, kirtan and "
                   "classical baithaks in the South Bay — include "
                   "Eventbrite, Partiful and Sulekha listings"},
    # ── Community music orgs (owner, 2026-08-30: Genie missed the SF
    # Indian Music Project's Aug 29 Saratoga acoustic jam) ──
    # sfimp, a grounded search for the org's Partiful / UNATION / Sulekha
    # listings, was retired 2026-10-14: 5 events in 63 tries, nothing
    # since 2026-09-16, and none of those sites offers a feed. The
    # India Currents calendar (sources_bayarea: india-currents) is the
    # Bay-wide South Asian source with a feed.
    # ── Markets ──
    {"id": "flea-markets", "name": "Bay Area flea markets",
     "kind": "search", "url": "https://www.sjfm.com",
     "city": "Bay Area", "categories": ["market", "family"],
     "query_hint": "flea markets: San Jose Flea Market, De Anza, Alameda "
                   "Point Antiques Faire dates"},
    # ── Fitness (owner, 2026-08-24: "I'd also like fitness events") ──
    {"id": "bayarea-races", "name": "Bay Area running races & fun runs",
     "kind": "search", "url": "https://runsignup.com",
     "city": "Bay Area", "categories": ["fitness", "run", "outdoor"],
     "query_hint": "5K, 10K, half marathon, trail races, fun runs and "
                   "kids runs in the South Bay and Peninsula (San Jose, "
                   "Mountain View, Palo Alto, Santa Clara, Los Gatos) — "
                   "registration-open races with dates"},
    {"id": "bayarea-fitness", "name": "Bay Area fitness & wellness events",
     "kind": "search", "url": "https://www.eventbrite.com",
     "city": "Bay Area", "categories": ["fitness", "wellness"],
     "query_hint": "fitness events in the South Bay: yoga in the park, "
                   "outdoor bootcamps, community CrossFit competitions, "
                   "hike meetups, cycling events, wellness fairs"},
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
    # monterey-bigsur (a search of seemonterey.com) was retired 2026-10-14:
    # nothing since 2026-09-16, and the site's own RSS carries no event
    # dates. The Monterey Public Library's calendar (sources_bayarea:
    # montereypl) is the coast's feed; a Big Sur trip is planned by the
    # concierge's live search.
] + BAY_AREA_SOURCES


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
