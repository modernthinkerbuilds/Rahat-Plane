"""Steps over a period, from the Health records (2026-10-27).

LIVE (Oct 27, 16:21): "How many steps do I average in the last 4 months?"
went to Miya's synthesizer, which holds no step history. With the Gemini
project over its monthly spend cap the model call failed, and the reply
was the debug fallback: the question echoed back. A number like this
never needed a model.

Deterministic: each day's total from raw_vitals (metric 'steps', the
Health export's minute points), averaged over the asked period's complete
days. Today is left out of an average (it is still counting). When the
records start later than the period asked, the answer says what it
covers instead of pretending to cover all of it.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta

# A question about steps already taken — not a hypothetical, a target, a
# statement of his own numbers ("9601 steps a day") or a movement ("step-ups").
STEPS_QUERY_RE = re.compile(
    r"^(?!.*\b(?:should|need|needs|target|goal|left|more|plan|if|assume|assuming)\b)"
    r"(?!.*\bstep\s*(?:by\s*step|[\s-]*ups?|[\s-]*overs?|[\s-]*downs?|back)\b)"
    r"(?!.*\b\d[\d,]{2,}\s*steps\b)"
    r"(?=.*\b(?:steps|step\s*count)\b)"
    r"(?=.*\b(?:how\s+many|average|averaged|averaging|avg|mean|total|per\s+day|a\s+day|daily|count|"
    r"so\s+far|did\s*i|have\s+i|my\s+steps|today|yesterday)\b)",
    re.I)

_N = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_LAST_N_RE = re.compile(rf"\b(?:last|past|previous)\s+{_N}?\s*(day|week|month|year)s?\b", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_SINCE_RE = re.compile(r"\bsince\s+(" + "|".join(m[:3] + r"\w*" for m in _MONTHS) + r")\b", re.I)


def _months_back(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 1)


def period(text: str, today: date) -> tuple[date, date, str, bool]:
    """(first, last, label, single_day) for the period the message names; default the last 30 days."""
    t = (text or "").lower()
    yesterday = today - timedelta(days=1)
    if re.search(r"\byesterday\b", t):
        return yesterday, yesterday, "yesterday", True
    if re.search(r"\btoday\b|\bso\s+far\s+today\b", t):
        return today, today, "today", True
    m = _LAST_N_RE.search(t)
    if m:
        word, unit = (m.group(1) or "").lower(), m.group(2).lower()
        if not word and re.search(rf"\blast\s+{unit}\b", t):
            # "last month" / "last week" / "last year": the previous calendar one
            if unit == "week":
                mon = today - timedelta(days=today.weekday() + 7)
                return mon, mon + timedelta(days=6), "last week", False
            if unit == "month":
                end = today.replace(day=1) - timedelta(days=1)
                return end.replace(day=1), end, end.strftime("%B"), False
            if unit == "year":
                return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), str(today.year - 1), False
        n = int(word) if word.isdigit() else _NUM.get(word, 1)
        first = {"day": lambda: today - timedelta(days=n), "week": lambda: today - timedelta(weeks=n),
                 "month": lambda: _months_back(today, n),
                 "year": lambda: _months_back(today, 12 * n)}[unit]()
        label = f"the last {n} {unit}{'s' if n != 1 else ''}" if n != 1 else f"the last {unit}"
        return first, yesterday, label, False
    m = _SINCE_RE.search(t)
    if m:
        mon = next(i for i, name in enumerate(_MONTHS, 1) if name.startswith(m.group(1).lower()[:3]))
        year = today.year if mon <= today.month else today.year - 1
        return date(year, mon, 1), yesterday, f"since {_MONTHS[mon - 1].title()}", False
    if re.search(r"\bthis\s+week\b|\bweek\s+so\s+far\b", t):
        return today - timedelta(days=today.weekday()), yesterday, "this week", False
    if re.search(r"\bthis\s+month\b", t):
        return today.replace(day=1), yesterday, "this month", False
    if re.search(r"\bthis\s+year\b|\bytd\b|\byear\s+to\s+date\b", t):
        return date(today.year, 1, 1), yesterday, "this year", False
    return today - timedelta(days=30), yesterday, "the last 30 days", False


def daily_totals(con: sqlite3.Connection, first: date, last: date) -> dict[str, float]:
    """Day → total steps, for the days in [first, last] that have any records."""
    rows = con.execute(
        "SELECT substr(timestamp, 1, 10), SUM(value) FROM raw_vitals WHERE metric_type = 'steps' "
        "AND timestamp >= ? AND timestamp < ? GROUP BY 1 ORDER BY 1",
        (first.isoformat(), (last + timedelta(days=1)).isoformat())).fetchall()
    return {d: float(v or 0) for d, v in rows if d}


def _fmt_day(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _n(x: float) -> str:
    return f"{int(round(x)):,}"


def answer(text: str, *, now: datetime | None = None, db_path: str | None = None) -> str:
    """The reply to a steps question, from the records. Never raises a model call."""
    now = now or datetime.now()
    today = now.date()
    first, last, label, single = period(text, today)
    if db_path is None:
        from core import io as cio
        db_path = str(cio.DB_PATH)
    con = sqlite3.connect(db_path)
    try:
        days = daily_totals(con, first, last)
        first_ever = con.execute("SELECT MIN(substr(timestamp, 1, 10)), MAX(timestamp) FROM raw_vitals "
                                 "WHERE metric_type = 'steps'").fetchone()
    finally:
        con.close()
    if single:
        total = days.get(first.isoformat())
        if total is None:
            return f"I have no step records for {label} yet — the Health export hasn't sent any."
        if first == today:
            synced = str(first_ever[1] or "")[11:16]
            return (f"*Steps today:* {_n(total)} so far"
                    + (f" (last synced at {synced})." if synced else "."))
        return f"*Steps {label}:* {_n(total)}."
    if not days:
        start = first_ever[0] if first_ever else None
        return (f"I have no step records for {label}."
                + (f" Your records start {_fmt_day(date.fromisoformat(start))}." if start else ""))
    vals = list(days.values())
    avg = sum(vals) / len(vals)
    d0, d1 = date.fromisoformat(min(days)), date.fromisoformat(max(days))
    lines = [f"*Steps — {label}:* {_n(avg)} a day on average."]
    span = (d1 - d0).days + 1
    gaps = span - len(days)
    ndays = f"{len(days)} day{'s' if len(days) != 1 else ''} with data"
    covers = (f"{_fmt_day(d0)} – {_fmt_day(d1)} ({ndays}" if d0 != d1 else f"{_fmt_day(d0)} ({ndays}")
    covers += f", {gaps} without any)" if gaps else ")"
    if d0 > first + timedelta(days=2):
        whole = f"the full {label[len('the last '):]}" if label.startswith("the last ") else "the whole period"
        lines.append(f"Your step records start {_fmt_day(d0)}, so this is {covers}, not {whole}.")
    else:
        lines.append(f"From {covers}.")
    by_month: dict[str, list[float]] = {}
    for d, v in days.items():
        by_month.setdefault(d[:7], []).append(v)
    if len(by_month) >= 2:
        parts = []
        for ym in sorted(by_month):
            mv = by_month[ym]
            name = date.fromisoformat(ym + "-01").strftime("%b")
            partial = " so far" if ym == today.strftime("%Y-%m") else ""
            parts.append(f"{name} {_n(sum(mv) / len(mv))}{partial}")
        lines.append("By month: " + " · ".join(parts) + ".")
    best = max(days, key=days.get)
    lines.append(f"Best day: {_n(days[best])} ({_fmt_day(date.fromisoformat(best))})."
                 + (" Today isn't counted; it's still going." if last >= today - timedelta(days=1) else ""))
    return "\n".join(lines)
