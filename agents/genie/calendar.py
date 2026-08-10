"""Genie household calendar — capture, conflicts, rendering (2026-08-10).

Owner request (verbatim intent): "through the week my wife and I come
across events or commitments — 'we have to go to Navya's home for
lunch on Saturday', a temple visit, a sleepover… give Genie a calendar
for us, point out conflicts when giving schedules from the discovery
engine — 'this event is available, but you have a temple visit; want
to swap it, or which one do you want?' — and it has to stay in sync
across my chat, my wife's chat, and Bade Miya. Not just a
recommendation engine — a scheduling engine for the weekend."

THE SHAPE (PRD non-negotiable preserved):
  * The LLM only ELICITS — free text → typed calendar entries with
    normalized dates/times. It never decides what wins a conflict; the
    humans do ("which one do you want?").
  * Storage lives in agents.genie.state (charter-gated, single vault
    store) — sync across all three channels is by construction, not by
    replication.
  * Conflict math is DETERMINISTIC and lives here: interval overlap
    when both sides are timed; a same-day untimed commitment is a soft
    conflict (flagged as "timing TBC").
  * Deterministic fallback parser covers the common shapes (day word +
    meal word / explicit time) so capture works even with the LLM down.

Failure contract: elicit() returns None on any trouble; the caller
falls back to fallback_parse(); nothing here ever raises into route().
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Callable

from agents.genie.live_plan import _parse_json_block, _clean

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 6
_DEFAULT_EVENT_HOURS = 2        # assumed duration when an end is unknown


# ─────────────────────────── elicitation ───────────────────────────
def _elicit_prompt(text: str, today_iso: str) -> str:
    return f"""You extract calendar entries from a family's own message. Today is
{today_iso} ({datetime.strptime(today_iso, '%Y-%m-%d').strftime('%A')}).
Do NOT invent, plan, or decide anything — only extract what they said.

Rules:
- "date": resolve day words to ISO dates ("Saturday" → the upcoming
  Saturday, "tomorrow", "next Sunday", explicit dates). "" if unclear.
- "start"/"end": "HH:MM" 24h. Meal words imply windows (lunch ≈
  12:00–14:00, dinner ≈ 18:00–20:30, snack ≈ 16:00–17:30, sleepover ≈
  18:00 onward). "" when no timing is stated or implied.
- "kind": "commitment" when they HAVE to / are going to attend
  (get-together, temple visit, invited to X); "wishlist" when they
  merely WANT to attend something they spotted (an event from
  Instagram, a show they saw advertised).
- "title": short, their own words ("Lunch at Navya's home").
- "where": venue/host if mentioned, else "".

Return STRICT JSON only, no fences:
{{"entries": [{{"title": "...", "date": "YYYY-MM-DD or ''",
               "start": "", "end": "", "where": "",
               "kind": "commitment"}}]}}

THE MESSAGE:
{text}"""


def elicit(text: str, *, today_iso: str,
           llm: Callable[[str], str] | None = None) -> list[dict] | None:
    """Free text → typed calendar entries. None on ANY failure (caller
    falls back to the deterministic parser)."""
    prompt = _elicit_prompt(text, today_iso)
    try:
        if llm is not None:
            raw = llm(prompt) or ""
        else:
            import os
            if os.getenv("RAHAT_TEST_MODE") == "1":
                return None      # hermetic: no wire without a seam
            from core import llm as _llm
            usage = _llm.generate("genie", "genie.calendar.elicit",
                                  prompt=prompt)
            if usage.error:
                logger.warning("calendar elicit LLM error: %s", usage.error)
                return None
            raw = usage.text
    except Exception as e:  # noqa: BLE001 — incl. BudgetExceeded
        logger.warning("calendar elicit failed (%s: %s)",
                       type(e).__name__, e)
        return None
    obj = _parse_json_block(raw)
    if not isinstance(obj, dict):
        return None
    out = []
    for e in (obj.get("entries") or [])[:_MAX_ENTRIES]:
        if not isinstance(e, dict):
            continue
        title = _clean(e.get("title"), 120)
        date = _clean(e.get("date"), 10)
        if not title or len(date) != 10:
            continue
        out.append({
            "title": title, "date": date,
            "start": _clean(e.get("start"), 5),
            "end": _clean(e.get("end"), 5),
            "where": _clean(e.get("where"), 120),
            "kind": e.get("kind") if e.get("kind") in
            ("commitment", "wishlist") else "commitment",
        })
    return out or None


# ──────────────────── deterministic fallback parser ───────────────────
_DAY_WORDS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
              "friday": 4, "saturday": 5, "sunday": 6}
_MEAL_WINDOWS = {"breakfast": ("09:00", "10:30"),
                 "brunch": ("10:30", "12:30"),
                 "lunch": ("12:00", "14:00"),
                 "snack": ("16:00", "17:30"),
                 "dinner": ("18:00", "20:30"),
                 "sleepover": ("18:00", "23:59"),
                 "morning": ("09:00", "12:00"),
                 "afternoon": ("13:00", "17:00"),
                 "evening": ("17:00", "21:00"),
                 "tonight": ("18:00", "22:00")}
_TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
                      re.I)
_WISH_RE = re.compile(r"\b(?:want|would\s+(?:love|like))\s+to\s+"
                      r"(?:attend|go|check|see|do)\b", re.I)


def fallback_parse(text: str, now: datetime) -> dict | None:
    """Day word (+ meal word / explicit time) → one entry. None when no
    date can be resolved (never guess a date for a commitment)."""
    low = (text or "").lower()
    date: str | None = None
    if "tomorrow" in low:
        date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    elif "today" in low or "tonight" in low:
        date = now.strftime("%Y-%m-%d")
    else:
        for word, wd in _DAY_WORDS.items():
            if re.search(rf"\b{word}\b", low):
                ahead = (wd - now.weekday()) % 7
                date = (now + timedelta(days=ahead)).strftime("%Y-%m-%d")
                break
    if date is None:
        return None
    start = end = ""
    m = _TIME_RE.search(low)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm"
                                       else 0)
        start = f"{hour:02d}:{m.group(2) or '00'}"
        end_h = min(hour + _DEFAULT_EVENT_HOURS, 23)
        end = f"{end_h:02d}:{m.group(2) or '00'}"
    else:
        for word, window in _MEAL_WINDOWS.items():
            if word in low:
                start, end = window
                break
    title = re.sub(r"\s+", " ", (text or "").strip())[:120]
    return {"title": title, "date": date, "start": start, "end": end,
            "where": "",
            "kind": "wishlist" if _WISH_RE.search(low) else "commitment"}


# ─────────────────────────── conflict math ───────────────────────────
def _mins(hhmm: str, default: int) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def conflicts_for(date: str, start: str, end: str,
                  entries: list[dict]) -> list[dict]:
    """Calendar entries conflicting with a (date, start–end) slot.

    Both timed → interval overlap (unknown end = start + 2h). An
    UNTIMED commitment on the same day is a soft conflict — the humans
    said they're busy that day but not when; flag it, marked soft."""
    out = []
    for e in entries:
        if e.get("date") != date or e.get("kind") == "wishlist":
            continue
        if not start or not e.get("start"):
            out.append(dict(e, soft=True))
            continue
        s1 = _mins(start, 0)
        e1 = _mins(end, s1 + _DEFAULT_EVENT_HOURS * 60)
        s2 = _mins(e["start"], 0)
        e2 = _mins(e.get("end", ""), s2 + _DEFAULT_EVENT_HOURS * 60)
        if s1 < e2 and s2 < e1:
            out.append(dict(e, soft=False))
    return out


def event_conflicts(event_row: dict, entries: list[dict]) -> list[dict]:
    """Conflicts for an inventory/discovery event row (start_ts
    'YYYY-MM-DD HH:MM:SS'). All-day rows (00:00) count as untimed."""
    ts = str(event_row.get("start_ts") or "")
    date, hhmm = ts[:10], ts[11:16]
    if hhmm == "00:00":
        hhmm = ""
    end = str(event_row.get("end_ts") or "")[11:16]
    return conflicts_for(date, hhmm, end, entries)


def conflict_note(hits: list[dict]) -> str:
    """One human line: '⚠️ conflicts with Lunch at Navya's (12:00)'."""
    if not hits:
        return ""
    h = hits[0]
    when = (f" ({h['start']}" + (f"–{h['end']}" if h.get("end") else "")
            + ")") if h.get("start") else " (timing TBC)"
    tail = " — swap it in, or keep the commitment?" if not h.get("soft") \
        else " — timing TBC, could still fit"
    return f"⚠️ you have {h['title']}{when}{tail}"


# ─────────────────────────── rendering ───────────────────────────
def entry_line(e: dict) -> str:
    when = e.get("start") or "time TBC"
    if e.get("start") and e.get("end"):
        when = f"{e['start']}–{e['end']}"
    mark = "📌" if e.get("kind") != "wishlist" else "⭐"
    line = f"  {mark} {when} — {e['title']}"
    if e.get("where"):
        line += f" @ {e['where']}"
    if e.get("by"):
        line += f" _(added by {e['by']})_"
    return line


def render(entries: list[dict], now: datetime) -> str:
    """The /calendar view — next 14 days, grouped by day."""
    if not entries:
        return ("The household calendar is empty. Tell me things like "
                "\"we have lunch at Navya's on Saturday\" or "
                "\"add temple visit Sunday morning\" and I'll track "
                "them — and warn you when event suggestions clash.")
    by_day: dict[str, list[dict]] = {}
    for e in entries:
        by_day.setdefault(e["date"], []).append(e)
    lines = ["*Household calendar* — 📌 committed · ⭐ want to attend"]
    for day in sorted(by_day):
        d = datetime.strptime(day, "%Y-%m-%d")
        lines += ["", f"*{d.strftime('%A')} {d.strftime('%b')} {d.day}*"]
        lines += [entry_line(e) for e in by_day[day]]
    lines += ["", "_Say \"remove <name>\" from `/calendar`, or add more "
                  "anytime — everyone in the household sees the same "
                  "calendar._"]
    return "\n".join(lines)
