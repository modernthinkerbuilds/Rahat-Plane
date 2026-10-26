"""bridges.events.standing — something special, or just open (owner,
2026-10-26).

THE COMPLAINT. "Every week the events are around the Botanical Garden,
this museum ... go to the Botanical Garden, go to [the museum], go to
all these places that are open. I just want it if something special is
happening there. If not, there's no point going." The inventory proved
it: the garden's "Free Admission" row is on file for twenty days in a
row, a museum's resident free-admission row every Saturday,
and the weekly programs (storytime, drop-in play, ask-a-gardener) eight
to sixteen times each, while the same venues also list the real
one-offs (a Halloween day, Día de los Muertos, a choir, a bat walk).

THE RULE (code, no model). A row is one of:
  * special: its title names an occasion (a festival, a celebration, a
    holiday, a parade, an opening ...). Always shown, even when it recurs.
  * admission: an admission notice (a free day, resident admission,
    "free first Sunday"), not an event. Hidden.
  * standing: a regular program, on STANDING_DATES or more distinct
    dates in the inventory, a run longer than ONGOING_DAYS, or regular by
    nature (a farmers market, a storytime, a drop-in). Hidden, unless
    the household keeps it (`keep` words, e.g. "storytime").
  * event: everything else (a one-off or a short series). Shown.
Hidden rows are never lost: `/whatson all` shows them, and the lists
say how many were left out.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

STANDING_DATES = 4          # a series on this many distinct dates is a regular program
ONGOING_DAYS = 7            # a row that runs longer than this is a standing listing

SPECIAL_RE = re.compile(
    r"\b(?:festival|fest|celebrat\w*|halloween|trick[\s-]*or[\s-]*treat|trunk[\s-]*or[\s-]*treat|d[ií]a de (?:los )?muertos"
    r"|diwali|lunar new year|holiday|christmas|hanukkah|kwanzaa|thanksgiving|new year'?s|parade|fair|carnival|"
    r"grand opening|opening (?:day|night|weekend|celebration|reception)|premiere|"
    r"night market|fireworks|light show|lantern|exhibition opening|member preview|last chance|closing day)\b",
    re.I)
ADMISSION_RE = re.compile(
    r"\b(?:admission|free entry|free day|free (?:first|1st|second|third)\s+(?:mon|tue|wed|thu|fri|sat|sun)\w*"
    r"|first (?:mon|tue|wed|thu|fri|sat|sun)\w*s?\s+(?:at|free)|free first \w+|museums on us|general admission|"
    r"open (?:daily|today|to the public)|residents? (?:free|day|admission)|pay what you wish)\b", re.I)


# Regular by nature, whatever the feed's history says (a feed that lists only the next date of a weekly
# market gives no recurrence to count).
REGULAR_TITLE_RE = re.compile(
    r"\b(?:farmers'?\s*market|farmer'?s'?\s*market|story\s*time|storytime|drop[\s-]*in|open studio|open play|"
    r"docent tour|daily tour|office hours)\b", re.I)


def _days(row: dict) -> float:
    s, e = str(row.get("start_ts") or ""), str(row.get("end_ts") or "")
    try:
        a = datetime.strptime(s[:10], "%Y-%m-%d")
        b = datetime.strptime(e[:10], "%Y-%m-%d")
    except ValueError:
        return 0.0
    return (b - a).days


def kind(row: dict, *, recurrence: int = 1, keep: Iterable[str] = ()) -> str:
    """special | admission | standing | event, for one inventory row.
    `recurrence` is the row's series' distinct dates (store.recurrence_counts)."""
    title = str(row.get("title") or "")
    if SPECIAL_RE.search(title):
        return "special"
    if ADMISSION_RE.search(title):
        return "admission"
    low = title.lower()
    if recurrence >= STANDING_DATES or _days(row) > ONGOING_DAYS or REGULAR_TITLE_RE.search(title):
        if any(k and k.lower() in low for k in keep):
            return "event"
        return "standing"
    return "event"


def split(rows: list[dict], *, recurrence: dict[str, int] | None = None, keep: Iterable[str] = (),
          show_all: bool = False) -> tuple[list[dict], list[dict]]:
    """(shown, hidden), order kept. `recurrence` maps a series key to its
    distinct dates; read from the store when not given (never raises:
    without it, only admission notices and long runs are hidden)."""
    if show_all:
        return list(rows), []
    from bridges.events.store import series_key
    if recurrence is None:
        try:
            from bridges.events.store import recurrence_counts
            recurrence = recurrence_counts()
        except Exception:  # noqa: BLE001 - no store, no recurrence
            recurrence = {}
    keep = [k for k in keep if k]
    shown, hidden = [], []
    for r in rows:
        n = recurrence.get(series_key(str(r.get("title") or ""), str(r.get("venue") or "")), 1)
        (hidden if kind(r, recurrence=n, keep=keep) in ("admission", "standing") else shown).append(r)
    return shown, hidden


def hidden_note(hidden: list[dict]) -> str | None:
    """One line saying what was left out, or None."""
    if not hidden:
        return None
    n = len({str(r.get("title") or "").casefold() for r in hidden})
    return (f"_Left out {n} regular listing{'s' if n != 1 else ''} (free-admission days, weekly programs, "
            f"places that are just open). `/whatson all` shows everything._")


GENERIC_VISIT_RE = re.compile(
    r"^\s*(?:visit|explore|stroll|wander|walk(?:\s+(?:around|through))?|tour|see|check out|enjoy|spend|relax|"
    r"picnic|hike|play at|browse|discover)\b", re.I)


def is_generic_visit(activity: str) -> bool:
    """A suggested outing that is just going somewhere ("Visit the
    Botanical Garden", "Explore the museum") with no occasion named."""
    return bool(GENERIC_VISIT_RE.search(activity or "")) and not SPECIAL_RE.search(activity or "")
