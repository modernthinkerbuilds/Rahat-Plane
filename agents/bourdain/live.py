"""agents.bourdain.live — the one grounded search, used only when the
seed comes up short (PRD §5.3 step 3, §12).

  * exactly one `core.llm.generate(actor="bourdain", search=True,
    thinking_budget=0)` call per ask; never in a loop, never scheduled;
  * every candidate must carry a source URL and an address (the "real
    place" proxy until the Places key lands in S1) and pass the §5.4
    gates: Google ≥ 4.3 (4.3–4.4 = "below your usual bar"), ≥ 100
    reviews unless a trusted writer praises it, < 20 locations, not a
    bar, no seafood-led place when the wife is eating;
  * survivors are saved (list "live") with a mention row, so the next
    ask costs nothing; an identical query is cached 6 h;
  * hermetic: no wire under RAHAT_TEST_MODE without an `llm` seam.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Callable

from agents.bourdain import state

logger = logging.getLogger("bourdain")

RATING_FLOOR = 4.3
RATING_OK = 4.5
MIN_REVIEWS = 100
CHAIN_MIN_LOCATIONS = 20
MAX_GROUNDED_CALLS_PER_ASK = 2


def _hermetic() -> bool:
    """No wire under test mode without a seam (tests patch this to
    exercise the Spend path with a stubbed core.llm.generate)."""
    return os.getenv("RAHAT_TEST_MODE") == "1"


def _prompt(city_label: str, near: str, meal: str, party: str,
            cuisine: str | None) -> str:
    diners = ("one adult who prefers gluten-free versions of pastry, pizza, "
              "bread and pasta (a preference, not an allergy)")
    if party == "we":
        diners += (", plus one adult who is vegetarian (eats eggs, "
                   "occasionally chicken) and dislikes places that smell "
                   "of seafood")
    want = cuisine or meal
    return f"""Use web search. Find up to 5 independent, well-regarded places for
{want} near {near} in {city_label}, for {diners}. Prefer what local food
writers (Eater, The Infatuation, the city paper, Michelin Bib Gourmand,
James Beard) and local community threads recommend. No bars, no chains
(20+ locations), nothing without a real address.

Return STRICT JSON only, no fences:
{{"places": [{{"name": "...", "address": "...", "area": "...",
  "cuisine": "...", "google_rating": 4.6, "review_count": 1200,
  "locations": 1, "hours_text": "...", "is_bar": false,
  "seafood_led": false, "him": "<a dish for the GF-preferring adult>",
  "her": "<a vegetarian main>", "source_name": "...",
  "source_url": "https://...", "why": "<one clause from the source>"}}]}}
Unknown numbers → null. Never invent a place, a rating or a source."""


def gate(c: dict, party: str) -> tuple[bool, str, list[str]]:
    """(passes, reason_if_not, labels)."""
    labels: list[str] = []
    if not c.get("source_url") or not c.get("address"):
        return False, "no source or address", labels
    if c.get("is_bar"):
        return False, "bar", labels
    if party == "we" and c.get("seafood_led"):
        return False, "seafood-led", labels
    try:
        rating = float(c.get("google_rating")) if c.get("google_rating") \
            is not None else None
    except (TypeError, ValueError):
        rating = None
    try:
        reviews = int(c.get("review_count")) if c.get("review_count") \
            is not None else None
    except (TypeError, ValueError):
        reviews = None
    try:
        locs = int(c.get("locations")) if c.get("locations") is not None \
            else 1
    except (TypeError, ValueError):
        locs = 1
    if rating is None or rating < RATING_FLOOR:
        return False, f"Google {rating if rating is not None else '?'} — under your floor", labels
    if rating < RATING_OK:
        labels.append("below your usual bar")
    if reviews is not None and reviews < MIN_REVIEWS and not c.get("source_name"):
        return False, "too few reviews", labels
    if locs >= CHAIN_MIN_LOCATIONS:
        return False, f"{locs} locations — a chain", labels
    if 2 <= locs < CHAIN_MIN_LOCATIONS:
        labels.append("small group")
    return True, "", labels


def search(*, city: str, city_label: str, near: str, meal: str, party: str,
           cuisine: str | None, now: datetime,
           llm: Callable[[str], str] | None = None,
           db_path: str | None = None) -> tuple[list[dict], str | None]:
    """→ (saved place dicts, error). error is a short reason when the
    model was unavailable (capped / 429 / hermetic)."""
    key = f"{city}|{near.lower()}|{cuisine or meal}|{party}"
    cached = state.live_cache_get(key, now)
    if cached is not None:
        by_id = {p["place_id"]: p for p in state.places(city, path=db_path)}
        return [by_id[i] for i in cached if i in by_id], None
    prompt = _prompt(city_label, near, meal, party, cuisine)
    if llm is not None:
        raw = llm(prompt) or ""
    else:
        if _hermetic():
            return [], "hermetic"
        from core import llm as _llm
        try:
            usage = _llm.generate("bourdain", "bourdain.live_search",
                                  prompt=prompt, search=True,
                                  thinking_budget=0)
        except Exception as e:  # noqa: BLE001 — BudgetExceeded and kin
            logger.warning("live search blocked: %s", e)
            return [], "capped"
        if usage.error:
            logger.warning("live search failed: %s", usage.error)
            return [], "down"
        raw = usage.text
    from agents.genie.live_plan import _parse_json_block
    obj = _parse_json_block(raw)
    cands = (obj or {}).get("places") if isinstance(obj, dict) else None
    if not isinstance(cands, list):
        return [], None
    kept: list[dict] = []
    for c in cands[:5]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        ok, why_not, labels = gate(c, party)
        if not ok:
            logger.info("live candidate dropped: %s — %s", c.get("name"), why_not)
            continue
        meals = [meal] + ([cuisine] if cuisine else [])
        tags = ["live"] + (["small_group"] if "small group" in labels else []) \
            + (["below_bar"] if "below your usual bar" in labels else [])
        rating_txt = (f"Google {c.get('google_rating')}"
                      + (f" ({c.get('review_count')})" if c.get("review_count") else ""))
        pid = state.upsert_place({
            "name": c["name"], "list": "live", "city": city,
            "area": c.get("area") or "", "address": c.get("address"),
            "meals": meals, "tags": tags, "rating": rating_txt,
            "rating_num": c.get("google_rating"),
            "review_count": c.get("review_count"),
            "locations": c.get("locations") or 1,
            "hours_text": c.get("hours_text") or "hours not checked",
            "him": c.get("him") or "—", "her": c.get("her") or "—",
            "map_url": "https://www.google.com/maps/search/?api=1&query="
                       + _q(f"{c['name']} {c.get('address') or ''}"),
            "why": f"{c.get('source_name') or 'source'}: {c.get('why') or ''}".strip(": "),
            "why_url": c.get("source_url"), "note": "found by live search",
        }, now=now, path=db_path)
        state.add_mention(pid, source=c.get("source_name") or "search",
                          url=c.get("source_url") or "",
                          snippet=c.get("why") or "", now=now, path=db_path)
        kept.append(next(p for p in state.places(city, path=db_path)
                         if p["place_id"] == pid))
    state.live_cache_put(key, [p["place_id"] for p in kept], now)
    return kept, None


def _q(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s.strip())
