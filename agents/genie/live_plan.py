"""Genie live discovery + deterministic day sequencing (Phase-0, 2026-08-09).

WHY THIS SHAPE (Genie PRD research doc, private/genie/, 2026-06-18).
The PRD's non-negotiable from the planning literature: "the LLM elicits
and narrates, it does NOT schedule in its head — every LLM-proposed plan
is verified deterministically before display" (LLM+P; Valmeekam). This
module is the smallest honest version of that split, replacing the
scaffold's hardcoded activity menus (live incident 2026-08-08: "the
genie is utterly useless" — it planned nothing):

  * `discover_options()` — the LLM's ONLY job: grounded Google-Search
    discovery of real candidate options (weather + local family
    activities) returned as STRICT JSON. It proposes; it never sequences.
  * `sequence_day()` — DETERMINISTIC day assembly: energy budget caps
    how many outings fit, the nap window is protected whenever a
    toddler/newborn Subject is in scope, items are ordered by time slot,
    and everything that didn't fit is surfaced as a glass-box
    "ruled out" line (PRD differentiator #3) — never silently dropped.

Explicitly NOT claimed (PRD honesty legend): no OPTW solver, no
per-member conflict ledger, no outcome memory — those remain [NEW]/[BET].

Sovereignty (NFR-Privacy): the household location comes from .env /
vault (state.household_location()); it is passed to the search prompt
and never persisted here. No location string lives in the repo.

Failure contract: every path returns None on trouble (no key, budget
exceeded, bad JSON, empty options) — the caller falls back to the
static offline menus, so /weekend_plan can never go silent.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Time-slot ordering for deterministic assembly. Unknown hints sort as
# "morning" so an unparseable slot can't push an item past the nap block.
_SLOTS = ("morning", "midday", "afternoon", "evening")
_SLOT_ORDER = {s: i for i, s in enumerate(_SLOTS)}

# Concrete time windows per slot (J1: a TIME-SEQUENCED plan, not vibes).
# Deterministic household-rhythm defaults; a future profile field can
# override. Rendered into every plan line.
SLOT_WINDOWS = {
    "morning": "9:00–11:30",
    "midday": "12:30–3:00",
    "afternoon": "3:30–5:30",
    "evening": "6:00–9:00",
}

# Energy budget → max outings per day (the deterministic cap the PRD's
# solver will eventually replace; conservative on purpose).
_ENERGY_CAP = {"low": 1, "medium": 2, "high": 3}

_NAP_LINE = "Midday: naps protected at home (toddler/newborn)"

_MAX_FIELD = 120          # generic per-string sanity cap from LLM output
_MAX_CANDIDATES = 6       # per-day candidates considered before the cap

# Tighter per-field caps (2026-08-09 render-quality pass): the first live
# run shipped full street addresses and seasonal trivia inside `place` —
# hard-trim so a rambling model can't bloat the plan card.
_FIELD_CAPS = {"activity": 60, "place": 48, "why": 80, "source": 32}


@dataclass
class LiveOption:
    """One discovered candidate — typed, so the render never carries a
    numeric fact that didn't pass through a field."""
    time: str = "morning"
    activity: str = ""
    place: str = ""
    why: str = ""
    source: str = ""

    def body(self) -> str:
        """The line without the time prefix — shared by render() and the
        window-stamped plan lines."""
        head = self.activity if not self.place else f"{self.activity} at {self.place}"
        bits = [head]
        if self.why:
            bits.append(f"— {self.why}")
        if self.source:
            bits.append(f"({self.source})")
        return " ".join(bits)

    def render(self) -> str:
        return f"{self.time.capitalize()}: {self.body()}"

    def plan_line(self) -> str:
        """The line as a plan carries it, with its time window:
        'Afternoon (3:30–5:30): Museum at Hall — why (source)'."""
        window = SLOT_WINDOWS.get(self.time, "")
        when = f"{self.time.capitalize()} ({window})" if window else self.time.capitalize()
        return f"{when}: {self.body()}"


@dataclass
class LiveDiscovery:
    weather_sat: str = ""
    weather_sun: str = ""
    saturday: list[LiveOption] = field(default_factory=list)
    sunday: list[LiveOption] = field(default_factory=list)


# ─────────────────────────── LLM discovery ───────────────────────────
def _discovery_prompt(*, location: str, sat_iso: str, sun_iso: str,
                      energy: str, roles: list[str],
                      constraints: list[str],
                      mode: str = "family") -> str:
    role_line = ", ".join(roles) or "family"
    cons_line = "; ".join(constraints) or "none listed"
    if mode == "couple_evening":
        ask = f"""2. Up to {_MAX_CANDIDATES} real ADULTS-ONLY evening options PER DAY near
   the location — dinner spots worth booking, wine bars, live music,
   evening events. This is a couple's night out (no kids attending).
   Prefer "evening" time slots. ORDER each day's list best-fit first."""
    else:
        ask = f"""2. Up to {_MAX_CANDIDATES} real family-appropriate options PER DAY near the
   location — actual events (farmers' markets, library story times,
   festivals, park programs) and evergreen spots (trails, playgrounds),
   suited to the energy budget and constraints. Prefer free/cheap,
   stroller-friendly options when a toddler or newborn is listed.
   ORDER each day's list best-fit first — the first item is the one
   that gets planned."""
    if sat_iso == sun_iso:
        # One day (a Sunday ask: Saturday is gone). LIVE BUG 2026-10-04: the
        # prompt said "Saturday 2026-10-04 and Sunday 2026-10-04", a date that
        # is a Sunday called a Saturday; the model sometimes answered with no
        # options at all, and Genie said live search was unavailable.
        name = _weekday(sun_iso) or "Sunday"
        key = "saturday" if name == "Saturday" else "sunday"
        other = "sunday" if key == "saturday" else "saturday"
        dates = (f"Date: {name} {sun_iso}, this one day only. Put every option "
                 f'under "{key}" and leave "{other}" empty; weather for "{key}" only.')
    else:
        dates = f"Dates: Saturday {sat_iso} and Sunday {sun_iso}"
    return f"""You research REAL, current weekend options for one household. Use web search.

Location: {location}
{dates}
Household attending: {role_line} (energy budget: {energy})
Constraints: {cons_line}

Find, from live sources:
1. The weather forecast for each day — ONE short phrase, max 8 words,
   no trailing period (e.g. "mostly sunny, low 80s").
{ask}

Field rules (hard):
- "activity": the thing you do, max 6 words.
- "place": VENUE NAME ONLY — no street address, no city, no parking or
  seasonal notes.
- "why": max 8 words, one concrete reason for THIS household.
- "source": one short site/organization name, max 4 words.

Return STRICT JSON only — no prose, no markdown fences:
{{"weather": {{"saturday": "...", "sunday": "..."}},
  "options": {{"saturday": [{{"time": "morning|midday|afternoon|evening",
                             "activity": "...", "place": "...",
                             "why": "...", "source": "..."}}],
               "sunday": [ ... same shape ... ]}}}}
Only include options you actually found via search. If you cannot find
real options, return {{"weather": {{...}}, "options": {{"saturday": [],
"sunday": []}}}}."""


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _weekday(iso: str) -> str | None:
    try:
        return _WEEKDAYS[datetime.strptime(str(iso), "%Y-%m-%d").weekday()]
    except (TypeError, ValueError):
        return None


def _day_key(key: Any, sat_iso: str, sun_iso: str) -> str | None:
    """'saturday' / 'sunday' for a key the model used for a day: the day
    name in any case, its short form, or the ISO date; for a one-day ask
    also 'day' / 'today'."""
    k = str(key or "").strip().lower()
    one_day = sat_iso == sun_iso
    if k in ("saturday", "sat"):
        return "saturday"
    if k in ("sunday", "sun"):
        return "sunday"
    if one_day and k in ("day", "today", "options", (_weekday(sun_iso) or "").lower()):
        return "sunday"
    if k.startswith(str(sun_iso)):
        return "sunday"
    if k.startswith(str(sat_iso)):
        return "saturday"
    return None


def _parse_json_block(text: str) -> dict | None:
    """Lenient parse: strip code fences, then take the outermost {...}."""
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _clean(s: Any, cap: int = _MAX_FIELD) -> str:
    if not isinstance(s, (str, int, float)):
        return ""
    out = str(s).strip().rstrip(".")
    return out[:cap].strip()


def _coerce_option(raw: Any) -> LiveOption | None:
    if not isinstance(raw, dict):
        return None
    activity = _clean(raw.get("activity") or raw.get("title") or raw.get("name") or raw.get("event"),
                      _FIELD_CAPS["activity"])
    if not activity:
        return None
    time = _clean(raw.get("time")).lower()
    if time not in _SLOT_ORDER:
        time = "morning"
    # `place`: keep the venue name only — the first live run shipped
    # "357 E. Taylor Street, San Jose, CA 95112 (behind ...)" inside it.
    # Take the first comma segment, drop any parenthetical, and if what
    # remains still looks like a street address (leading house number),
    # it isn't a venue name at all — drop it.
    place = _clean(raw.get("place") or raw.get("venue") or raw.get("location"),
                   _FIELD_CAPS["place"]).split(",")[0]
    place = re.sub(r"\(.*?\)", "", place).strip()
    if re.match(r"^\d+\s", place):
        place = ""
    return LiveOption(time=time, activity=activity,
                      place=place,
                      why=_clean(raw.get("why"), _FIELD_CAPS["why"]),
                      source=_clean(raw.get("source"), _FIELD_CAPS["source"]))


def discover_options(*, location: str, sat_iso: str, sun_iso: str,
                     energy: str, roles: list[str],
                     constraints: list[str],
                     llm: Callable[[str], str] | None = None,
                     mode: str = "family",
                     ) -> LiveDiscovery | None:
    """Run grounded discovery. `llm` is the injectable seam: a callable
    prompt→text. Default (None) goes through core.llm.generate — the
    budget-gated spend chokepoint — with search grounding ON.
    `mode`: "family" (default) or "couple_evening" (J2 date-night).

    A grounded answer that comes back unusable (no JSON, or no options)
    is asked once more: the model varies run to run, and one more call
    (≈2¢) beats telling the family live search is down (LIVE BUG
    2026-10-04). A failed call (budget, quota, wire) is not retried.

    Returns None on ANY failure so the caller falls back to the static
    offline plan. Never raises.
    """
    prompt = _discovery_prompt(location=location, sat_iso=sat_iso,
                               sun_iso=sun_iso, energy=energy,
                               roles=roles, constraints=constraints,
                               mode=mode)
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            if llm is not None:
                text = llm(prompt) or ""
            else:
                from core import llm as _llm
                # Model pin (2026-08-09): use the SAME flash id the runner
                # validated at boot (NEW_MIYA_MODEL_FLASH) rather than the
                # core picker — the picker's auto-upgrade once selected
                # `gemini-omni-flash-preview`, which 400s on grounded calls,
                # and the whole live path silently fell back to offline.
                model = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
                usage = _llm.generate("genie", "genie.live_plan.discovery",
                                      prompt=prompt, model=model, search=True)
                if usage.error:
                    logger.warning("genie live discovery LLM error "
                                   "(falling back to offline plan): %s",
                                   usage.error)
                    return None
                text = usage.text
        except Exception as e:  # noqa: BLE001 — incl. BudgetExceeded; fallback
            logger.warning("genie live discovery failed (%s: %s) — offline "
                           "fallback", type(e).__name__, e)
            return None
        disc, why = _discovery_from_text(text, sat_iso=sat_iso, sun_iso=sun_iso)
        if disc is not None:
            return disc
        logger.warning("genie live discovery returned %s (attempt %d of %d)%s",
                       why, attempt, attempts,
                       " — asking once more" if attempt < attempts else " — offline fallback")
    return None


def _discovery_from_text(text: str, *, sat_iso: str, sun_iso: str) -> tuple[LiveDiscovery | None, str]:
    """(the discovery, "") or (None, why it is unusable). Lenient about
    the shape: day keys in any case or as ISO dates, an options list
    whose items carry a day or date, and common field-name variants."""
    obj = _parse_json_block(text)
    if obj is None:
        return None, f"unparseable output ({len(text or '')} chars)"
    by_day: dict[str, list] = {"saturday": [], "sunday": []}
    options = obj.get("options")
    if isinstance(options, dict):
        for k, v in options.items():
            day = _day_key(k, sat_iso, sun_iso)
            if day and isinstance(v, list):
                by_day[day].extend(v)
    elif isinstance(options, list):
        for item in options:
            if not isinstance(item, dict):
                continue
            day = (_day_key(item.get("day") or item.get("date"), sat_iso, sun_iso)
                   or ("sunday" if sat_iso == sun_iso else None))
            if day:
                by_day[day].append(item)
    weather_raw = obj.get("weather") if isinstance(obj.get("weather"), dict) else {}
    weather: dict[str, Any] = {}
    for k, v in weather_raw.items():
        day = _day_key(k, sat_iso, sun_iso)
        if day and day not in weather:
            weather[day] = v

    def _day(key: str) -> list[LiveOption]:
        out = []
        for r in by_day[key][:_MAX_CANDIDATES]:
            o = _coerce_option(r)
            if o is not None:
                out.append(o)
        return out

    disc = LiveDiscovery(
        weather_sat=_clean(weather.get("saturday")),
        weather_sun=_clean(weather.get("sunday")),
        saturday=_day("saturday"),
        sunday=_day("sunday"),
    )
    if not disc.saturday and not disc.sunday:
        shape = (sorted(map(str, options)) if isinstance(options, dict)
                 else f"a list of {len(options)}" if isinstance(options, list) else type(options).__name__)
        return None, f"no usable options (options: {shape})"
    return disc, ""


# ─────────────────────── deterministic sequencing ───────────────────────
def sequence_day(options: list[LiveOption], *, energy: str,
                 protect_nap: bool,
                 ) -> tuple[list[str], list[LiveOption], list[str]]:
    """Assemble one day from candidates — deterministically.

    Rules (the checker the LLM's proposal must pass through):
      * at most _ENERGY_CAP[energy] outings survive (first-listed wins —
        the LLM was told to lead with the best fit);
      * one outing per time slot: a second find for a slot that already
        holds one is an alternate, so no two items share a window;
      * when a toddler/newborn Subject is in scope, the midday nap block
        is protected: midday candidates are VIOLATIONS (glass-box
        "ruled out" with the reason) and the nap line is inserted;
      * candidates beyond the cap are NOT failures — they are
        ALTERNATES, returned typed so the render can offer them as
        choices (PRD J1: surface options, the humans decide). First
        live run (2026-08-09) dumped 10 of them as identical "over the
        budget" ruled-out lines — clutter, not glass-box;
      * every item, the nap and home blocks included, is ordered
        morning → evening;
      * a low-energy day gets a closing home block so the plan reads
        complete instead of ending at the nap line. It takes a slot no
        outing holds (see `home_block`).

    Returns (plan_lines, alternates, violation_lines).
    """
    cap = _ENERGY_CAP.get(energy, 1)
    kept: list[LiveOption] = []
    alternates: list[LiveOption] = []
    violations: list[str] = []
    taken: set[str] = set()

    for o in options:
        if protect_nap and o.time == "midday":
            violations.append(f"{o.activity} — collides with the protected "
                              "midday nap window")
            continue
        if len(kept) >= cap or o.time in taken:
            alternates.append(o)
            continue
        kept.append(o)
        taken.add(o.time)

    entries = [(_SLOT_ORDER.get(o.time, 0), f"  • {o.plan_line()}") for o in kept]
    if protect_nap:
        entries.append((_SLOT_ORDER["midday"], f"  • Midday ({SLOT_WINDOWS['midday']}): "
                                               "naps protected at home (toddler/newborn)"))
    if energy == "low":
        home = home_block(taken)
        if home:
            entries.append((_SLOT_ORDER[slot_of(home) or "afternoon"], f"  • {home}"))
    entries.sort(key=lambda e: e[0])          # stable: one item per slot
    return [line for _, line in entries], alternates, violations


# The closing home block of a low-energy day. QA-2026-10-04-05: it was
# appended as "Afternoon (3:30–5:30)" whatever the outings were, so an
# afternoon outing shared its slot and an evening outing came before it.
_HOME_MARK = ": home — "


def home_block(taken: set[str]) -> str | None:
    """The home block for a day whose outings hold the `taken` slots: the
    afternoon when it is free (before any evening outing), else the
    evening; None when both hold outings. No bullet."""
    if "afternoon" not in taken:
        rest = "a rest before the evening" if "evening" in taken else "early wind-down"
        return f"Afternoon ({SLOT_WINDOWS['afternoon']}){_HOME_MARK}quiet play, {rest}"
    if "evening" not in taken:
        return f"Evening ({SLOT_WINDOWS['evening']}){_HOME_MARK}early wind-down"
    return None


def slot_of(line: str) -> str | None:
    """The slot a plan line opens with ('Afternoon (3:30–5:30): …' →
    'afternoon'), or None."""
    head = re.split(r"[\s:(]", line.strip().lstrip("•").strip(), maxsplit=1)[0].casefold()
    return head if head in _SLOT_ORDER else None


def is_home_block(line: str) -> bool:
    """The nap block or the closing home block — not an outing."""
    return "naps protected" in line or _HOME_MARK in line or "wind-down" in line


def settle_day(lines: list[str]) -> list[str]:
    """A saved day after an edit (a swap): items in slot order, and a home
    block that now shares a slot with an outing moves to a free one, or
    goes. Lines without a slot keep their place at the front, as before."""
    outings = [ln for ln in lines if not is_home_block(ln)]
    taken = {slot_of(ln) for ln in outings} - {None}
    out = []
    for ln in lines:
        if is_home_block(ln) and "naps protected" not in ln and slot_of(ln) in taken:
            moved = home_block(taken)
            if moved:
                out.append(moved)
            continue
        out.append(ln)
    return sorted(out, key=lambda ln: _SLOT_ORDER.get(slot_of(ln) or "", 0))
