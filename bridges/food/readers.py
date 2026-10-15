"""bridges.food.readers — feed items and the places they name, in code.

`parse_rss` reads RSS 2.0 and Atom with the standard library;
`parse_hungryonion` reads a Discourse category's JSON topic list. From
an item, `mentions_from` finds the eating places it names, three ways:

  1. Hungry Onion's title convention: "June's Pizza [West Oakland]",
     "Ludwig's (Mountain View)", "[Oakland Chinatown] Imperial Soup - …".
  2. Headline shapes: "X opens in Y", "X is closing after 15 years",
     "Inside X, the new …".
  3. Names the knowledge base or the gazetteer already knows, matched
     as whole phrases in the title and text (a `NameIndex`).

Every mention carries where it was read (the item's URL and date), a
short quote, and a kind: opening, closing, closed, list or mention. No
model reads any of this; the text is data.
"""
from __future__ import annotations

import html as _html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

from core.fetch import html_to_text
from core.kb import schema as kb

MAX_TEXT = 20_000

# A city or neighbourhood word in a Hungry Onion title, so "[San Francisco, North Beach]" and "(Mountain View)"
# resolve to a city the gazetteer knows. Suffix and prefix forms cover "West Oakland" and "Oakland Chinatown".
BAY_CITIES = ("san francisco", "oakland", "berkeley", "san jose", "santa clara", "sunnyvale", "mountain view", "palo alto",
              "menlo park", "redwood city", "san carlos", "belmont", "san mateo", "burlingame", "millbrae", "daly city",
              "south san francisco", "cupertino", "los altos", "saratoga", "los gatos", "campbell", "milpitas", "fremont",
              "newark", "union city", "hayward", "san leandro", "alameda", "emeryville", "richmond", "el cerrito",
              "albany", "walnut creek", "concord", "lafayette", "orinda", "danville", "san ramon", "dublin",
              "pleasanton", "livermore", "castro valley", "morgan hill", "gilroy", "santa cruz", "capitola",
              "sausalito", "mill valley", "san rafael", "novato", "tiburon", "pacifica", "san bruno", "half moon bay",
              "napa", "sonoma", "petaluma", "santa rosa", "monterey", "carmel", "foster city", "east palo alto",
              "piedmont", "brisbane", "sebastopol", "healdsburg", "vallejo", "benicia", "martinez", "pleasant hill")
_SF_ALIASES = {"sf": "san francisco", "the city": "san francisco", "soma": "san francisco", "the mission": "san francisco"}


@dataclass
class Item:
    title: str
    url: str
    published: str | None = None          # "YYYY-MM-DD"
    summary: str = ""
    content: str = ""                     # full text when the feed carries it
    categories: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def text(self) -> str:
        """The item's words as plain text: title, summary, body; HTML stripped; bounded."""
        parts = [self.title]
        for raw in (self.summary, self.content):
            if raw:
                t, _ = html_to_text(raw) if "<" in raw else (raw, "")
                parts.append(t)
        return "\n".join(p for p in parts if p)[:MAX_TEXT]


@dataclass
class Mention:
    name: str
    kind: str                             # opening | closing | closed | list | mention
    city: str | None = None
    neighborhood: str | None = None
    quote: str = ""
    how: str = ""                         # title_convention | headline | known_name
    confidence: float = 0.6

    @property
    def name_norm(self) -> str:
        return kb.norm_name(self.name)


# ── feeds ───────────────────────────────────────────────────────────────
def _text_of(el, *names: str) -> str:
    for n in names:
        for child in el:
            tag = child.tag.split("}")[-1]
            if tag == n and (child.text or "").strip():
                return (child.text or "").strip()
    return ""


def _date_of(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def parse_rss(text: str) -> list[Item]:
    """RSS 2.0 or Atom → items. A broken document yields what was parsed."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        # a stray control character or an unescaped ampersand: salvage the item blocks
        text = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError:
            return []
    items: list[Item] = []
    tag = root.tag.split("}")[-1]
    if tag == "feed":                                                   # Atom
        for e in root:
            if e.tag.split("}")[-1] != "entry":
                continue
            link = ""
            for l in e:
                if l.tag.split("}")[-1] == "link" and (l.get("rel") in (None, "alternate")):
                    link = l.get("href") or ""
                    break
            cats = [c.get("term") or "" for c in e if c.tag.split("}")[-1] == "category"]
            items.append(Item(title=_html.unescape(_text_of(e, "title")), url=link,
                              published=_date_of(_text_of(e, "published", "updated")),
                              summary=_text_of(e, "summary"), content=_text_of(e, "content"),
                              categories=[c for c in cats if c]))
        return items
    channel = root.find("channel") if tag == "rss" else root
    if channel is None:
        return []
    for it in channel:
        if it.tag.split("}")[-1] != "item":
            continue
        content = ""
        for c in it:
            if c.tag.split("}")[-1] == "encoded":                       # content:encoded
                content = (c.text or "").strip()
        cats = [(c.text or "").strip() for c in it if c.tag.split("}")[-1] == "category"]
        items.append(Item(title=_html.unescape(_text_of(it, "title")), url=_text_of(it, "link", "guid"),
                          published=_date_of(_text_of(it, "pubDate", "date")), summary=_text_of(it, "description"),
                          content=content, categories=[c for c in cats if c]))
    return items


def parse_hungryonion(text: str, *, base_url: str = "https://www.hungryonion.org") -> list[Item]:
    """A Discourse category's `.json` topic list → items (the pinned About topic left out)."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return []
    out: list[Item] = []
    for t in ((data.get("topic_list") or {}).get("topics") or []):
        title = _html.unescape(str(t.get("title") or "")).strip()
        if not title or t.get("pinned") or title.lower().startswith("about the "):
            continue
        out.append(Item(title=title, url=f"{base_url}/t/{t.get('slug')}/{t.get('id')}",
                        published=_date_of(str(t.get("last_posted_at") or t.get("created_at") or "")),
                        summary=_html.unescape(str(t.get("excerpt") or "")),
                        extra={"posts": t.get("posts_count"), "views": t.get("views"), "created": _date_of(str(t.get("created_at") or ""))}))
    return out


# ── the names in an item ────────────────────────────────────────────────
_HO_TITLE_RES = (
    re.compile(r"^(?P<name>[^\[\(]{2,80}?)\s*\[(?P<where>[^\]]{2,60})\]"),
    re.compile(r"^(?P<name>[^\[\(]{2,80}?)\s*\((?P<where>[^\)]{2,60})\)\s*$"),
    re.compile(r"^\[(?P<where>[^\]]{2,60})\]\s*(?P<name>[^-–—:]{2,80})"),
)
_OPEN_WORDS = r"(?i:opens|opened|opening|to open|will open|soft-opens|debuts|arrives|lands|launches|comes to|is coming to|has opened|now open)"
_CLOSE_WORDS = r"(?i:closes|closed|closing|to close|will close|shutters|shuttered|has closed|is closing|permanently closed|shutting down|shuts)"
_NAME = r"(?P<name>[A-Z][\w'’&.!\-]*(?:\s+(?:[A-Z&][\w'’&.!\-]*|de|la|del|di|da|y|and|the|of|&))*?)"
_TAIL = r"(?:,\s*[^,]{0,60}?)?(?:\s+(?i:at|in|on|near)\s+[^,]{1,40}?)?"
_VERB_LEAD = r"\s+(?:(?i:is|are|finally|officially|has|have)\s+)?(?:(?i:now)\s+)?"
_HEADLINE_OPEN_RE = re.compile(r"^(?:Inside\s+)?" + _NAME + _TAIL + _VERB_LEAD + _OPEN_WORDS + r"\b")
_HEADLINE_CLOSE_RE = re.compile(r"^" + _NAME + _TAIL + _VERB_LEAD + _CLOSE_WORDS + r"\b")
_NAME_TRIM_RE = re.compile(r"(?:['’]s)?\s+(?:SF|San Francisco|Oakland|Berkeley|East Bay|Peninsula|South Bay)?\s*(?:location|outpost|branch|spot|restaurant|shop)\b.*$", re.I)
_NAME_CUT_RE = re.compile(r"\s+(?:at|in|on|near|to|for|with|after|from)\s+[A-Z].*$")
_LIST_RE = re.compile(r"\b(best|top \d+|guide|essential|where to|the \d+ |favorite|favourite|roundup|our picks|big eat)\b", re.I)
_CLOSED_PAST_RE = re.compile(r"\b(has closed|closed (?:its doors|for good|permanently)|permanently closed|shuttered|is now closed)\b", re.I)
_SENT_RE = re.compile(r"(?<=[.!?])\s+")

# Areas and generic phrases that are also somebody's restaurant name somewhere: never matched as a known name.
_STOP_PHRASES = set(BAY_CITIES) | {
    "the mission", "mission", "castro", "the castro", "sunset", "outer sunset", "inner sunset", "marina", "the marina",
    "richmond", "the richmond", "outer richmond", "inner richmond", "haight", "the haight", "soma", "tenderloin",
    "nob hill", "russian hill", "north beach", "chinatown", "union square", "financial district", "hayes valley",
    "dogpatch", "potrero", "potrero hill", "bernal", "bernal heights", "noe valley", "glen park", "excelsior",
    "bayview", "japantown", "fillmore", "pacific heights", "presidio", "embarcadero", "fisherman's wharf",
    "telegraph hill", "cow hollow", "west portal", "twin peaks", "mission bay", "south beach", "rincon hill",
    "treasure island", "downtown", "uptown", "temescal", "rockridge", "piedmont avenue", "lake merritt",
    "jack london", "jack london square", "fruitvale", "grand lake", "elmwood", "gourmet ghetto", "fourth street",
    "solano avenue", "bay area", "east bay", "south bay", "north bay", "the peninsula", "silicon valley",
    "golden gate", "golden gate park", "ferry building", "the ferry building", "happy hour", "farmers market",
    "food truck", "food trucks", "wine country", "napa valley", "the city", "west oakland", "east oakland",
    "oakland chinatown", "old oakland", "downtown oakland", "downtown berkeley", "downtown san jose", "japantown",
    "little saigon", "little italy", "valencia", "valencia street", "clement street", "irving street", "polk street",
    "chestnut street", "fillmore street", "college avenue", "san pablo avenue", "telegraph avenue", "shattuck avenue",
    "university avenue", "castro street", "murphy avenue", "santana row", "the alameda", "willow glen", "the pruneyard"}

# Single-word names that are everyday words are never matched on their own.
_STOP_SINGLE = {"kitchen", "market", "cafe", "coffee", "grill", "bar", "pizza", "taco", "tacos", "house", "place",
                "corner", "garden", "bakery", "deli", "diner", "burger", "burgers", "ramen", "sushi", "bistro", "cantina",
                "tavern", "lounge", "club", "room", "table", "spot", "shop", "store", "station", "park", "square",
                "plaza", "village", "central", "local", "golden", "lucky", "happy", "little", "big", "blue", "red",
                "green", "black", "white", "new", "old", "north", "south", "east", "west", "downtown", "main", "first",
                "the", "and", "home", "family", "brothers", "sisters", "mama", "papa", "sweet", "fresh", "hot", "cold",
                "gold", "silver", "star", "sun", "moon", "ocean", "bay", "city", "town", "street", "avenue", "road"}


def where_parts(where: str) -> tuple[str | None, str | None]:
    """"San Francisco, North Beach" → (san francisco, North Beach); "West Oakland" → (oakland, West Oakland);
    "Oakland Chinatown" → (oakland, Chinatown); "Mountain View" → (mountain view, None)."""
    w = _html.unescape(where or "").strip().strip(",")
    low = w.lower()
    if low in _SF_ALIASES:
        return _SF_ALIASES[low], None
    parts = [p.strip() for p in w.split(",") if p.strip()]
    city, hood = None, None
    for p in parts:
        pl = p.lower()
        if pl in BAY_CITIES or pl in _SF_ALIASES:
            city = _SF_ALIASES.get(pl, pl)
        elif hood is None:
            hood = p
    if city is None:
        for c in sorted(BAY_CITIES, key=len, reverse=True):
            if low.endswith(" " + c) or low.startswith(c + " "):
                city, hood = c, w                      # "West Oakland", "Oakland Chinatown": the whole of it
                break
    if city is None and hood and hood.lower() in _SF_ALIASES:
        city, hood = _SF_ALIASES[hood.lower()], None
    return city, hood


def _clean_name(name: str) -> str:
    n = _html.unescape(name).strip(" ,.:;-–—'\"“”‘’")
    n = _NAME_TRIM_RE.sub("", n)
    n = _NAME_CUT_RE.sub("", n)
    return n.strip(" ,.:;-–—")


def _quote_around(text: str, name: str, width: int = 200) -> str:
    i = text.lower().find(name.lower())
    if i < 0:
        return text[:width].strip()
    start = max(0, i - width // 2)
    q = text[start:start + width].strip()
    return re.sub(r"\s+", " ", q)


class NameIndex:
    """Known place names (the knowledge base and the gazetteer), matched as whole phrases in
    normalized text. Single everyday words never match; a one-word name needs five letters."""

    def __init__(self, names: Iterable[tuple[str, str | None]]):
        self.by_first: dict[str, list[tuple[tuple[str, ...], str, str | None]]] = {}
        for name, city in names:
            norm = kb.norm_name(name)
            toks = tuple(norm.split())
            if not toks or (len(toks) == 1 and (len(toks[0]) < 6 or toks[0] in _STOP_SINGLE)) or len(toks) > 6:
                continue
            if all(t in _STOP_SINGLE for t in toks) or norm in _STOP_PHRASES:
                continue
            self.by_first.setdefault(toks[0], []).append((toks, name, city))
        for lst in self.by_first.values():
            lst.sort(key=lambda x: -len(x[0]))                      # the longest name wins

    def find(self, text: str, *, limit: int = 12) -> list[tuple[str, str | None]]:
        """Known names in the text, in order of appearance. A hit must also appear in the raw text
        with a capital letter (prose says "the progress"; the restaurant is "Progress"), and a
        one-word name never at the start of a sentence ("Three new spots" is not a place)."""
        toks = kb.norm_name(text).split()
        out: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        i = 0
        while i < len(toks) and len(out) < limit:
            hit = None
            for cand, name, city in self.by_first.get(toks[i], ()):
                if tuple(toks[i:i + len(cand)]) == cand:
                    hit = (cand, name, city)
                    break
            if hit:
                if hit[1] not in seen and _capitalized_in(text, hit[1], one_word=len(hit[0]) == 1):
                    seen.add(hit[1])
                    out.append((hit[1], hit[2]))
                i += len(hit[0])
            else:
                i += 1
        return out


def _capitalized_in(text: str, name: str, *, one_word: bool) -> bool:
    for m in re.finditer(r"(?<![\w'’])" + re.escape(name) + r"(?![\w'’])", text, re.I):
        if not text[m.start()].isupper():
            continue
        if one_word:
            before = text[:m.start()].rstrip(" \t")
            if not before.strip() or before.endswith("\n") or before.rstrip()[-1] in ".!?:\"“" \
                    or before.rstrip().endswith(("—", "–", "•")):
                continue                       # a sentence or title start says nothing about a name
        return True
    return False


def mentions_from(item: Item, *, index: NameIndex | None = None, source: dict | None = None) -> list[Mention]:
    """The eating places an item names, with a kind and a quote each."""
    out: list[Mention] = []
    seen: set[str] = set()
    title = _html.unescape(item.title or "").strip()
    text = item.text()
    is_list = bool(_LIST_RE.search(title))
    kind_default = "list" if is_list else "mention"

    def add(name: str, kind: str, how: str, conf: float, city: str | None = None, hood: str | None = None,
            quote: str | None = None) -> None:
        name = _clean_name(name)
        n = kb.norm_name(name)
        if not n or n in seen or len(n) < 3:
            return
        seen.add(n)
        out.append(Mention(name=name, kind=kind, city=city, neighborhood=hood, how=how, confidence=conf,
                           quote=(quote if quote is not None else _quote_around(text, name))[:200]))

    if (source or {}).get("kind") == "hungryonion":
        for rx in _HO_TITLE_RES:
            m = rx.match(title)
            if m:
                city, hood = where_parts(m.group("where"))
                kind = "opening" if re.search(r"\b(new|opened|opening|now open)\b", title + " " + item.summary, re.I) else kind_default
                add(m.group("name"), kind, "title_convention", 0.85, city, hood, quote=title)
                break
    m = _HEADLINE_OPEN_RE.match(title)
    if m:
        add(m.group("name"), "opening", "headline", 0.75, quote=title)
    m = _HEADLINE_CLOSE_RE.match(title)
    if m:
        add(m.group("name"), "closed" if _CLOSED_PAST_RE.search(title) else "closing", "headline", 0.75, quote=title)
    if index is not None:
        for name, city in index.find(text):
            n = kb.norm_name(name)
            if n in seen:
                continue
            around = _quote_around(text, name, 240)
            if re.search(r"\b" + re.escape(name.lower()) + r"\b[^.\n]{0,40}\b" + _CLOSE_WORDS, around.lower()):
                kind = "closed" if _CLOSED_PAST_RE.search(around) else "closing"
            elif re.search(r"\b" + re.escape(name.lower()) + r"\b[^.\n]{0,40}\b" + _OPEN_WORDS, around.lower()) \
                    or re.search(r"\b(new|newly opened|just opened)\b[^.\n]{0,40}\b" + re.escape(name.lower()), around.lower()):
                kind = "opening"
            else:
                kind = kind_default
            add(name, kind, "known_name", 0.7, city, quote=around)
    return out


def is_food_item(item: Item, words: Iterable[str]) -> bool:
    t = (item.title + " " + item.summary[:400]).lower()
    return any(w in t for w in words)
