"""Event curation — recurrence, online popularity, family fit (2026-09-03).

Owner, verbatim: "the list is so exhaustive, it's really overwhelming
me … go by the popularity of the event. If it's a recurring event, look
for its reviews online, look how popular it is amongst the different
interests of our family members … if some event is not recurring and
it's a brand new event then give it as is and allow us to decide."

Three ideas, kept deliberately separate so each is testable and cheap:

  * RECURRING vs NEW is decided from the inventory's OWN history:
    store.series_key strips dates/weekdays/numbers from a title, and a
    series seen on ≥2 distinct dates has a track record. No LLM.
  * POPULARITY is looked up ONLINE once per recurring series (one
    grounded flash call through the budget chokepoint), cached for
    30 days in events_popularity, and refreshed at most `limit`
    series per ingest pass — so cost is bounded and the digest itself
    never spends a token. A brand-new event is never scored: there is
    nothing to look up, and the owner wants to judge those himself.
  * FAMILY FIT is a plain word-overlap between the family profile's
    per-member preferences and the event's title / categories /
    audience — no model, no ambiguity about why something ranked.

rank() returns (picks, new): recurring rows ordered by
0.7·popularity + 0.3·family-fit (unscored recurring series fall back
to fit alone), and the new rows untouched, in feed order.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Callable

from bridges.events import store

logger = logging.getLogger(__name__)

RECURRING_MIN_DATES = 2
CACHE_DAYS = 30
PICK_FLOOR = 45          # a recurring series reviewed below this is
                         # demoted out of the digest entirely — the
                         # owner asked for a SHORTER list, and a known
                         # dud is the first thing to cut
_LOOKAHEAD_DAYS = 21


# ─────────────────────────── online popularity ───────────────────────
def _prompt(row: dict) -> str:
    return f"""Use web search to assess how popular and well-regarded this
RECURRING Bay Area event is — reviews, attendance, how long it has run,
what kind of crowd it draws.

Event: {row.get('title')}
Venue: {row.get('venue') or 'unknown'} — {row.get('city') or 'Bay Area'}, CA

Return STRICT JSON only, no fences:
{{"score": <0-100 integer; 80+ = beloved local institution, 50 = fine
but unremarkable, <30 = poorly reviewed or barely attended>,
 "evidence": "<one clause, max 120 chars, citing what you found>",
 "audience": ["toddlers"|"kids"|"teens"|"adults"|"seniors"|"families"|
              "couples" ...]}}
If you can find nothing about it, return {{"score": null,
"evidence": "no track record found online", "audience": []}}."""


def score_series(row: dict, llm: Callable[[str], str] | None = None
                 ) -> dict | None:
    """One grounded lookup → {score, evidence, audience}. Hermetic: no
    wire under RAHAT_TEST_MODE without the seam; failures → None."""
    prompt = _prompt(row)
    if llm is not None:
        raw = llm(prompt) or ""
    else:
        if os.getenv("RAHAT_TEST_MODE") == "1":
            return None
        from core import llm as _llm
        model = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
        usage = _llm.generate("events", "events.popularity",
                              prompt=prompt, model=model, search=True,
                              thinking_budget=0)     # extraction, no reasoning
        if usage.error:
            logger.warning("popularity lookup failed for %r: %s",
                           row.get("title"), usage.error)
            return None
        raw = usage.text
    from agents.genie.live_plan import _parse_json_block
    obj = _parse_json_block(raw)
    if not isinstance(obj, dict):
        return None
    score = obj.get("score")
    try:
        score = None if score is None else max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = None
    aud = obj.get("audience") or []
    return {"score": score,
            "evidence": str(obj.get("evidence") or "")[:200],
            "audience": ",".join(str(a) for a in aud if a)[:120]}


def score_upcoming(now: datetime | None = None, *,
                   llm: Callable[[str], str] | None = None,
                   limit: int = 10,
                   path: str | None = None) -> int:
    """Ingest-time pass: look up at most `limit` recurring series in the
    next 3 weeks that have no fresh cache row. Returns how many were
    scored. Never raises."""
    now = now or datetime.now()
    start = now.strftime("%Y-%m-%d")
    end = (now + timedelta(days=_LOOKAHEAD_DAYS)).strftime("%Y-%m-%d")
    try:
        rows = store.query_window(start, end, limit=400, path=path)
        counts = store.recurrence_counts(path)
    except Exception:  # noqa: BLE001
        return 0
    by_key: dict[str, dict] = {}
    for r in rows:
        k = store.series_key(r.get("title") or "", r.get("venue") or "")
        if counts.get(k, 0) >= RECURRING_MIN_DATES and k not in by_key:
            by_key[k] = r
    cached = store.get_popularity(list(by_key), path=path)
    floor = (now - timedelta(days=CACHE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    todo = [(k, r) for k, r in by_key.items()
            if not cached.get(k) or (cached[k].get("checked_at") or "") < floor]
    scored = 0
    for k, r in todo[:limit]:
        res = score_series(r, llm)
        if res is None:
            continue
        store.set_popularity(k, res["score"], res["evidence"],
                             res["audience"], now=now, path=path)
        scored += 1
    if scored:
        logger.info("popularity: scored %d recurring series", scored)
    return scored


# ─────────────────────────── family fit ───────────────────────────
_WORD = re.compile(r"[a-z]{4,}")


def interest_match(row: dict, interests: list[str],
                   audience: str = "") -> float:
    """0..1 — share of the family's preference phrases that share a
    meaningful word with the event's title / categories / audience."""
    if not interests:
        return 0.0
    hay = " ".join([str(row.get("title") or ""),
                    str(row.get("categories") or ""),
                    str(row.get("venue") or ""), audience]).lower()
    hay_words = set(_WORD.findall(hay))
    hits = 0
    for phrase in interests:
        words = set(_WORD.findall(str(phrase).lower()))
        if words & hay_words:
            hits += 1
    return min(1.0, hits / max(1, len(interests)))


# ─────────────────────────── ranking ───────────────────────────
def rank(rows: list[dict], *, interests: list[str] | None = None,
         path: str | None = None) -> tuple[list[dict], list[dict]]:
    """Split one day's rows into (picks, new).

    picks: recurring series (≥2 distinct dates in the inventory), each
           annotated with _score / _evidence / _fit and ordered best
           first; unscored recurring series rank on fit alone.
    new:   everything else, untouched, in feed order — the owner's
           call, as asked."""
    interests = interests or []
    counts = store.recurrence_counts(path)
    keys = [store.series_key(r.get("title") or "", r.get("venue") or "")
            for r in rows]
    pop = store.get_popularity(list({k for k in keys}), path=path)
    picks: list[dict] = []
    new: list[dict] = []
    for r, k in zip(rows, keys):
        if counts.get(k, 0) >= RECURRING_MIN_DATES:
            info = pop.get(k) or {}
            fit = interest_match(r, interests, info.get("audience", ""))
            score = info.get("score")
            if score is not None and score < PICK_FLOOR:
                continue                       # known dud → not shown
            r = dict(r, _score=score, _evidence=info.get("evidence", ""),
                     _fit=fit, _series=k)
            r["_rank"] = ((0.7 * (score / 100.0) + 0.3 * fit)
                          if score is not None else 0.3 * fit)
            picks.append(r)
        else:
            new.append(r)
    picks.sort(key=lambda r: -r["_rank"])
    return picks, new
