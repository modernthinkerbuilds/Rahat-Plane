"""Event ingestion — per-source fetch → typed events → store.

Three fetch kinds (PRD §6.3: high-yield structured first, degrade
gracefully everywhere else):

  * "ical"   — an RFC 5545 feed URL. Minimal VEVENT parser (DTSTART,
               DTEND, SUMMARY, LOCATION, URL). Precise; preferred when
               a feed URL is known. RRULE expansion is v2 — recurring
               masters are ingested at their DTSTART occurrence and a
               debug line notes the skipped rule.
  * "page"   — 2026-08-24 (owner: venue/library/city events not being
               picked up): fetch the source URL directly (an events
               page or a public RSS feed), strip it to text, and
               LLM-extract dated events from THAT text (plain call, no
               search grounding). Recall is ~deterministic — the whole
               calendar is in-context — which is what small venue
               sites (Linden Tree), bibliocommons library RSS feeds,
               and server-rendered city calendars need; grounded
               search barely surfaces them (mv-libcal: zero rows in
               two weeks of refreshes).
  * "search" — site-scoped grounded LLM extraction through the
               budget-gated chokepoint: "search site/domain X for dated
               events in the next N days, STRICT JSON out". No fragile
               scraping; failures degrade to zero events, never raise.
               Recall flaps per refresh — last resort for JS-only
               surfaces and regional discovery.

CLI:
    .venv/bin/python -m bridges.events            # refresh all sources
    .venv/bin/python -m bridges.events --stats    # yield per source

Cost note: one flash call per LLM-extracted source per refresh —
grounded for search-kind, plain (cheaper) for page-kind — ~24 sources
× 3 refreshes/day, all through core.llm.generate (actor="events",
budget-capped).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable

from bridges.events.registry import load_sources
from bridges.events.store import upsert_events, inventory_stats

logger = logging.getLogger(__name__)

_HORIZON_DAYS = 21          # ingestion horizon (PRD: weeks out)
_MAX_EVENTS_PER_SOURCE = 40


# ─────────────────────────── iCal (minimal RFC 5545) ───────────────────
_ICS_DT = re.compile(r"^(?:DTSTART|DTEND)[^:]*:(\d{8})(?:T(\d{6}))?")


def _ics_dt(line: str) -> str | None:
    m = _ICS_DT.match(line)
    if not m:
        return None
    d, t = m.group(1), m.group(2) or "000000"
    return (f"{d[:4]}-{d[4:6]}-{d[6:8]} "
            f"{t[:2]}:{t[2:4]}:{t[4:6]}")


def parse_ical(text: str, source: dict) -> list[dict]:
    """VEVENT blocks → event dicts. Tolerant: a bad block is skipped."""
    events: list[dict] = []
    # Unfold RFC 5545 folded lines (CRLF + space/tab continuation).
    text = re.sub(r"\r?\n[ \t]", "", text or "")
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        ev: dict = {"city": source.get("city", ""),
                    "categories": list(source.get("categories") or [])}
        for line in block.splitlines():
            if line.startswith("DTSTART"):
                ev["start_ts"] = _ics_dt(line)
            elif line.startswith("DTEND"):
                ev["end_ts"] = _ics_dt(line)
            elif line.startswith("SUMMARY:"):
                ev["title"] = line[len("SUMMARY:"):].strip()
            elif line.startswith("LOCATION:"):
                ev["venue"] = line[len("LOCATION:"):].strip()
            elif line.startswith("URL:"):
                ev["url"] = line[len("URL:"):].strip()
            elif line.startswith("RRULE:"):
                logger.debug("RRULE skipped (v2): %s", line[:80])
        if ev.get("title") and ev.get("start_ts"):
            events.append(ev)
    return events[:_MAX_EVENTS_PER_SOURCE]


def _fetch_ical(source: dict) -> list[dict]:
    import requests
    resp = requests.get(source["url"], timeout=30)
    resp.raise_for_status()
    return parse_ical(resp.text, source)


# ─────────────────────────── page-kind (fetch + extract) ───────────────
_PAGE_TEXT_CAP = 18000       # chars of stripped page text into the prompt


def _strip_html(html: str) -> str:
    """Markup → readable text. Good enough for extraction: kill
    script/style/head blocks, then tags, then collapse whitespace.
    RSS/XML passes through mostly intact (tags become separators)."""
    text = re.sub(r"(?is)<(script|style|head|noscript|svg)[^>]*>.*?</\1>",
                  " ", html or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;?", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:_PAGE_TEXT_CAP]


def _page_prompt(source: dict, today: datetime, page_text: str) -> str:
    until = (today + timedelta(days=_HORIZON_DAYS)).strftime("%Y-%m-%d")
    return f"""Today is {today.strftime('%Y-%m-%d')}. Below is the text content of
{source['name']} ({source['url']}). Extract REAL dated events from it.

Focus: {source.get('query_hint', 'upcoming events')}
Area: {source.get('city', 'Bay Area')}, California
Window: today through {until}

Only events that actually appear in the text, each with a real calendar
date in the window. Resolve relative dates ("Sunday, August 30") against
today's date. No inventions, no past events, no "check the website".

Return STRICT JSON only, no fences:
{{"events": [{{"title": "...", "start_ts": "YYYY-MM-DD HH:MM:SS",
              "end_ts": "" , "venue": "...", "city": "...",
              "url": "..."}}]}}
Use 00:00:00 for all-day events. Max {_MAX_EVENTS_PER_SOURCE}.

PAGE TEXT:
{page_text}"""


def _fetch_page(source: dict, today: datetime,
                llm: Callable[[str], str] | None,
                http: Callable[[str], str] | None = None) -> list[dict]:
    """Fetch the source URL, extract events from its text. Both the
    HTTP fetch and the LLM call have test seams; hermetic runs without
    seams yield zero events (never the wire — same rule as search)."""
    if http is not None:
        html = http(source["url"]) or ""
    else:
        if os.getenv("RAHAT_TEST_MODE") == "1":
            return []            # hermetic: no wire without a seam
        import requests
        resp = requests.get(
            source["url"], timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (rahat-events-bridge)"})
        resp.raise_for_status()
        html = resp.text
    page_text = _strip_html(html)
    if not page_text:
        return []
    prompt = _page_prompt(source, today, page_text)
    if llm is not None:
        raw = llm(prompt) or ""
    else:
        if os.getenv("RAHAT_TEST_MODE") == "1":
            return []            # hermetic: no wire without a seam
        from core import llm as _llm
        model = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
        usage = _llm.generate("events", "events.ingest.page",
                              prompt=prompt, model=model)
        if usage.error:
            logger.warning("ingest page-extract failed for %s: %s",
                           source["id"], usage.error)
            return []
        raw = usage.text
    return _typed_events(raw, source)


def _typed_events(raw: str, source: dict) -> list[dict]:
    """Shared JSON-out validation for the LLM extraction kinds."""
    from agents.genie.live_plan import _parse_json_block
    obj = _parse_json_block(raw)
    if not isinstance(obj, dict):
        return []
    out = []
    for e in (obj.get("events") or [])[:_MAX_EVENTS_PER_SOURCE]:
        if isinstance(e, dict) and e.get("title") and e.get("start_ts"):
            e.setdefault("city", source.get("city", ""))
            e["categories"] = list(source.get("categories") or [])
            out.append(e)
    return out


# ─────────────────────────── search-kind (grounded LLM) ────────────────
def _search_prompt(source: dict, today: datetime) -> str:
    until = (today + timedelta(days=_HORIZON_DAYS)).strftime("%Y-%m-%d")
    return f"""Today is {today.strftime('%Y-%m-%d')}. Use web search restricted to
this source to find REAL dated events:

Source: {source['name']} — {source['url']}
Focus: {source.get('query_hint', 'upcoming events')}
Area: {source.get('city', 'Bay Area')}, California
Window: today through {until}

Only include events you actually found, each with a real calendar date
in the window. No inventions, no past events, no "check the website".

Return STRICT JSON only, no fences:
{{"events": [{{"title": "...", "start_ts": "YYYY-MM-DD HH:MM:SS",
              "end_ts": "" , "venue": "...", "city": "...",
              "url": "..."}}]}}
Use 00:00:00 for all-day events. Max {_MAX_EVENTS_PER_SOURCE}."""


def _fetch_search(source: dict, today: datetime,
                  llm: Callable[[str], str] | None) -> list[dict]:
    prompt = _search_prompt(source, today)
    if llm is not None:
        raw = llm(prompt) or ""
    else:
        if os.getenv("RAHAT_TEST_MODE") == "1":
            return []            # hermetic: no wire without a seam
        from core import llm as _llm
        model = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
        # thinking_budget=0 (2026-09-19): this is JSON extraction from
        # web results — a thinking model's reasoning tokens were a third
        # of the September bill and bought nothing here.
        usage = _llm.generate("events", "events.ingest.search",
                              prompt=prompt, model=model, search=True,
                              thinking_budget=0)
        if usage.error:
            logger.warning("ingest search failed for %s: %s",
                           source["id"], usage.error)
            return []
        raw = usage.text
    return _typed_events(raw, source)


# ─────────────── grounding-redirect URLs (2026-09-13) ───────────────
# LIVE: search-kind sources come back from Gemini grounded search with
# opaque redirect links (vertexaisearch.cloud.google.com/grounding-api-
# redirect/AUZIYQG…=). They work, but they are 200+ characters of noise
# under a "[here]" link, they carry '_' (which broke Telegram's legacy
# Markdown — see digest.md_url), and at 300 chars the store truncates
# them into dead links. Resolve them to the real page at ingest, and
# backfill rows already stored. Hermetic: no wire under RAHAT_TEST_MODE
# unless a `resolver` seam is passed.
REDIRECT_MARKERS = ("grounding-api-redirect",)


def is_redirect_url(url: str | None) -> bool:
    u = url or ""
    return any(m in u for m in REDIRECT_MARKERS)


def resolve_url(url: str, resolver: Callable[[str], str] | None = None,
                timeout: float = 8.0) -> str:
    """The final URL behind a redirect link, or the input unchanged."""
    if not is_redirect_url(url):
        return url
    if resolver is not None:
        try:
            return (resolver(url) or "").strip() or url
        except Exception:  # noqa: BLE001
            return url
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return url
    try:
        import requests
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        final = resp.url or ""
        if not final or is_redirect_url(final) or resp.status_code >= 400:
            resp = requests.get(url, allow_redirects=True, timeout=timeout,
                                stream=True)
            final = resp.url or ""
            resp.close()
        return final if final and not is_redirect_url(final) else url
    except Exception as e:  # noqa: BLE001
        logger.debug("redirect resolve failed for %s: %s", url[:60], e)
        return url


def resolve_event_urls(events: list[dict],
                       resolver: Callable[[str], str] | None = None,
                       limit: int = 30) -> int:
    """In place: swap redirect links for their targets, at most `limit`
    lookups per call. Returns how many changed."""
    changed = 0
    for e in events:
        if limit <= 0:
            break
        u = str(e.get("url") or "")
        if not is_redirect_url(u):
            continue
        limit -= 1
        final = resolve_url(u, resolver)
        if final != u:
            e["url"] = final
            changed += 1
    return changed


def resolve_stored_urls(resolver: Callable[[str], str] | None = None, *,
                        now: datetime | None = None, limit: int = 40,
                        db_path: str | None = None) -> int:
    """Backfill: resolve redirect links already in the inventory (from
    the upcoming window on), soonest first. Returns how many changed."""
    from bridges.events.store import redirect_url_rows, set_url
    now = now or datetime.now()
    changed = 0
    try:
        rows = redirect_url_rows(REDIRECT_MARKERS[0],
                                 start_date=now.strftime("%Y-%m-%d"),
                                 limit=limit, path=db_path)
    except Exception:  # noqa: BLE001
        return 0
    for event_id, url in rows:
        final = resolve_url(url, resolver)
        if final != url:
            set_url(event_id, final, path=db_path)
            changed += 1
    if changed:
        logger.info("events: resolved %d redirect links", changed)
    return changed


# ─────────────────────────── refresh ───────────────────────────
def refresh_source(source: dict, *, today: datetime | None = None,
                   llm: Callable[[str], str] | None = None,
                   http: Callable[[str], str] | None = None,
                   resolver: Callable[[str], str] | None = None,
                   db_path: str | None = None) -> dict:
    """Refresh one source. Never raises; failures yield zero events."""
    today = today or datetime.now()
    kind = source.get("kind") or "search"
    try:
        if kind == "ical":
            events = _fetch_ical(source)
        elif kind == "page":
            events = _fetch_page(source, today, llm, http)
        else:
            events = _fetch_search(source, today, llm)
            resolve_event_urls(events, resolver)
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh %s failed (%s: %s)", source.get("id"),
                       type(e).__name__, e)
        events = []
    counts = upsert_events(events, source["id"], now=today, path=db_path,
                           source_kind=kind)
    counts["source_id"] = source["id"]
    counts["fetched"] = len(events)
    return counts


# ─────────────── spend control (2026-09-19) ───────────────
# September's Gemini invoice was $19.75, 95% of it this pipeline: every
# search-kind source and every popularity lookup ran THREE times a day,
# each call grounded (web context billed as input) with dynamic
# thinking on (billed as output). Owner's call: the free kinds (ical,
# page) keep their three daily passes; the paid search pass runs
# Wednesday and Saturday at 03:00 (`--search`, scheduled by launchd)
# and on demand from Genie, rate-limited. ~102 paid calls/day → ~10.
FREE_KINDS = ("ical", "page")
PAID_KINDS = ("search",)
ON_DEMAND_SOURCE_ID = "_on_demand"
ON_DEMAND_MIN_GAP_HOURS = 6


def refresh_all(*, today: datetime | None = None,
                llm: Callable[[str], str] | None = None,
                db_path: str | None = None,
                kinds: tuple[str, ...] | None = None) -> list[dict]:
    """Refresh every registered source, or only those whose kind is in
    `kinds` (None = all). The scheduled free passes call this with
    FREE_KINDS; the search pass and the on-demand refresh with all."""
    sources = [s for s in load_sources()
               if kinds is None or (s.get("kind") or "search") in kinds]
    results = [refresh_source(s, today=today, llm=llm, db_path=db_path)
               for s in sources]
    total = sum(r["fetched"] for r in results)
    logger.info("events refresh: %d sources (%s), %d events fetched",
                len(results), ",".join(kinds) if kinds else "all", total)
    return results


def last_on_demand(db_path: str | None = None) -> datetime | None:
    from bridges.events.store import last_refresh_at
    ts = last_refresh_at(ON_DEMAND_SOURCE_ID, path=db_path)
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def refresh_on_demand(*, now: datetime | None = None,
                      llm: Callable[[str], str] | None = None,
                      resolver: Callable[[str], str] | None = None,
                      min_gap_hours: float = ON_DEMAND_MIN_GAP_HOURS,
                      db_path: str | None = None) -> dict:
    """A paid search pass on request ("refresh the events"), rate-
    limited so a double-tap can't run the bill. Returns
      {"ran": bool, "reason": str, "sources": n, "fetched": n,
       "added": n, "last": datetime|None, "next": datetime|None}.
    Spend goes through core.llm.generate like any other call."""
    from bridges.events.store import mark_refresh
    now = now or datetime.now()
    last = last_on_demand(db_path)
    if last is not None and (now - last) < timedelta(hours=min_gap_hours):
        return {"ran": False, "reason": "rate-limited", "sources": 0,
                "fetched": 0, "added": 0, "last": last,
                "next": last + timedelta(hours=min_gap_hours)}
    results = [refresh_source(s, today=now, llm=llm, resolver=resolver,
                              db_path=db_path)
               for s in load_sources()
               if (s.get("kind") or "search") in PAID_KINDS]
    try:
        from bridges.events.popularity import score_upcoming
        score_upcoming(now, llm=llm, path=db_path)
    except Exception:  # noqa: BLE001 — curation is best-effort
        pass
    mark_refresh(ON_DEMAND_SOURCE_ID, now=now,
                 fetched=sum(r["fetched"] for r in results), path=db_path)
    return {"ran": True, "reason": "ok", "sources": len(results),
            "fetched": sum(r["fetched"] for r in results),
            "added": sum(r["added"] for r in results), "last": now,
            "next": now + timedelta(hours=min_gap_hours)}


def main() -> int:
    import sys
    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s :: %(message)s")
    if "--stats" in sys.argv:
        for source_id, active, latest in inventory_stats():
            print(f"{source_id:24s} {active or 0:4d} active   "
                  f"last refresh {latest}")
        return 0
    # Default pass = the FREE kinds only. `--search` adds the paid
    # search-kind sources + popularity lookups (Wed/Sat 03:00 in launchd).
    paid = "--search" in sys.argv
    kinds = None if paid else FREE_KINDS
    for r in refresh_all(kinds=kinds):
        print(f"{r['source_id']:24s} fetched {r['fetched']:3d}  "
              f"added {r['added']:3d}  updated {r['updated']:3d}")
    if paid:
        # Curation (2026-09-03): look up online popularity for at most
        # 10 recurring series per pass (30-day cache) so the digest can
        # rank. Paid + grounded → only on the search pass.
        from bridges.events.popularity import score_upcoming
        print(f"popularity: scored {score_upcoming()} recurring series")
    print(f"links: resolved {resolve_stored_urls()} redirect links")
    print("pass:", "search + free" if paid else "free kinds only "
          "(ical/page); add --search for the paid pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
