"""Genie idea capture — the PRD core loop's step 4 (2026-08-09).

THE INCIDENT. The spouse's first real message to the Genie bot was a
multi-weekend plan proposal ("Aug 16th weekend: Alameda … Husband and
wife outing - Every Friday: 1. Mt Hamilton …"). Two failures at once:
keyword intents fired on words buried in the text ("adults only",
"high") and hijacked it into a wrong date-night plan — and Genie had
NO path for what the message actually was: the humans handing back a
rough idea (PRD core loop step 4, "they hand back a rough idea →
Genie refines it").

THE SHAPE (PRD non-negotiable preserved): the LLM only ELICITS — it
converts free text into typed proposals (weekend ideas with normalized
dates, a date-night rotation, leftover notes) and never plans, ranks
or schedules anything. Storage is deterministic and charter-gated
(KIND_IDEAS_CAPTURE). Downstream, /weekend_plan anchors on the stored
proposal for that weekend and date-night pulls from the rotation — the
humans' picks stay the spine; discovery only decorates.

Failure contract: elicit() returns None on any trouble; the caller
falls back to storing the raw text as a note (capture must never lose
the humans' words).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from agents.genie.live_plan import _parse_json_block, _clean

logger = logging.getLogger(__name__)

_MAX_WEEKENDS = 12
_MAX_ITEMS = 8
_MAX_DATE_NIGHTS = 12


def _elicit_prompt(text: str, today_iso: str) -> str:
    return f"""You extract structure from a family's own weekend-planning notes.
Today is {today_iso}. Do NOT invent, rank, or plan anything — only
extract what the humans wrote.

Rules:
- Each weekend mention becomes one entry. "weekend_of" = ISO date of
  that weekend's SATURDAY (assume the current year unless stated;
  "Aug 16th weekend" → the Saturday of that weekend). Use "" if you
  cannot resolve a date.
- "items" = the activities in the order written (split chains like
  "A > B > C" into separate items). Keep the humans' own words.
- "companions" = who they mentioned joining/attending, verbatim-ish.
- Recurring couple/date-night idea LISTS go into "date_nights" (one
  short string per idea, order preserved).
- Anything else meaningful goes into "notes".

Return STRICT JSON only — no prose, no fences:
{{"weekends": [{{"weekend_of": "YYYY-MM-DD or ''", "label": "short",
                "items": ["..."], "companions": "", "notes": ""}}],
  "date_nights": ["..."],
  "notes": ["..."]}}

THE NOTES:
{text}"""


def _coerce(obj: dict) -> dict | None:
    weekends = []
    for w in (obj.get("weekends") or [])[:_MAX_WEEKENDS]:
        if not isinstance(w, dict):
            continue
        items = [_clean(i, 100) for i in (w.get("items") or [])[:_MAX_ITEMS]
                 if _clean(i, 100)]
        label = _clean(w.get("label"), 60)
        if not items and not label:
            continue
        weekends.append({
            "weekend_of": _clean(w.get("weekend_of"), 10),
            "label": label or (items[0] if items else ""),
            "items": items,
            "companions": _clean(w.get("companions"), 80),
            "notes": _clean(w.get("notes"), 120),
        })
    date_nights = [_clean(d, 120) for d in (obj.get("date_nights") or [])
                   [:_MAX_DATE_NIGHTS] if _clean(d, 120)]
    notes = [_clean(n, 160) for n in (obj.get("notes") or [])[:6]
             if _clean(n, 160)]
    if not weekends and not date_nights and not notes:
        return None
    return {"weekends": weekends, "date_nights": date_nights,
            "notes": notes}


def elicit(text: str, *, today_iso: str,
           llm: Callable[[str], str] | None = None) -> dict | None:
    """Free text → typed proposals. LLM elicits; everything else is
    deterministic. None on ANY failure (caller stores raw). No search
    grounding — this is parsing, not discovery."""
    prompt = _elicit_prompt(text, today_iso)
    try:
        if llm is not None:
            raw = llm(prompt) or ""
        else:
            import os
            if os.getenv("RAHAT_TEST_MODE") == "1":
                return None      # hermetic: no wire unless a seam is injected
            from core import llm as _llm
            usage = _llm.generate("genie", "genie.ideas.elicit",
                                  prompt=prompt)
            if usage.error:
                logger.warning("idea elicitation LLM error: %s", usage.error)
                return None
            raw = usage.text
    except Exception as e:  # noqa: BLE001 — incl. BudgetExceeded
        logger.warning("idea elicitation failed (%s: %s)",
                       type(e).__name__, e)
        return None
    obj = _parse_json_block(raw)
    if obj is None:
        logger.warning("idea elicitation returned unparseable output")
        return None
    return _coerce(obj)
