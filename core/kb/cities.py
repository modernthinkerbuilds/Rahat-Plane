"""core.kb.cities — the cities a household names in words, with a point
for each ("Los Altos", "in Berkeley", "downtown Oakland").

A message that is only a place has to move the origin without a model
and without the network: the table below is the fallback when the
knowledge base, its landmarks and the gazetteer don't know the words.
Centroids are approximate (a town's centre, not its edge), which is what
"about 55 min" and "nearest first" need. The same table gives the
gazetteer loader a city for rows that carry none (`nearest`).

Keys are what the knowledge base stores in `kb_location.city` and the
tenant's leg (`"los_altos"`, `"new_york"`): `key()` makes one from any
spelling, `label()` shows it back.
"""
from __future__ import annotations

import re

from core.kb import geo

# City centroids, Bay Area and the coast (name -> (lat, lng)); a row farther than CITY_MAX_KM from every
# centroid keeps no city in the gazetteer loader.
CITY_MAX_KM = 12.0
CITIES: dict[str, tuple[float, float]] = {
    "San Francisco": (37.7749, -122.4194), "Oakland": (37.8044, -122.2712), "Berkeley": (37.8716, -122.2727),
    "San Jose": (37.3382, -121.8863), "Santa Clara": (37.3541, -121.9552), "Sunnyvale": (37.3688, -122.0363),
    "Mountain View": (37.3861, -122.0839), "Palo Alto": (37.4419, -122.1430), "Menlo Park": (37.4530, -122.1817),
    "Redwood City": (37.4852, -122.2364), "San Carlos": (37.5072, -122.2605), "Belmont": (37.5202, -122.2758),
    "San Mateo": (37.5630, -122.3255), "Burlingame": (37.5841, -122.3661), "Millbrae": (37.5985, -122.3872),
    "South San Francisco": (37.6547, -122.4077), "Daly City": (37.6879, -122.4702), "Foster City": (37.5585, -122.2711),
    "Half Moon Bay": (37.4636, -122.4286), "Pacifica": (37.6138, -122.4869), "San Bruno": (37.6305, -122.4111),
    "Cupertino": (37.3230, -122.0322), "Los Altos": (37.3852, -122.1141), "Los Altos Hills": (37.3797, -122.1375),
    "Saratoga": (37.2638, -122.0230), "Los Gatos": (37.2358, -121.9624), "Campbell": (37.2872, -121.9500),
    "Milpitas": (37.4323, -121.8996), "Fremont": (37.5485, -121.9886), "Newark": (37.5297, -122.0402),
    "Union City": (37.5934, -122.0439), "Hayward": (37.6688, -122.0808), "San Leandro": (37.7249, -122.1561),
    "Alameda": (37.7652, -122.2416), "Emeryville": (37.8313, -122.2852), "Richmond": (37.9358, -122.3477),
    "El Cerrito": (37.9158, -122.3108), "Albany": (37.8869, -122.2978), "Walnut Creek": (37.9101, -122.0652),
    "Concord": (37.9780, -122.0311), "Pleasant Hill": (37.9480, -122.0608), "Lafayette": (37.8858, -122.1180),
    "Orinda": (37.8771, -122.1797), "Danville": (37.8216, -121.9999), "San Ramon": (37.7799, -121.9780),
    "Dublin": (37.7022, -121.9358), "Pleasanton": (37.6624, -121.8747), "Livermore": (37.6819, -121.7680),
    "Castro Valley": (37.6941, -122.0864), "Morgan Hill": (37.1305, -121.6544), "Gilroy": (37.0058, -121.5683),
    "Santa Cruz": (36.9741, -122.0308), "Capitola": (36.9752, -121.9533), "Scotts Valley": (37.0511, -122.0147),
    "Aptos": (36.9772, -121.8991), "Watsonville": (36.9102, -121.7569), "Sausalito": (37.8591, -122.4853),
    "Mill Valley": (37.9060, -122.5450), "San Rafael": (37.9735, -122.5311), "Novato": (38.1074, -122.5697),
    "Tiburon": (37.8735, -122.4566), "Brisbane": (37.6808, -122.3999), "Napa": (38.2975, -122.2869),
    "Vallejo": (38.1041, -122.2566), "Petaluma": (38.2324, -122.6367), "Santa Rosa": (38.4404, -122.7141),
    "Sonoma": (38.2919, -122.4580), "Monterey": (36.6002, -121.8947), "Carmel-by-the-Sea": (36.5552, -121.9233),
    "Pacific Grove": (36.6177, -121.9166), "Woodside": (37.4300, -122.2539), "Portola Valley": (37.3841, -122.2352),
    "Atherton": (37.4613, -122.1977), "East Palo Alto": (37.4688, -122.1411), "Martinez": (38.0194, -122.1341),
    "Benicia": (38.0494, -122.1586), "Pittsburg": (38.0280, -121.8847), "Antioch": (38.0049, -121.8058),
    "Brentwood": (37.9319, -121.6958), "San Pablo": (37.9621, -122.3455), "Piedmont": (37.8244, -122.2316),
    "Moraga": (37.8349, -122.1297), "Salinas": (36.6777, -121.6555), "Hollister": (36.8525, -121.4016),
}

# Cities the household's trip legs use that the table above doesn't cover: the key is what kb_location.city
# holds; the point is the city centre.
TRIP_CITIES: dict[str, tuple[float, float]] = {
    "new_york": (40.7549, -73.9840), "boston": (42.3601, -71.0589),
}

_LABELS = {"new_york": "New York", "boston": "Boston"}
_NOISE = re.compile(r"^\s*(?:downtown|central|old|the)\s+|\s+(?:downtown|area|proper)\s*$", re.I)
_PREFIX = re.compile(r"^\s*(?:(?:i'?m|i am|we'?re|we are|now|currently|still)\s+)?"
                     r"(?:(?:going|heading|headed|driving|on (?:my|our) way)\s+to\s+|(?:at|in|near|around|by|from)\s+)?", re.I)
# Words a bare place never carries: a verb, a meal, a question word, a reply.
_NOT_PLACE = re.compile(
    r"\b(?:eat|eating|coffee|lunch|dinner|breakfast|brunch|dessert|snack|food|open|closed|more|else|which|what|where|"
    r"when|how|why|yes|no|ok|okay|thanks|thank|please|go|going|save|list|help|hi|hello|here|we|us|me|my|our|it|that|"
    r"this|not|and|or|for|with|to|is|are|was|were|be|can|could|should|would|want|like|need)\b", re.I)


def key(name: str | None) -> str:
    """`"Los Altos"` → `"los_altos"`; `"NYC"` stays `"nyc"` (the caller maps aliases)."""
    return re.sub(r"[\s-]+", "_", (name or "").strip().lower().replace("é", "e"))


def label(city_key: str | None) -> str:
    k = (city_key or "").strip()
    if not k:
        return ""
    if k in _LABELS:
        return _LABELS[k]
    return " ".join(w.capitalize() if w not in ("by", "the", "of") else w for w in k.split("_"))


def point(name: str | None) -> tuple[float, float] | None:
    """The centroid for a city named in any spelling, or None."""
    k = key(_NOISE.sub("", name or ""))
    if not k:
        return None
    for city, pt in CITIES.items():
        if key(city) == k:
            return pt
    return TRIP_CITIES.get(k)


def find(text: str) -> tuple[str, tuple[float, float]] | None:
    """The city named inside a short text ("in Berkeley", "downtown Los Altos", "near Campbell"):
    the longest table entry present as whole words, with its point."""
    low = " " + re.sub(r"[^a-z0-9é' ]+", " ", (text or "").lower()).replace("é", "e") + " "
    best: tuple[str, tuple[float, float]] | None = None
    for city, pt in CITIES.items():
        c = " " + city.lower().replace("é", "e").replace("-", " ") + " "
        if c in low and (best is None or len(c) > len(best[0]) + 2):
            best = (city, pt)
    if best:
        return best
    for k, pt in TRIP_CITIES.items():
        if " " + k.replace("_", " ") + " " in low:
            return label(k), pt
    return None


def bare_place(text: str) -> str | None:
    """The place when a message is only a place ("Los Altos", "in Berkeley", "I'm at the Ferry Building",
    "near Castro St."): the words after any "I'm at / in / near / going to" lead-in, or None when the
    message says anything else (a verb, a meal, a question, a reply, more than five words)."""
    t = (text or "").strip().rstrip(".!")
    if not t or len(t) > 60 or "?" in t:
        return None
    rest = _PREFIX.sub("", t).strip()
    if not rest or len(rest.split()) > 5:
        return None
    if not re.match(r"^\w[\w'&.,\- ]*$", rest) or _NOT_PLACE.search(rest):
        return None
    return rest


def nearest(lat: float, lng: float, *, max_km: float = CITY_MAX_KM) -> str | None:
    """The table city nearest a point, within `max_km`, else None."""
    best, best_km = None, max_km
    for city, (clat, clng) in CITIES.items():
        km = geo.haversine_km(lat, lng, clat, clng)
        if km < best_km:
            best, best_km = city, km
    return best
