"""core.harness.numbers — numbers from records (ADR-018 A6, addition 1).

A core capability a tenant turns on in its manifest
(`capabilities.numbers_from_records`); off by default, so the first
tenant's prices, hours and walking minutes are untouched.

The model never writes a digit the reader sees. It writes a placeholder,
`{{r:<record id>}}`, and the renderer prints the record's value in the
reader's units; a digit run outside a placeholder is a stray digit and
fails the check. Three pieces, all pure:

  placeholders(text)        the (id, format) pairs a text carries
  stray_digits(text, allow) digit runs outside placeholders and outside
                            the allowed tokens ("Zone 2", "5K", a time of
                            day the tenant whitelists)
  substitute(text, records, units)
                            the text with every placeholder replaced by
                            its record's value in the reader's units, and
                            the ids that had no record

Formats after a bar: `{{r:<id>|range}}` prints lo–hi, `{{r:<id>|bare}}`
the number without its unit, `{{r:<id>|int}}` rounded to a whole
number, `{{r:<id>|dec}}` with one decimal, `{{r:<id>|signed}}` with its sign. The unit decides the shape:
s/mi → m:ss/mi, km → km with miles beside it when the reader wants
both, h → NhMMm, bpm/min/%/°F/ft/m/s/count → the number and its unit.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

PLACEHOLDER = re.compile(r"\{\{\s*r:([^}|\s]+)\s*(?:\|\s*([a-z]+)\s*)?\}\}")
DIGITS = re.compile(r"\d[\d,.:]*")
DEFAULT_ALLOW = ("Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5", "Z2", "Z3")


def placeholders(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), (m.group(2) or "").lower()) for m in PLACEHOLDER.finditer(text or "")]


def stray_digits(text: str, allow: Iterable[str] = DEFAULT_ALLOW) -> list[str]:
    """Digit runs outside placeholders and outside the allowed tokens
    (case-insensitive, whole phrases)."""
    stripped = PLACEHOLDER.sub(" ", text or "")
    for tok in allow:
        if tok:
            stripped = re.sub(re.escape(tok), " ", stripped, flags=re.I)
    return [m.group(0) for m in DIGITS.finditer(stripped)]


def mmss(seconds: float) -> str:
    seconds = float(seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    m, s = divmod(int(round(seconds)), 60)
    return f"{sign}{m}:{s:02d}"


def hm(hours: float) -> str:
    total = int(round(float(hours) * 60))
    h, m = divmod(total, 60)
    return f"{h}h{m:02d}m" if h else f"{m} min"


def _num(v: float, nd: int) -> str:
    v = float(v)
    if nd == 0:
        return f"{int(round(v))}"
    return f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd > 0 else f"{v}"


def format_value(value: Any, unit: str, fmt: str = "", *, units: dict | None = None) -> str:
    """One record's value in the reader's units."""
    units = units or {}
    unit = (unit or "").strip()
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) or unit in ("name", "grade", "flag", "local", "text", ""):
        return str(value)
    v = float(value)
    bare = fmt == "bare"
    if fmt == "signed":
        body = format_value(value, unit, "", units=units)
        return ("+" + body) if v > 0 else body
    if unit == "s/mi":
        return mmss(v) + ("" if bare else "/mi")
    if unit == "s/km":
        return mmss(v) + ("" if bare else "/km")
    if unit == "km":
        km = _num(v, 1 if fmt != "int" else 0)
        if units.get("distance") == "km+mi" and not bare:
            return f"{km} km ({_num(v / 1.609344, 1)} mi)"
        return km + ("" if bare else " km")
    if unit == "mi":
        return _num(v, 1 if fmt != "int" else 0) + ("" if bare else " mi")
    if unit == "h":
        return hm(v) if not bare else _num(v, 1)
    if unit == "min":
        return _num(v, 0 if (fmt == "int" or abs(v - round(v)) < 0.05) else 1) + ("" if bare else " min")
    if unit in ("bpm", "steps/min"):
        return _num(v, 1 if fmt == "dec" else 0) + ("" if bare else f" {unit}")
    if unit == "%":
        return _num(v, 0 if fmt == "int" or abs(v - round(v)) < 0.05 else 1) + ("" if bare else "%")
    if unit in ("°F", "°C"):
        return _num(v, 0) + ("" if bare else unit)
    if unit in ("ft", "m", "s", "kcal", "kcal/km", "count", "TRIMP", "mph"):
        nd = 0 if (fmt == "int" or unit in ("ft", "m", "s", "kcal", "count") or abs(v - round(v)) < 0.05) else 1
        return _num(v, nd) + ("" if bare or unit == "count" else f" {unit}")
    return _num(v, 1) + ("" if bare else f" {unit}")


def substitute(text: str, records: Any, *, units: dict | None = None, missing: str = "[no record]") -> tuple[str, list[str]]:
    """Every placeholder replaced by its record's value; the ids with no
    record are listed (and printed as `missing`). `records` answers
    `.get(id)` with an object carrying value, unit, lo, hi (a RecordSet),
    or is a dict of id → such an object or → {"value", "unit", ...}."""
    absent: list[str] = []

    def lookup(rid: str):
        if hasattr(records, "get"):
            return records.get(rid)
        return None

    def field(rec, name):
        if rec is None:
            return None
        if isinstance(rec, dict):
            return rec.get(name)
        return getattr(rec, name, None)

    def repl(m: re.Match) -> str:
        rid, fmt = m.group(1), (m.group(2) or "").lower()
        rec = lookup(rid)
        if rec is None:
            absent.append(rid)
            return missing
        unit = field(rec, "unit") or ""
        if fmt == "range":
            lo, hi = field(rec, "lo"), field(rec, "hi")
            if lo is None or hi is None:
                return format_value(field(rec, "value"), unit, "", units=units)
            return f"{format_value(lo, unit, 'bare', units=units)}–{format_value(hi, unit, '', units=units)}"
        if fmt == "signed":
            return format_value(field(rec, "value"), unit, "signed", units=units)
        return format_value(field(rec, "value"), unit, fmt, units=units)

    return PLACEHOLDER.sub(repl, text or ""), absent


def check_text(text: str, records: Any, *, allow: Iterable[str] = DEFAULT_ALLOW) -> dict:
    """The two mechanical verdicts on one text: the stray digits it
    carries and the placeholder ids with no record."""
    ids = [rid for rid, _ in placeholders(text)]
    absent = [rid for rid in ids if (records.get(rid) if hasattr(records, "get") else None) is None]
    return {"stray": stray_digits(text, allow), "absent": absent, "placeholders": ids}
