"""core.hours — hours text → weekday intervals, and "open at arrival"
(ADR-018 §2.2 Verify; platform PR-G3, PR-G4).

Hours arrive as text from a site, an ordering page or a seed:
"Mon–Sun 11:30–22:00", "Daily 8–15", "Tue–Sun 11–21",
"Sat 11:30–15:00, 16:00–22:00", "Wed 4:30–9", "7 AM–3 AM",
"Mon–Fri 7–2:30; Sat 8–3; closed Sun", "lunch 11:30–3, dinner 5–9:30".
`parse()` turns that into intervals per weekday (0 = Monday) with a
service tag when the text names one. `open_at()` answers the question
the verifier asks: open at this minute on this weekday, how long until
the close, and the last-seating margin.

Heuristics for a missing AM/PM: an hour below 7 is PM; an end at or
before its start is PM (or after midnight when it is still before the
start as PM). Every parsed interval keeps the text it came from so a
reply can show it. When the text cannot be parsed, `parse()` returns
no intervals and the caller must say "hours unconfirmed".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_ALIASES = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
                "friday": "fri", "saturday": "sat", "sunday": "sun", "tues": "tue", "thurs": "thu",
                "weds": "wed"}
_SERVICES = ("breakfast", "brunch", "lunch", "dinner", "kitchen", "bar", "takeout", "happy hour")

_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|noon|midnight)?", re.I)
_RANGE_RE = re.compile(
    r"(?P<a>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|noon|midnight)\s*(?:–|—|-|to|until|till)\s*"
    r"(?P<b>\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|noon|midnight|close|late)", re.I)
_DAYSPEC_RE = re.compile(
    r"\b(daily|every ?day|weekdays?|weekends?|mon|tue|tues|wed|weds|thu|thurs|fri|sat|sun|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)\b(?:\s*(?:–|—|-|to|through|thru)\s*"
    r"(mon|tue|tues|wed|weds|thu|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)?",
    re.I)


@dataclass
class Interval:
    weekday: int
    open_min: int
    close_min: int           # may exceed 1440 for after-midnight closes
    service: str = "all"
    text: str = ""


@dataclass
class Hours:
    intervals: list[Interval] = field(default_factory=list)
    closed_days: set[int] = field(default_factory=set)
    unparsed: list[str] = field(default_factory=list)
    source_text: str = ""

    @property
    def parsed(self) -> bool:
        return bool(self.intervals) or bool(self.closed_days)


def _day_index(tok: str) -> int | None:
    t = _DAY_ALIASES.get(tok.lower(), tok.lower()[:3])
    return DAYS.index(t) if t in DAYS else None


def _days_from(spec: re.Match | None) -> list[int]:
    if spec is None:
        return list(range(7))
    a = spec.group(1).lower()
    if a.startswith("daily") or a.startswith("every"):
        return list(range(7))
    if a.startswith("weekday"):
        return [0, 1, 2, 3, 4]
    if a.startswith("weekend"):
        return [5, 6]
    start = _day_index(a)
    if start is None:
        return list(range(7))
    if spec.group(2):
        end = _day_index(spec.group(2))
        if end is None:
            return [start]
        if end >= start:
            return list(range(start, end + 1))
        return list(range(start, 7)) + list(range(0, end + 1))
    return [start]


def _to_minutes(tok: str, *, default_pm_below: int = 7, ref_start: int | None = None) -> int | None:
    t = tok.strip().lower()
    if t == "noon":
        return 12 * 60
    if t == "midnight":
        return 24 * 60
    if t in ("close", "late"):
        return None
    m = _TIME_RE.match(t)
    if not m:
        return None
    h, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if h > 24 or mm > 59:
        return None
    if ap.startswith("a"):
        if h == 12:
            h = 0
    elif ap.startswith("p"):
        if h != 12:
            h += 12
    else:
        if h < default_pm_below and h != 0:          # "4:30–9" is dinner
            h += 12
        elif ref_start is not None and h * 60 + mm <= ref_start and h <= 12:
            h += 12
    return h * 60 + mm


_CLOSED_RE = re.compile(r"\bclosed\b", re.I)
_ALLDAY_RE = re.compile(r"\b24\s*(?:hours|hrs|h)\b|\b24/7\b|\bopen 24\b", re.I)


def _segments(text: str) -> list[str]:
    """Split hours text into clauses, one per day spec that owns its times.

    Hard separators first (';', newlines, bullets, '|'). Then a new clause
    starts at a day spec once the current clause already holds a time
    range (or a "closed") and the day spec is itself followed by a range
    (or a "closed"). So "Sun–Tue 11 AM–9 PM, Wed–Sat 11 AM–10 PM" and
    Google's "Monday: 12pm-11pm. Tuesday: 12pm-11pm." split per day, while
    "Mon, Wed, Fri 9–5" stays one clause (its days are unioned) and
    "Sat 11:30–15:00, 16:00–22:00" keeps both ranges on Saturday."""
    out: list[str] = []
    text = re.sub(r"\([^)]*\)", " ", text or "")       # a parenthetical note is not hours
    for seg in re.split(r"[;\n·|]+", text):
        specs = list(_DAYSPEC_RE.finditer(seg))
        if len(specs) <= 1:
            out.append(seg)
            continue
        cuts = [0]
        for i, m in enumerate(specs[1:], start=1):
            since = seg[cuts[-1]:m.start()]
            nxt = specs[i + 1].start() if i + 1 < len(specs) else len(seg)
            after = seg[m.end():nxt]
            owns_before = _RANGE_RE.search(since) or _CLOSED_RE.search(since) or _ALLDAY_RE.search(since)
            owns_after = _RANGE_RE.search(after) or _CLOSED_RE.search(after) or _ALLDAY_RE.search(after)
            if owns_before and owns_after:
                cuts.append(m.start())
        cuts.append(len(seg))
        for a, b in zip(cuts, cuts[1:]):
            piece = re.sub(r"\b(?:and|&)\s*$", "", seg[a:b].strip(" ,.")).strip(" ,.&")
            if piece:
                out.append(piece)
    return out


def _clause_days(seg: str) -> list[int]:
    """The days a clause is about: every day spec before its first time
    range (or anywhere in it when the days come after the times); every
    day when it names none."""
    rng = _RANGE_RE.search(seg)
    head = seg[:rng.start()] if rng else seg
    specs = list(_DAYSPEC_RE.finditer(head)) or list(_DAYSPEC_RE.finditer(seg))
    if not specs:
        return list(range(7))
    return sorted({d for s in specs for d in _days_from(s)})


def parse(text: str) -> Hours:
    out = Hours(source_text=text or "")
    if not text:
        return out
    for seg in _segments(text):
        seg = seg.strip(" ,.")
        if not seg:
            continue
        low = seg.lower()
        if _CLOSED_RE.search(low) and not _RANGE_RE.search(low):
            if _DAYSPEC_RE.search(low):
                out.closed_days.update(_clause_days(low))
                continue
        days = _clause_days(seg)
        if _ALLDAY_RE.search(low) and not _RANGE_RE.search(low):
            for d in days:
                out.intervals.append(Interval(weekday=d, open_min=0, close_min=24 * 60, service="all", text=seg))
            continue
        service = next((s for s in _SERVICES if s in low), "all")
        ranges = list(_RANGE_RE.finditer(seg))
        if not ranges:
            out.unparsed.append(seg)
            continue
        for r in ranges:
            a = _to_minutes(r.group("a"))
            if a is None:
                out.unparsed.append(seg)
                continue
            b = _to_minutes(r.group("b"), ref_start=a)
            if b is None:                       # "to close" / "late": assume 22:00
                b = max(a + 60, 22 * 60)
            if b <= a:                          # after midnight
                b += 24 * 60
            for d in days:
                out.intervals.append(Interval(weekday=d, open_min=a, close_min=b,
                                              service=service, text=seg))
    return out


def open_at(hours: Hours, weekday: int, minute: int, *, last_seating_before_close_min: int = 0
            ) -> dict:
    """Is the place serving at `minute` (minutes since midnight) on
    `weekday`? Considers an interval that started the previous day and
    runs past midnight. Returns closes_in_min (None when closed), the
    interval text, and whether the last-seating margin is met."""
    if weekday in hours.closed_days and not any(i.weekday == weekday for i in hours.intervals):
        return {"open": False, "why": "closed that day", "closes_in_min": None, "interval": None}
    best: Interval | None = None
    for iv in hours.intervals:
        if iv.weekday == weekday and iv.open_min <= minute < iv.close_min:
            best = iv
            break
        prev = (weekday - 1) % 7
        if iv.weekday == prev and iv.close_min > 24 * 60 and minute < iv.close_min - 24 * 60:
            best = iv
            break
    if best is None:
        if not hours.parsed:
            return {"open": None, "why": "hours unconfirmed", "closes_in_min": None, "interval": None}
        nxt = sorted((iv.open_min for iv in hours.intervals if iv.weekday == weekday and iv.open_min > minute))
        why = f"opens at {fmt(nxt[0])}" if nxt else "not open at that time"
        return {"open": False, "why": why, "closes_in_min": None, "interval": None}
    close = best.close_min if best.weekday == weekday else best.close_min - 24 * 60
    closes_in = close - minute
    return {"open": True, "why": "", "closes_in_min": closes_in, "interval": best.text,
            "last_seating_ok": closes_in >= last_seating_before_close_min,
            "closes_at": fmt(close % (24 * 60)), "service": best.service}


def fmt(minute: int) -> str:
    h, m = divmod(minute % (24 * 60), 60)
    ap = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {ap}" if m else f"{h12} {ap}"
