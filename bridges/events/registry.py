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
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SOURCES: list[dict] = [
    # ── Libraries (storytimes, author visits, kids workshops) ──
    # bibliocommons gateway RSS feeds: live, dated, server-rendered —
    # each verified by fetch 2026-08-24. One feed covers every branch.
    {"id": "sjpl", "name": "San José Public Library",
     "kind": "page",
     "url": "https://gateway.bibliocommons.com/v2/libraries/sjpl/rss/events",
     "city": "San Jose", "categories": ["kids", "library", "author"],
     "query_hint": "storytimes, author visits, kids programs, workshops"},
    {"id": "sccl-library", "name": "Santa Clara County Library District",
     "kind": "page",
     "url": "https://gateway.bibliocommons.com/v2/libraries/sccl/rss/events",
     "city": "Los Altos", "categories": ["kids", "library", "author"],
     "query_hint": "Los Altos, Cupertino, Campbell, Saratoga, Milpitas, "
                   "Gilroy branches — storytimes, kids programs, "
                   "workshops"},
    {"id": "paloalto-library", "name": "Palo Alto City Library",
     "kind": "page",
     "url": "https://gateway.bibliocommons.com/v2/libraries/paloalto/"
            "rss/events",
     "city": "Palo Alto", "categories": ["kids", "library", "author"],
     "query_hint": "storytimes, LEGO days, STEAM workshops, author "
                   "visits"},
    {"id": "mv-libcal", "name": "Mountain View Library",
     "kind": "search",       # LibCal is JS-only (verified 08-24) — no
                             # server-rendered surface; search until an
                             # iCal cid is found for the overlay.
     "url": "https://mountainview.libcal.com/calendar/libraryevents",
     "city": "Mountain View", "categories": ["kids", "library", "author"],
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
    {"id": "mv-city", "name": "City of Mountain View events",
     "kind": "page",         # server-rendered monthly calendar
                             # (verified 08-24; old /events/ URL 404s)
     "url": "https://www.mountainview.gov/whats-happening/events",
     "city": "Mountain View", "categories": ["city", "festival", "family"],
     "query_hint": "city special events, Music on Castro, Concerts on "
                   "the Plaza, movie nights, festivals, markets"},
    {"id": "sj-city", "name": "San José city & Visit San José events",
     "kind": "search",       # sanjose.org calendar is JS-rendered;
                             # grounded search indexes it well
     "url": "https://www.sanjose.org/events",
     "city": "San Jose", "categories": ["city", "festival", "family"],
     "query_hint": "San Jose events: downtown festivals, parks and "
                   "recreation programs, Christmas in the Park class "
                   "events, VivaCalleSJ"},
    {"id": "pa-city", "name": "City of Palo Alto events",
     "kind": "search",       # city moved to paloalto.gov (old
                             # cityofpaloalto.org URLs 404 — 08-24)
     "url": "https://www.paloalto.gov/Home/Calendar",
     "city": "Palo Alto", "categories": ["city", "family", "theatre"],
     "query_hint": "Palo Alto city events, children's theatre "
                   "productions, community festivals"},
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
     "query_hint": "Indian live music concerts, band performances, desi "
                   "cultural events, community acoustic jams, kirtan and "
                   "classical baithaks in the South Bay — include "
                   "Eventbrite, Partiful and Sulekha listings"},
    # ── Community music orgs (owner, 2026-08-30: Genie missed the SF
    # Indian Music Project's Aug 29 Saratoga acoustic jam) ──
    # Their own site is JS-only with no feed/API (verified 08-30), but
    # their events are indexed on Partiful / UNATION / Sulekha, so a
    # dedicated grounded search scoped to the org's known series
    # catches them. The 4-productive-miss suspect window keeps them
    # once found.
    {"id": "sfimp", "name": "SF Indian Music Project",
     "kind": "search", "url": "https://sfindianmusicproject.org",
     "city": "Bay Area", "categories": ["music", "indian", "jam",
                                        "family"],
     "query_hint": "SF Indian Music Project events and RSVPs (their "
                   "Partiful pages count): Acoustic Jam sessions, "
                   "concerts, Jam at The Commons SF, Jam at Spark "
                   "Social SF, and South Bay jams like the Saratoga "
                   "redwoods acoustic evening — include location, "
                   "start time, and whether free"},
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
