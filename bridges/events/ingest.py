"""Event ingestion — per-source fetch → typed events → store.

Two fetch kinds (PRD §6.3: high-yield structured first, degrade
gracefully everywhere else):

  * "ical"   — an RFC 5545 feed URL. Minimal VEVENT parser (DTSTART,
               DTEND, SUMMARY, LOCATION, URL). Precise; preferred when
               a feed URL is known. RRULE expansion is v2 — recurring
               masters are ingested at their DTSTART occurrence and a
               debug line notes the skipped rule.
  * "search" — site-scoped grounded LLM extraction through the
               budget-gated chokepoint: "search site/domain X for dated
               events in the next N days, STRICT JSON out". No fragile
               scraping; failures degrade to zero events, never raise.

CLI:
    .venv/bin/python -m bridges.events            # refresh all sources
    .venv/bin/python -m bridges.events --stats    # yield per source

Cost note: one grounded flash call per search-kind source per refresh —
~12 sources × 3 refreshes/day, all through core.llm.generate
(actor="events", budget-capped).
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
        usage = _llm.generate("events", "events.ingest.search",
                              prompt=prompt, model=model, search=True)
        if usage.error:
            logger.warning("ingest search failed for %s: %s",
                           source["id"], usage.error)
            return []
        raw = usage.text
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


# ─────────────────────────── refresh ───────────────────────────
def refresh_source(source: dict, *, today: datetime | None = None,
                   llm: Callable[[str], str] | None = None,
                   db_path: str | None = None) -> dict:
    """Refresh one source. Never raises; failures yield zero events."""
    today = today or datetime.now()
    try:
        if source.get("kind") == "ical":
            events = _fetch_ical(source)
        else:
            events = _fetch_search(source, today, llm)
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh %s failed (%s: %s)", source.get("id"),
                       type(e).__name__, e)
        events = []
    counts = upsert_events(events, source["id"], now=today, path=db_path)
    counts["source_id"] = source["id"]
    counts["fetched"] = len(events)
    return counts


def refresh_all(*, today: datetime | None = None,
                llm: Callable[[str], str] | None = None,
                db_path: str | None = None) -> list[dict]:
    results = [refresh_source(s, today=today, llm=llm, db_path=db_path)
               for s in load_sources()]
    total = sum(r["fetched"] for r in results)
    logger.info("events refresh: %d sources, %d events fetched",
                len(results), total)
    return results


def main() -> int:
    import sys
    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s :: %(message)s")
    if "--stats" in sys.argv:
        for source_id, active, latest in inventory_stats():
            print(f"{source_id:24s} {active or 0:4d} active   "
                  f"last refresh {latest}")
        return 0
    for r in refresh_all():
        print(f"{r['source_id']:24s} fetched {r['fetched']:3d}  "
              f"added {r['added']:3d}  updated {r['updated']:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
