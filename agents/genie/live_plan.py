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
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Time-slot ordering for deterministic assembly. Unknown hints sort as
# "morning" so an unparseable slot can't push an item past the nap block.
_SLOTS = ("morning", "midday", "afternoon", "evening")
_SLOT_ORDER = {s: i for i, s in enumerate(_SLOTS)}

# Energy budget → max outings per day (the deterministic cap the PRD's
# solver will eventually replace; conservative on purpose).
_ENERGY_CAP = {"low": 1, "medium": 2, "high": 3}

_NAP_LINE = "Midday: naps protected at home (toddler/newborn)"

_MAX_FIELD = 120          # per-string sanity cap from LLM output
_MAX_CANDIDATES = 6       # per-day candidates considered before the cap


@dataclass
class LiveOption:
    """One discovered candidate — typed, so the render never carries a
    numeric fact that didn't pass through a field."""
    time: str = "morning"
    activity: str = ""
    place: str = ""
    why: str = ""
    source: str = ""

    def render(self) -> str:
        head = self.activity if not self.place else f"{self.activity} at {self.place}"
        bits = [f"{self.time.capitalize()}: {head}"]
        if self.why:
            bits.append(f"— {self.why}")
        if self.source:
            bits.append(f"({self.source})")
        return " ".join(bits)


@dataclass
class LiveDiscovery:
    weather_sat: str = ""
    weather_sun: str = ""
    saturday: list[LiveOption] = field(default_factory=list)
    sunday: list[LiveOption] = field(default_factory=list)


# ─────────────────────────── LLM discovery ───────────────────────────
def _discovery_prompt(*, location: str, sat_iso: str, sun_iso: str,
                      energy: str, roles: list[str],
                      constraints: list[str]) -> str:
    role_line = ", ".join(roles) or "family"
    cons_line = "; ".join(constraints) or "none listed"
    return f"""You research REAL, current weekend options for one household. Use web search.

Location: {location}
Dates: Saturday {sat_iso} and Sunday {sun_iso}
Household: {role_line} (energy budget: {energy})
Constraints: {cons_line}

Find, from live sources:
1. The weather forecast for each day (one short phrase per day).
2. Up to {_MAX_CANDIDATES} real family-appropriate options PER DAY near the
   location — actual events (farmers' markets, library story times,
   festivals, park programs) and evergreen spots (trails, playgrounds),
   suited to the energy budget and constraints. Prefer free/cheap,
   stroller-friendly options when a toddler or newborn is listed.

Return STRICT JSON only — no prose, no markdown fences:
{{"weather": {{"saturday": "...", "sunday": "..."}},
  "options": {{"saturday": [{{"time": "morning|midday|afternoon|evening",
                             "activity": "...", "place": "...",
                             "why": "one short reason it fits this household",
                             "source": "site or org name"}}],
               "sunday": [ ... same shape ... ]}}}}
Only include options you actually found via search. If you cannot find
real options, return {{"weather": {{...}}, "options": {{"saturday": [],
"sunday": []}}}}."""


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


def _clean(s: Any) -> str:
    return str(s).strip()[:_MAX_FIELD] if isinstance(s, (str, int, float)) else ""


def _coerce_option(raw: Any) -> LiveOption | None:
    if not isinstance(raw, dict):
        return None
    activity = _clean(raw.get("activity"))
    if not activity:
        return None
    time = _clean(raw.get("time")).lower()
    if time not in _SLOT_ORDER:
        time = "morning"
    return LiveOption(time=time, activity=activity,
                      place=_clean(raw.get("place")),
                      why=_clean(raw.get("why")),
                      source=_clean(raw.get("source")))


def discover_options(*, location: str, sat_iso: str, sun_iso: str,
                     energy: str, roles: list[str],
                     constraints: list[str],
                     llm: Callable[[str], str] | None = None,
                     ) -> LiveDiscovery | None:
    """Run grounded discovery. `llm` is the injectable seam: a callable
    prompt→text. Default (None) goes through core.llm.generate — the
    budget-gated spend chokepoint — with search grounding ON.

    Returns None on ANY failure so the caller falls back to the static
    offline plan. Never raises.
    """
    prompt = _discovery_prompt(location=location, sat_iso=sat_iso,
                               sun_iso=sun_iso, energy=energy,
                               roles=roles, constraints=constraints)
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

    obj = _parse_json_block(text)
    if obj is None:
        logger.warning("genie live discovery returned unparseable output "
                       "(%d chars) — offline fallback", len(text or ""))
        return None
    weather = obj.get("weather") if isinstance(obj.get("weather"), dict) else {}
    options = obj.get("options") if isinstance(obj.get("options"), dict) else {}

    def _day(key: str) -> list[LiveOption]:
        raw = options.get(key)
        items = raw if isinstance(raw, list) else []
        out = []
        for r in items[:_MAX_CANDIDATES]:
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
        return None    # discovery found nothing usable — fall back
    return disc


# ─────────────────────── deterministic sequencing ───────────────────────
def sequence_day(options: list[LiveOption], *, energy: str,
                 protect_nap: bool) -> tuple[list[str], list[str]]:
    """Assemble one day from candidates — deterministically.

    Rules (the checker the LLM's proposal must pass through):
      * at most _ENERGY_CAP[energy] outings survive (first-listed wins —
        the LLM was told to lead with the best fit);
      * when a toddler/newborn Subject is in scope, the midday nap block
        is protected: midday candidates are ruled out and the nap line
        is inserted between morning and afternoon;
      * surviving items are ordered morning → evening;
      * everything dropped is RETURNED as ruled-out lines (glass-box),
        never silently discarded.

    Returns (plan_lines, ruled_out_lines).
    """
    cap = _ENERGY_CAP.get(energy, 1)
    kept: list[LiveOption] = []
    ruled_out: list[str] = []

    for o in options:
        if protect_nap and o.time == "midday":
            ruled_out.append(f"{o.activity} — collides with the protected "
                             "midday nap window")
            continue
        if len(kept) >= cap:
            ruled_out.append(f"{o.activity} — over the {energy}-energy "
                             "budget for one day")
            continue
        kept.append(o)

    kept.sort(key=lambda o: _SLOT_ORDER.get(o.time, 0))
    lines = [f"  • {o.render()}" for o in kept]

    if protect_nap:
        # Insert the nap line after the last morning item (or first).
        idx = sum(1 for o in kept if _SLOT_ORDER.get(o.time, 0) == 0)
        lines.insert(idx, f"  • {_NAP_LINE}")

    return lines, ruled_out
