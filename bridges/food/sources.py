"""bridges.food.sources — the feeds the mentions reader follows (owner
decisions 2026-10-14/15). Every one is read in code, without a model.

Kinds: `rss` (RSS 2.0 or Atom), `hungryonion` (a Discourse category's
JSON topic list, whose titles carry the place and the area). A `note`
of "robots" marks a feed whose robots.txt asks automated readers to
stay out; the owner chose to read them, as with Genie's twenty, and the
daily line is the check.
"""
from __future__ import annotations

FOOD_SOURCES: list[dict] = [
    # ── verified from the VM on Oct 14 ──
    {"id": "sfist-food", "name": "SFist Food", "kind": "rss", "url": "https://sfist.com/food/rss/",
     "region": "sf", "full_text": True},
    {"id": "sfstandard-food", "name": "The San Francisco Standard Food & Drink", "kind": "rss",
     "url": "https://sfstandard.com/food-drink/feed/", "region": "sf"},
    {"id": "tablehopper", "name": "Tablehopper", "kind": "rss", "url": "https://feeds.feedburner.com/Tablehopper",
     "region": "sf", "full_text": True, "weekly": True},
    {"id": "7x7-eat", "name": "7x7 Eat + Drink", "kind": "rss", "url": "https://www.7x7.com/feeds/Eat-Drink.rss",
     "region": "bay", "full_text": True},
    {"id": "hungryonion-sfba", "name": "Hungry Onion, SF Bay Area", "kind": "hungryonion",
     "url": "https://www.hungryonion.org/c/regions/san-francisco-bay-area/9.json", "region": "bay"},
    # ── standard feeds the VM's fetcher could not open; the health line is the check ──
    {"id": "sfgate-food", "name": "SFGATE Food & Dining", "kind": "rss",
     "url": "https://www.sfgate.com/rss/feed/Food-Dining-550.php", "region": "bay"},
    {"id": "eater-sf", "name": "Eater San Francisco", "kind": "rss", "url": "https://sf.eater.com/rss/index.xml",
     "region": "bay"},
    # 2026-10-19: the Mercury News answers this reader 403 (its web firewall, whatever the User-Agent), with
    # no feed on another host; retired (FOOD_RETIRED). Metro Silicon Valley's food section covers the
    # South Bay instead (fetched with this reader: RSS 2.0, a few posts a month).
    {"id": "metro-food", "name": "Metro Silicon Valley, Food & Drink", "kind": "rss",
     "url": "https://www.metrosiliconvalley.com/food-drink/feed/", "region": "south-bay"},
    # ── robots.txt asks readers out; the owner chose to read them (2026-10-15) ──
    {"id": "berkeleyside-nosh", "name": "Berkeleyside Nosh", "kind": "rss",
     "url": "https://www.berkeleyside.org/category/nosh/feed", "region": "east-bay", "note": "robots"},
    {"id": "oaklandside-nosh", "name": "The Oaklandside, Nosh openings", "kind": "rss",
     "url": "https://oaklandside.org/tag/nosh-openings/feed/", "region": "east-bay", "note": "robots"},
    # 2026-10-19: /food/ became a landing page and its old feed loops on redirects; the section's articles
    # are the WordPress category "food" (from search results; not read from here, robots).
    {"id": "paloaltoonline-food", "name": "Palo Alto Online Food (the Peninsula Foodist)", "kind": "rss",
     "url": "https://www.paloaltoonline.com/category/food/feed/", "region": "peninsula", "note": "robots"},
    {"id": "reddit-sffood", "name": "Reddit r/SFFood", "kind": "rss", "url": "https://www.reddit.com/r/SFFood/new/.rss",
     "region": "sf", "note": "robots"},
]

# A general feed keeps only the items that are about eating out.
FOOD_WORDS = ("restaurant", "cafe", "café", "bakery", "bar ", "brunch", "dinner", "lunch", "taqueria", "pizza",
              "ramen", "sushi", "dim sum", "chef", "menu", "eat", "food", "wine", "cocktail", "dessert", "bistro",
              "opens", "opening", "closes", "closing", "dining", "kitchen", "noodle", "taco", "burger", "bbq", "barbecue")


# Feeds taken out, with why; a regression pin keeps them out.
FOOD_RETIRED = {
    "mercury-things": "2026-10-19: answers 403 to every reader; no feed on another host",
}


def by_id(source_id: str) -> dict | None:
    return next((s for s in FOOD_SOURCES if s["id"] == source_id), None)
