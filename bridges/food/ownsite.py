"""bridges.food.ownsite — a place's own page, read in code for the facts
it states in schema.org JSON-LD: opening hours, phone, price range,
cuisine, a menu link. No model reads the page.

Most restaurant sites built on Squarespace, Wix, Toast, BentoBox or
WordPress emit a `Restaurant` / `FoodEstablishment` / `LocalBusiness`
block with `openingHoursSpecification`. A site without one yields
nothing, and the place keeps its last hours; Google Maps re-checks it
at ask time, for a tenth of a cent.

Fetches go through core.httpcache under core.sources' rules (robots,
blocked hosts, throttling), so a site that asks readers out is left
alone, and under test mode nothing leaves the machine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from core import httpcache, sources

_LD_RE = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_TYPES = ("restaurant", "foodestablishment", "localbusiness", "cafeorcoffeeshop", "bakery", "barorpub",
          "fastfoodrestaurant", "icecreamshop", "winery", "brewery", "distillery")
_DAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
         "mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
USER_AGENT = "rahat-food-ownsite/1.0 (a household's eating-out reader; one visit a week at most)"


@dataclass
class SiteFacts:
    url: str
    ok: bool = False
    status: int = 0
    hours_text: str | None = None
    phone: str | None = None
    price_range: str | None = None
    cuisine: str | None = None
    menu_url: str | None = None
    name: str | None = None
    error: str | None = None
    fetched_at: str = ""
    types: list[str] = field(default_factory=list)


def _ampm(hhmm: str) -> str | None:
    m = re.match(r"^(\d{1,2}):(\d{2})", hhmm.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h >= 24:
        h -= 24
    return f"{h % 12 or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}"


def _day_index(d) -> int | None:
    s = str(d or "").strip()
    s = s.rsplit("/", 1)[-1].lower()                      # "https://schema.org/Monday" → monday
    return _DAYS.get(s)


def hours_from_spec(spec) -> str | None:
    """openingHoursSpecification (a list or one object) → "Monday: 11:00 AM–9:00 PM; …"."""
    if not spec:
        return None
    if isinstance(spec, dict):
        spec = [spec]
    week: dict[int, list[str]] = {}
    for s in spec:
        if not isinstance(s, dict):
            continue
        days = s.get("dayOfWeek") or []
        if isinstance(days, (str, dict)):
            days = [days]
        opens, closes = s.get("opens"), s.get("closes")
        for d in days:
            if isinstance(d, dict):
                d = d.get("@id") or d.get("name")
            i = _day_index(d)
            if i is None:
                continue
            if not opens or not closes or str(opens).startswith("00:00") and str(closes).startswith("00:00"):
                week.setdefault(i, []).append("Closed")
                continue
            a, b = _ampm(str(opens)), _ampm(str(closes))
            if a and b:
                week.setdefault(i, []).append(f"{a}–{b}")
    if not week:
        return None
    return "; ".join(f"{_DAY_NAMES[d]}: {', '.join(v)}" for d, v in sorted(week.items()))


def hours_from_text(spec) -> str | None:
    """`openingHours` as schema.org text ("Mo-Fr 11:00-21:00", or a list of them)."""
    if not spec:
        return None
    from core.kb import gazetteer as gz
    parts = spec if isinstance(spec, list) else [spec]
    return gz.osm_hours_to_text("; ".join(str(p) for p in parts if p))


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def facts_from_html(html: str, url: str) -> SiteFacts:
    out = SiteFacts(url=url)
    for block in _LD_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", block.strip()))
            except ValueError:
                continue
        for node in _walk(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else list(types or [])
            if not any(str(t).lower() in _TYPES for t in types):
                continue
            out.types = [str(t) for t in types]
            out.name = out.name or (node.get("name") if isinstance(node.get("name"), str) else None)
            out.hours_text = out.hours_text or hours_from_spec(node.get("openingHoursSpecification")) \
                or hours_from_text(node.get("openingHours"))
            out.phone = out.phone or (node.get("telephone") if isinstance(node.get("telephone"), str) else None)
            out.price_range = out.price_range or (node.get("priceRange") if isinstance(node.get("priceRange"), str) else None)
            cz = node.get("servesCuisine")
            if cz and not out.cuisine:
                out.cuisine = ", ".join(cz) if isinstance(cz, list) else str(cz)
            menu = node.get("hasMenu") or node.get("menu")
            if isinstance(menu, dict):
                menu = menu.get("url") or menu.get("@id")
            if isinstance(menu, str) and menu.startswith("http") and not out.menu_url:
                out.menu_url = menu
    out.ok = any((out.hours_text, out.phone, out.price_range, out.cuisine, out.menu_url))
    return out


def read_site(url: str, *, now: datetime, timeout: float = 12.0, opener=None) -> SiteFacts:
    """Fetch a place's page under the source rules and read its JSON-LD."""
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    pol = sources.policy_for(url)
    if pol["policy"] in ("blocked", "link_only"):
        return SiteFacts(url=url, error=f"{pol['policy']}: {pol['why']}", fetched_at=fetched_at)
    sources.throttle(url)
    try:
        f = httpcache.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
                          timeout=timeout, opener=opener)
    except httpcache.HermeticMiss:
        return SiteFacts(url=url, error="hermetic: no cassette for this page", fetched_at=fetched_at)
    if f.error or f.status >= 400:
        if f.status in (403, 429):
            sources.block_for_today(url, f"http {f.status}")
        return SiteFacts(url=url, status=f.status, error=f.error or f"http {f.status}", fetched_at=fetched_at)
    body = f.body.decode("utf-8", "replace") if isinstance(f.body, (bytes, bytearray)) else str(f.body)
    out = facts_from_html(body, url)
    out.status, out.fetched_at = f.status, fetched_at
    if not out.ok:
        out.error = "no schema.org facts on the page"
    return out
