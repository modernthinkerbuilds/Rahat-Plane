"""agents.bourdain.resolve — who, where, when, what (PRD §4), worked out
before every answer, with no model.

  * MEAL from the words, else from the clock.
  * PARTY from the words ("just me", "we"), else the trip's default.
  * WHERE: what was typed this turn → the location override (3 h) →
    the stay for today's leg → ask once.
  * LEG: the trip leg whose dates contain today; on a hand-over day
    the morning belongs to the leg you wake up in, the evening to the
    next (assumption logged for the PRD: hand-over at 15:00).

Dates go through Genie's production resolver (parse_explicit_date) —
the date-trap rule; no hand-rolled dates here.
"""
from __future__ import annotations

import re
from datetime import datetime

# meal word → canonical meal key (matches the seed's `meals` vocabulary)
MEAL_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coffee", ("coffee", "espresso", "latte", "flat white", "cappuccino",
                "cafe", "café", "caffeine")),
    ("dessert", ("dessert", "desserts", "gelato", "genaro", "ice cream",
                 "icecream", "sweet", "sweets", "cheesecake", "chesecake",
                 "cake", "cannoli", "sundae")),
    ("pastry", ("pastry", "pastries", "pasties", "croissant", "muffin",
                "bakery", "baked")),
    ("bagels", ("bagel", "bagels")),
    ("pizza", ("pizza", "slice", "pie")),
    ("thai", ("thai",)),
    ("burmese", ("burmese", "burma", "myanmar")),
    ("late", ("late night", "late-night", "late bite", "midnight")),
    ("breakfast", ("breakfast", "brakfast", "brunch", "morning")),
    ("lunch", ("lunch",)),
    ("dinner", ("dinner", "supper", "tonight")),
)
# Cravings the seed may not hold — an eat ask that goes to live search
# when nothing local fits (PRD pin 35). Free text, matched as words.
CUISINE_WORDS = (
    "sushi", "ramen", "udon", "japanese", "korean", "bibimbap", "chinese",
    "dim sum", "dumplings", "szechuan", "sichuan", "cantonese", "vietnamese",
    "pho", "banh mi", "indian", "dosa", "biryani", "mexican", "tacos",
    "taco", "burrito", "italian", "pasta", "greek", "mediterranean",
    "middle eastern", "falafel", "shawarma", "kebab", "turkish", "ethiopian",
    "french", "bistro", "steak", "steakhouse", "burger", "burgers", "bbq",
    "barbecue", "wings", "fried chicken", "seafood", "oysters", "lobster",
    "tapas", "spanish", "peruvian", "caribbean", "jamaican", "soup",
    "noodles", "salad", "vegan", "vegetarian", "deli", "sandwich",
    "sandwiches", "halal", "kosher", "brunch spot", "diner", "tea",
    "boba", "bubble tea", "juice", "smoothie", "chocolate", "cookies",
    "cookie", "donut", "donuts", "doughnut", "cupcake", "macaron",
    "waffle", "waffles", "pancakes", "crepes",
)


def cuisine_from_text(text: str) -> str | None:
    low = f" {(text or '').lower()} "
    for w in CUISINE_WORDS:
        if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
            return w
    return None


_EAT_RE = re.compile(r"\b(eat|eating|food|hungry|meal|restaurant|near me|"
                     r"from my list)\b", re.I)


def meal_from_text(text: str, now: datetime) -> tuple[str, bool]:
    """(meal, said) — `said` False when the clock decided."""
    low = f" {(text or '').lower()} "
    for meal, words in MEAL_WORDS:
        for w in words:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
                return meal, True
    h = now.hour + now.minute / 60
    if h < 5 or h >= 22:
        return "late", False
    if h < 10.5:
        return "breakfast", False
    if h < 14.5:
        return "lunch", False
    if h < 16.5:
        return "coffee", False
    return "dinner", False


def is_eat_ask(text: str) -> bool:
    low = (text or "").lower()
    if _EAT_RE.search(low):
        return True
    for _meal, words in MEAL_WORDS:
        if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)
               for w in words):
            return True
    return cuisine_from_text(low) is not None


# ── party ─────────────────────────────────────────────────────────────
_JUST_ME_RE = re.compile(r"\b(just me|only me|me alone|solo|by myself|"
                         r"i'?m alone|i alone)\b", re.I)
_WE_RE = re.compile(r"\b(we|us|my wife|wife and i|both of us|date night|"
                    r"the two of us|kids too|family)\b", re.I)


def party_from_text(text: str, default: str) -> tuple[str, bool]:
    """('me' | 'we', said). Baby doesn't eat; 'we' = owner + wife."""
    if _JUST_ME_RE.search(text or ""):
        return "me", True
    if _WE_RE.search(text or ""):
        return "we", True
    return default, False


# ── where ─────────────────────────────────────────────────────────────
_LOC_RE = re.compile(
    r"\b(?:i'?m|we'?re|were)?\s*(?:at|in|near|around|by)\s+(?:the\s+)?"
    r"(?P<loc>[A-Za-z0-9][A-Za-z0-9'&.\- ]{2,40}?)"
    r"(?=$|[,.!?]|\s+(?:and|for|tonight|now|please|any|where|what))", re.I)
_STAY_WORDS = ("hotel", "stay", "house", "home base", "airbnb", "the room",
               "where we're staying", "where we are staying")
CITY_WORDS = {
    "new_york": ("new york", "nyc", "manhattan", "brooklyn", "queens",
                 "chelsea", "midtown", "times square", "village", "soho",
                 "nolita", "tribeca", "harlem", "astoria", "flatiron",
                 "gramercy", "nomad", "hell's kitchen", "upper east",
                 "penn station", "herald square"),
    "boston": ("boston", "cambridge", "burlington", "lexington", "bedford",
               "arlington", "somerville", "watertown", "woburn",
               "wilmington", "allston", "north end", "south end",
               "back bay", "seaport", "porter square", "inman square",
               "central square"),
}


def city_from_text(text: str) -> str | None:
    low = (text or "").lower()
    for city, words in CITY_WORDS.items():
        if any(w in low for w in words):
            return city
    return None


def location_from_text(text: str) -> dict | None:
    """{'label', 'is_stay', 'city'} when the message names a place."""
    low = (text or "").lower()
    if any(w in low for w in _STAY_WORDS):
        return {"label": "the stay", "is_stay": True, "city": None}
    m = _LOC_RE.search(text or "")
    if not m:
        return None
    label = m.group("loc").strip()
    if label.lower() in ("me", "here", "us", "my list", "the list"):
        return None
    if any(label.lower().startswith(w) for w in ("morning", "evening",
                                                  "night", "dinner",
                                                  "lunch", "breakfast")):
        return None
    return {"label": label, "is_stay": False, "city": city_from_text(label)}


# ── leg ───────────────────────────────────────────────────────────────
_DATE_PAIR_RE = re.compile(r"([A-Za-z]{3})\s+([A-Za-z]{3})\s+(\d{1,2})")
HANDOVER_HOUR = 15


def leg_dates(stay: dict, now: datetime) -> tuple[datetime, datetime] | None:
    """'Mon Sep 21 – Thu Sep 24' → (Sep 21, Sep 24) via the production
    date resolver."""
    from agents.genie.handler import parse_explicit_date
    found = _DATE_PAIR_RE.findall(stay.get("dates") or "")
    if len(found) < 2:
        return None
    a = parse_explicit_date(f"{found[0][1]} {found[0][2]}", now)
    b = parse_explicit_date(f"{found[1][1]} {found[1][2]}", now)
    if a is None or b is None:
        return None
    return a, b


def leg_for(trip: dict, now: datetime) -> str | None:
    """The stay key whose dates contain `now`; hand-over day splits at
    HANDOVER_HOUR. None outside the trip."""
    stays = (trip or {}).get("stays") or {}
    ordered = []
    for key, stay in stays.items():
        rng = leg_dates(stay, now)
        if rng:
            ordered.append((rng[0], rng[1], key))
    ordered.sort()
    today = now.date()
    hit = [(a, b, k) for a, b, k in ordered if a.date() <= today <= b.date()]
    if not hit:
        return None
    if len(hit) == 1:
        return hit[0][2]
    # Hand-over day: the earlier leg keeps the morning.
    hit.sort()
    return hit[0][2] if now.hour < HANDOVER_HOUR else hit[-1][2]
