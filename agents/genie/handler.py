"""genie.handler — Genie's slash + LLM routing.

Genie is the household / weekend-planning agent (PM thesis §3). This
module owns the inbound command surface — three commands:

    /genie [text]   — greeting / catch-all. "/genie hi" → the online
                      message WITH multi-subject family context injected.
    /weekend_plan   — propose (and commit) a household weekend plan,
                      sized to the household's energy budget (driven by
                      the youngest Subjects). Commit is charter-gated.
    /family_log <subject_role>: <text>
                    — append a household observation against a Subject
                      role. Append is charter-gated.

Routing order (mirrors fraser/kobe handlers):
    1. Slash commands → deterministic handler, no LLM.
    2. Otherwise → light keyword routing into the same handlers, then a
       structural fallback (the LLM overlay lands in a later phase; the
       deterministic path produces a complete reply on its own).

Multi-subject hookup: every greeting + plan reads the family Subjects via
state.load_family_subjects() and injects a PII-free context line, so the
plan is built FOR the household's roles, not a hard-coded "family". This
is the §3 rule-#1 contract (family members are Subjects).

State writes go through core.charter.check first — implemented in
state.py's _charter_gate (commit_weekend_plan / append_family_log).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Repo root on path so this module loads under importlib ("genie").
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.genie.protocols import (  # noqa: E402
    WeekendPlan, FamilyLogEntry, FamilySubject,
    FAMILY_ROLES, MINOR_ROLES,
    energy_for_subjects, family_context_line,
)
from agents.genie.state import (  # noqa: E402
    load_family_subjects,
    commit_weekend_plan,
    append_family_log,
    household_location,
    latest_weekend_plan,
    remember_alternates,
    last_alternates,
    last_violations,
    remember_pending_options,
    pending_options,
    clear_pending_options,
    set_household_location,
    household_role_for,
    save_household_ideas,
    proposal_for_weekend,
    date_night_rotation,
)

__all__ = [
    "ONLINE_MESSAGE",
    "SLASH_COMMANDS",
    "handle_genie",
    "handle_weekend_plan",
    "handle_family_log",
    "route",
    "start",
    "_try_slash_command",
]


# The pinned online greeting. The exact substring
# "Genie online, ready to plan your weekend" is load-bearing — the
# regression test asserts it, and the multi-subject family context is
# appended after it so the greeting always proves the Subjects loaded.
ONLINE_MESSAGE = "Genie online, ready to plan your weekend"


# ─────────────────────────── /genie greeting ──────────────────────────
def handle_genie(text: str = "") -> str:
    """Greeting / catch-all. Always injects the multi-subject family
    context so the reply proves the household Subjects loaded.

    "/genie hi" → "Genie online, ready to plan your weekend" + a PII-free
    family-context line. Any other free text routes through the same
    greeting (the deterministic surface; LLM overlay is a later phase).
    """
    subjects = load_family_subjects()
    context = family_context_line(subjects)
    energy = energy_for_subjects(subjects)
    return (
        f"{ONLINE_MESSAGE}.\n"
        f"Household in scope: {context} "
        f"(energy budget: {energy}).\n"
        f"Try `/weekend_plan` (add `options` for an A/B choice, or "
        f"`just us tonight` for a date night), `/whatson` for the raw "
        f"list, `/digest` for the weekend events summary, "
        f"`swap in <name>` / `why not <name>` to iterate, "
        f"`/replan_day` when the day slips, `/family` for the "
        f"profile, or `/family_log <role>: <note>` to log."
    )


# ─────────────────────────── /weekend_plan ────────────────────────────
def _next_saturday(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    # weekday(): Mon=0 .. Sat=5. Days until the upcoming Saturday (today
    # if it's already Saturday).
    days_ahead = (5 - now.weekday()) % 7
    return (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0)


# Activity menus keyed on the household energy budget. The youngest
# Subjects set the budget (newborn → low, toddler → medium, else high),
# so the plan never over-reaches the household's Saturday-morning energy
# (PM thesis §3c).
_SATURDAY_BY_ENERGY: dict[str, list[str]] = {
    "low": ["Slow morning at home", "Short stroller walk in the neighborhood",
            "Quiet-time + naps protected"],
    "medium": ["Morning park trip before the midday nap",
               "Easy lunch out", "Backyard / indoor play in the afternoon"],
    "high": ["Morning hike or farmers' market",
             "Lunch at a new spot", "Afternoon activity outing"],
}
_SUNDAY_BY_ENERGY: dict[str, list[str]] = {
    "low": ["Rest-and-reset day", "Grocery delivery, batch-cook"],
    "medium": ["Family brunch at home", "Light errands + nap window",
               "Wind-down evening"],
    "high": ["Brunch out", "Museum / activity", "Meal-prep for the week"],
}


_VALID_ENERGY = ("low", "medium", "high")


def _live_plan_enabled() -> bool:
    """RAHAT_GENIE_LIVE_PLAN gates live discovery (default ON — the
    whole point of the 2026-08-09 upgrade; set 0 to force offline)."""
    import os
    return os.getenv("RAHAT_GENIE_LIVE_PLAN", "1") not in ("0", "false", "no")


def _hermetic() -> bool:
    import os
    return os.getenv("RAHAT_TEST_MODE") == "1"


def _childcare_block(subjects: list[FamilySubject],
                     attending_roles: list[str]) -> list[str]:
    """J2 guard: NEVER propose an outing that silently assumes childcare.
    If any minor Subject is NOT attending, the prerequisite is stated as
    an explicit checklist item — Genie checks the assumption, the humans
    resolve it.

    2026-08-10: when other ADULT Subjects are in the household and also
    not attending (visiting grandparents), name them as candidate cover
    instead of asking into the void — Genie surfaces the option, the
    humans still decide (it never books or assumes anyone)."""
    home_minors = [s.display for s in subjects
                   if s.role in MINOR_ROLES and s.role not in attending_roles]
    if not home_minors:
        return []
    who = " + ".join(home_minors)
    cover = [s.display for s in subjects
             if s.role not in MINOR_ROLES and s.role not in attending_roles]
    lines = ["", "*Before this works* (childcare guard)"]
    if cover:
        lines.append(f"  ☐ Childcare for {who} — {' and '.join(cover)} "
                     "are home this weekend; could they cover?")
    else:
        lines.append(f"  ☐ Childcare for {who} — is that sorted, or "
                     "should I flag it as open?")
    return lines


def handle_weekend_plan(*, now: datetime | None = None,
                        commit: bool = True,
                        energy_override: str | None = None,
                        audience_text: str = "",
                        want_options: bool = False,
                        llm=None) -> str:
    """Propose a household weekend plan.

    PRD-shaped inputs (J1/J2):
      * `audience_text` — free text carrying "who is this outing for?"
        (required J1 input): "just us tonight" → couple mode (adults
        only, evening discovery, CHILDCARE GUARD engages); "without the
        newborn" → subset; default → everyone on file. Attendees — not
        the whole household — drive energy, nap protection and
        discovery constraints.
      * `want_options` — J1's deliberation shape: build TWO distinct
        sequenced candidates (A: closest fit; B: change of pace from
        the remaining pool), commit NEITHER, and wait for `go with A/B`
        (the human decision stays human — core loop step 3).

    Architecture unchanged (PRD non-negotiable): the LLM only DISCOVERS
    (grounded, typed); the deterministic sequencer sizes the day
    (energy caps, nap-window protection, concrete time windows) and
    surfaces what it ruled out. Offline fallback on any live failure.
    Commits are charter-gated.
    """
    subjects = load_family_subjects()
    household_roles = [s.role for s in subjects]
    attending_roles, couple_only = parse_attendees(audience_text,
                                                   household_roles)
    attending = [s for s in subjects if s.role in attending_roles]
    evening_mode = couple_only and bool(
        _EVENING_HINT_RE.search(audience_text or ""))

    profile_energy = energy_for_subjects(attending or subjects)
    override = (energy_override or "").strip().lower() or None
    if override not in _VALID_ENERGY:
        override = None
    energy = override or profile_energy
    saturday = _next_saturday(now)
    sunday = saturday + timedelta(days=1)
    roles = [s.role for s in (attending or subjects)]

    # ─── live discovery (LLM proposes) + deterministic sequencing ───
    live_sat: list[str] | None = None
    live_sun: list[str] | None = None
    alternates: list[str] = []
    typed_alternates: list[dict] = []
    violations: list[str] = []
    weather_line = ""
    option_b: dict | None = None
    location = household_location()
    live_ok = (_live_plan_enabled() and location
               and (llm is not None or not _hermetic()))
    # Nap guard is OPT-IN (owner directive 2026-08-10: "forget toddler
    # sleep time, doesn't matter unless I say"). It engages only when
    # the ask mentions naps, or RAHAT_GENIE_NAP_GUARD=1 pins the old
    # always-on behavior.
    import os as _os
    protect_nap = ("nap" in (audience_text or "").lower()
                   or _os.getenv("RAHAT_GENIE_NAP_GUARD") == "1")
    if live_ok:
        from agents.genie import live_plan as lp
        constraints = [c for s in (attending or subjects)
                       for c in s.constraints]
        disc = lp.discover_options(
            location=location,
            sat_iso=saturday.strftime("%Y-%m-%d"),
            sun_iso=sunday.strftime("%Y-%m-%d"),
            energy=energy, roles=roles, constraints=constraints,
            llm=llm,
            mode="couple_evening" if evening_mode else "family")
        if disc is not None:
            live_sat, alt_sat, vio_sat = lp.sequence_day(
                disc.saturday, energy=energy, protect_nap=protect_nap)
            live_sun, alt_sun, vio_sun = lp.sequence_day(
                disc.sunday, energy=energy, protect_nap=protect_nap)
            violations = vio_sat + vio_sun
            # Alternates: over-cap finds are CHOICES, not failures (PRD
            # J1 — the humans decide). Compact: name (+source), day-tagged.
            for day, alts in (("Sat", alt_sat), ("Sun", alt_sun)):
                for o in alts:
                    label = o.activity + (f" ({o.source})" if o.source else "")
                    alternates.append(f"{day}: {label}")
                    typed_alternates.append({
                        "day": day, "time": o.time, "activity": o.activity,
                        "place": o.place, "why": o.why, "source": o.source})
            if disc.weather_sat or disc.weather_sun:
                weather_line = (f"Weather: Sat — {disc.weather_sat or '?'}; "
                                f"Sun — {disc.weather_sun or '?'}")
            # J1 option sets: build B from the pool A didn't use —
            # a genuinely different weekend, not a variant of A.
            if want_options and (alt_sat or alt_sun):
                b_sat, _, bv_sat = lp.sequence_day(
                    alt_sat, energy=energy, protect_nap=protect_nap)
                b_sun, _, bv_sun = lp.sequence_day(
                    alt_sun, energy=energy, protect_nap=protect_nap)
                option_b = {"saturday": b_sat, "sunday": b_sun,
                            "violations": bv_sat + bv_sun}

    live = live_sat is not None or live_sun is not None
    note_bits = [f"Sized to {energy} energy"]
    if override:
        note_bits.append(f"(your override — profile says {profile_energy})")
    elif attending and len(attending) < len(subjects):
        note_bits.append(f"(for: {', '.join(s.display for s in attending)})")
    else:
        note_bits.append(
            "— set by "
            + (", ".join(s.display for s in subjects if s.is_constraint_setter)
               or "the household"))
    if live:
        note_bits.append("· live options via grounded search")
    plan_notes = " ".join(note_bits) + "."

    def _static(day_menu: dict) -> list[str]:
        return list(day_menu.get(energy, day_menu["medium"]))

    def _day_items(live_lines: list[str] | None,
                   static_items: list[str]) -> list[str]:
        if live_lines:
            return [ln.strip("• ").strip() for ln in live_lines]
        if live_lines is not None:      # live ran, found nothing that day
            return ["Open day"]
        return static_items

    plan = WeekendPlan(
        weekend_of=saturday.strftime("%Y-%m-%d"),
        saturday=_day_items(live_sat, _static(_SATURDAY_BY_ENERGY)),
        sunday=_day_items(live_sun, _static(_SUNDAY_BY_ENERGY)),
        subjects=roles,
        energy=energy,
        notes=plan_notes,
    )

    scope = (f"For: {', '.join(s.display for s in attending)} "
             f"(energy: {energy})." if attending and
             len(attending) < len(subjects)
             else f"For: {family_context_line(subjects)} (energy: {energy}).")
    header = f"*Weekend plan — week of {plan.weekend_of}*"
    if evening_mode:
        header = f"*Date night — weekend of {plan.weekend_of}*"
    if live:
        header += f" · live options for {location}"
    lines = [header, scope]
    if weather_line:
        lines.append(weather_line)

    # ── The humans' own proposal is the spine (core loop step 5:
    # they hand back a rough idea, Genie refines — never replaces). ──
    proposal = proposal_for_weekend(plan.weekend_of)
    if proposal and not evening_mode:
        by = proposal.get("by", "")
        who = f" (from {by.capitalize()})" if by else ""
        lines += ["", f"*Your plan on file{who}* — this weekend's anchor:"]
        if proposal.get("label") and not proposal.get("items"):
            lines.append(f"  • {proposal['label']}")
        for item in proposal.get("items", [])[:6]:
            lines.append(f"  • {item}")
        if proposal.get("companions"):
            lines.append(f"  • With: {proposal['companions']}")
        lines.append("  _Below is what discovery adds around it._")
    if evening_mode:
        rotation = date_night_rotation()
        if rotation:
            lines += ["", "*Your date-night rotation* — next up:"]
            lines += [f"  {i + 1}. {idea}"
                      for i, idea in enumerate(rotation[:3])]
            if len(rotation) > 3:
                lines.append(f"  …plus {len(rotation) - 3} more on the list")

    # ── J1 option sets: two distinct candidates, human decides ──
    if want_options and option_b and live:
        lines += ["", "*Option A — closest fit*", "*Saturday*"]
        lines += live_sat or ["  • (home day)"]
        lines += ["*Sunday*"]
        lines += live_sun or ["  • (home day)"]
        lines += ["", "*Option B — change of pace*", "*Saturday*"]
        lines += option_b["saturday"] or ["  • (home day)"]
        lines += ["*Sunday*"]
        lines += option_b["sunday"] or ["  • (home day)"]
        lines += _childcare_block(subjects, attending_roles)
        lines += ["", "Reply `go with A` or `go with B` to save one."]
        plan_a = plan.to_dict()
        plan_b = dict(plan_a)
        plan_b["saturday"] = [ln.strip("• ").strip()
                              for ln in option_b["saturday"]]
        plan_b["sunday"] = [ln.strip("• ").strip()
                            for ln in option_b["sunday"]]
        try:
            remember_pending_options(plan.weekend_of, {
                "A": {"plan": plan_a, "alternates": typed_alternates,
                      "violations": violations},
                "B": {"plan": plan_b, "alternates": [],
                      "violations": option_b["violations"]},
            })
        except Exception:  # noqa: BLE001 — cache only
            pass
        return "\n".join(lines)

    def _day_render(live_lines: list[str] | None,
                    static_items: list[str]) -> list[str]:
        # A live day that found NOTHING must not fall back to the static
        # family menu (a family "Brunch out" inside a date-night card —
        # observed 2026-08-10). Live-but-empty renders as an open day.
        if live_lines:
            return live_lines
        if live_lines is not None:      # live ran, day came back empty
            return ["  • Open — nothing solid found; `/whatson` for the "
                    "full list, or leave it free"]
        return [f"  • {a}" for a in static_items]

    lines += ["", "*Saturday*"]
    lines += _day_render(live_sat, plan.saturday)
    lines += ["", "*Sunday*"]
    lines += _day_render(live_sun, plan.sunday)
    lines += _childcare_block(subjects, attending_roles)
    if alternates:
        shown = alternates[:4]
        lines += ["", "*Also good this weekend* — swap any in:"]
        lines += [f"  • {a}" for a in shown]
        extra = len(alternates) - len(shown)
        if extra > 0:
            lines.append(f"  • …plus {extra} more found")
    if violations:
        lines += ["", "*Ruled out*"]
        lines += [f"  • {r}" for r in violations]
    if not live and _live_plan_enabled() and not location and not _hermetic():
        lines += ["", "_Offline plan — set RAHAT_GENIE_LOCATION in .env "
                      "(e.g. \"San Jose, CA\") to get real local options._"]
    lines += ["", f"_{plan.notes}_"]

    if commit:
        written, verdict = commit_weekend_plan(plan)
        if not written:
            lines.append("")
            lines.append(f"⚠️ Not saved — charter veto: {verdict.reason}")
        else:
            lines.append("")
            lines.append("✅ Plan saved.")
            if live:
                # Remember choices + rule-outs so "swap in <name>" and
                # "why not <name>" answer from the ACTUAL decision.
                try:
                    remember_alternates(plan.weekend_of, typed_alternates,
                                        violations)
                except Exception:  # noqa: BLE001 — cache only, never fatal
                    pass
    return "\n".join(lines)


def handle_go_with(choice: str) -> str:
    """Commit one of the pending J1 option sets — the human decision
    (core loop step 3) landing back in Genie (step 5). Charter-gated."""
    choice = (choice or "").strip().upper()
    pending = pending_options()
    if pending is None:
        return ("No option sets waiting — say `/weekend_plan options` "
                "to get an A/B choice first.")
    weekend_of, options = pending
    picked = options.get(choice)
    if not picked:
        return f"I only have options {', '.join(sorted(options))} pending."
    p = picked.get("plan") or {}
    plan = WeekendPlan(
        weekend_of=p.get("weekend_of", weekend_of),
        saturday=list(p.get("saturday") or []),
        sunday=list(p.get("sunday") or []),
        subjects=list(p.get("subjects") or []),
        energy=p.get("energy", "medium"),
        notes=(p.get("notes", "") .rstrip(".") +
               f" · option {choice} chosen."),
    )
    written, verdict = commit_weekend_plan(plan)
    if not written:
        return f"⚠️ Not saved — charter veto: {verdict.reason}"
    try:
        remember_alternates(plan.weekend_of,
                            list(picked.get("alternates") or []),
                            list(picked.get("violations") or []))
        clear_pending_options()
    except Exception:  # noqa: BLE001
        pass
    out = [f"✅ Option {choice} saved — here's the weekend:", ""]
    out += ["*Saturday*"] + [f"  • {ln}" for ln in plan.saturday]
    out += ["*Sunday*"] + [f"  • {ln}" for ln in plan.sunday]
    out += ["", "`swap in <name>` still works if you change your mind."]
    return "\n".join(out)


def handle_why_not(query: str) -> str:
    """Glass-box drill-down (§6.4): answer "why not X" from the ACTUAL
    sequencing decision — stored rule-out reasons and alternates —
    never a post-hoc rationalization."""
    q = (query or "").strip().strip(".!?").casefold()
    if not q:
        return "Ask me like: `why not the zoo`."
    plan = latest_weekend_plan()
    if plan is None:
        return "No saved plan yet — say `/weekend_plan` first."
    # It might simply BE in the plan (observed 2026-08-10: 'why not
    # friendship garden' when it was Sunday's outing).
    for day_name, day_lines in (("Saturday", plan.saturday),
                                ("Sunday", plan.sunday)):
        for ln in day_lines:
            if q in ln.casefold():
                return (f"Good news — it's IN the plan: {day_name}, "
                        f"“{ln}”.")
    for v in last_violations(plan.weekend_of):
        if q in v.casefold():
            return f"Ruled out: {v}."
    for a in last_alternates(plan.weekend_of):
        name = str(a.get("activity", ""))
        if q in name.casefold():
            return (f"{name} wasn't ruled out — it's an alternate that "
                    f"didn't fit the {plan.energy}-energy budget. "
                    f"Say `swap in {name}` to use it.")
    return (f"“{query}” didn't come up in this weekend's discovery — "
            "it wasn't considered, so there's no ruling to explain. "
            "`/whatson` shows everything that was found.")


def handle_replan_today(*, now: datetime | None = None) -> str:
    """J4-lite: day-of re-plan. The objective flips from "maximize fun"
    to "cut losses gracefully" — drop what the clock has passed, keep
    what still fits, protect the still-binding constraints (the nap
    block never moves)."""
    from agents.genie.live_plan import SLOT_WINDOWS
    now = now or datetime.now()
    plan = latest_weekend_plan()
    if plan is None:
        return "No saved plan to replan — say `/weekend_plan` first."
    today = now.strftime("%Y-%m-%d")
    sat = plan.weekend_of
    try:
        sun = (datetime.strptime(sat, "%Y-%m-%d")
               + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        sun = ""
    if today == sat:
        day_name, day_lines = "Saturday", list(plan.saturday)
    elif today == sun:
        day_name, day_lines = "Sunday", list(plan.sunday)
    else:
        return (f"The saved plan is for the weekend of {sat} — today "
                "isn't in it. `/weekend_plan` builds the next one.")

    # Current slot from the wall clock.
    h = now.hour
    current = ("morning" if h < 12 else
               "midday" if h < 15 else
               "afternoon" if h < 18 else "evening")
    rank = {"morning": 0, "midday": 1, "afternoon": 2, "evening": 3}

    kept, cut = [], []
    for ln in day_lines:
        slot = ln.split(" ", 1)[0].split(":", 1)[0].strip().casefold()
        slot = slot if slot in rank else "morning"
        # The nap block is a still-binding constraint — never cut it
        # while it's ahead or in progress.
        if rank[slot] >= rank[current] or ("naps protected" in ln
                                           and rank[slot] >= rank[current] - 1):
            kept.append(ln)
        else:
            cut.append(ln)

    out = [f"🕐 Day-of replan — {day_name}, cutting losses gracefully:", ""]
    if kept:
        out += ["*Rest of the day*"] + [f"  • {ln}" for ln in kept]
    else:
        out += ["Nothing left on the plan for today — wind down, "
                "you've earned it."]
    if cut:
        out += ["", "*Cut* (the clock got there first)"]
        out += [f"  • {ln}" for ln in cut]
    out += ["", f"_Now ≈ {current} ({SLOT_WINDOWS.get(current, '')}). "
                "`swap in <name>` can fill a remaining slot._"]
    return "\n".join(out)


def handle_family_show() -> str:
    """J6 surface: the living household profile — subjects, constraints,
    location, plan history — with the "last reviewed" nudge."""
    import os
    from agents.genie.state import family_profile_path
    subjects = load_family_subjects(include_departed=True)
    location = household_location()
    today = datetime.now().date()
    lines = ["*Household profile*"]
    for s in subjects:
        cons = f" — {', '.join(s.constraints)}" if s.constraints else ""
        stay = ""
        if s.is_temporary:
            stay = (f" · here until {s.present_until}"
                    if s.is_present_on(today)
                    else f" · departed {s.present_until} (not in plans)")
        lines.append(f"  • {s.display} ({s.role}){stay}{cons}")
    loc_label = location or "not set — `/family set location <City, ST>`"
    lines.append(f"  • Home area: {loc_label}")
    plan = latest_weekend_plan()
    if plan:
        lines.append(f"  • Last plan: weekend of {plan.weekend_of} "
                     f"({plan.energy} energy)")
    path = family_profile_path()
    if path.exists():
        try:
            age_days = int((datetime.now().timestamp()
                            - os.path.getmtime(path)) // 86400)
            if age_days >= 42:
                lines.append(f"  ⚠ Profile last touched ~{age_days} days "
                             "ago — still right?")
        except OSError:
            pass
    else:
        lines.append("  ⚠ Using the default profile — edit "
                     "vault/family_profile.json to make it yours.")
    return "\n".join(lines)


def handle_set_location(value: str, *, by_role: str = "") -> str:
    """J6 edit: set the home area — charter-gated profile write."""
    ok, result = set_household_location(value, by_role=by_role)
    if not ok:
        return (f"Couldn't set that ({result}). "
                "Try `/family set location San Jose, CA`.")
    return (f"✅ Home area set to *{result}*. Live discovery will use it "
            "(env RAHAT_GENIE_LOCATION still wins if set).")


# ─────────────────────────── /whatson (PRD J5) ────────────────────────
def handle_whats_on(*, now: datetime | None = None, llm=None) -> str:
    """J5 — "just give me the raw list": the discovery inventory exposed
    directly. A clean, de-duplicated flat list of what's actually on
    next weekend near the household — NOT a plan (no sequencing, no
    energy cap). Scoping is stated up front so it's adjustable (PRD J5
    success criterion). Falls back with a how-to when live discovery is
    unavailable.
    """
    location = household_location()
    saturday = _next_saturday(now)
    sunday = saturday + timedelta(days=1)

    # ── Inventory first (PRD §6.3): the registry's verified events for
    # the weekend, before any live search. Works even offline.
    inventory_lines: list[str] = []
    try:
        from bridges.events.store import query_window
        rows = query_window(saturday.strftime("%Y-%m-%d"),
                            sunday.strftime("%Y-%m-%d"), limit=20)
        seen_inv: set[str] = set()
        for r in rows:
            key = r["title"].casefold()
            if key in seen_inv:
                continue
            seen_inv.add(key)
            when = r["start_ts"][5:16].replace(" ", " · ")
            line = f"  • {when} — {r['title']}"
            if r.get("venue"):
                line += f" @ {r['venue']}"
            line += f" ({r['city']})"
            inventory_lines.append(line)
    except Exception:  # noqa: BLE001 — inventory optional
        pass

    if not (_live_plan_enabled() and location
            and (llm is not None or not _hermetic())):
        if inventory_lines:
            return "\n".join(
                [f"*What's on — weekend of "
                 f"{saturday.strftime('%Y-%m-%d')}* · from your event feeds"]
                + inventory_lines
                + ["", "_Live search is offline — this is the verified "
                       "feed inventory. `/weekend_plan` for a plan._"])
        return ("I need a home area to look things up — set "
                "RAHAT_GENIE_LOCATION in .env (e.g. \"San Jose, CA\"), "
                "then ask me again. `/weekend_plan` works offline.")

    from agents.genie import live_plan as lp
    subjects = load_family_subjects()
    disc = lp.discover_options(
        location=location,
        sat_iso=saturday.strftime("%Y-%m-%d"),
        sun_iso=sunday.strftime("%Y-%m-%d"),
        energy="high",                      # raw list: don't pre-filter
        roles=[s.role for s in subjects],
        constraints=[c for s in subjects for c in s.constraints],
        llm=llm)
    if disc is None:
        return ("Couldn't reach live listings just now — try again in a "
                "bit, or `/weekend_plan` for the offline plan.")

    seen: set[str] = set()
    lines = [f"*What's on — weekend of {saturday.strftime('%Y-%m-%d')}* "
             f"· near {location}"]
    if inventory_lines:
        # Inventory FIRST (verified source feeds), search extras after.
        lines += ["", "*From your event feeds* (verified)"]
        for inv in inventory_lines[:10]:
            lines.append(inv)
            seen.add(inv.split("— ", 1)[-1].split(" @")[0]
                     .split(" (")[0].casefold())
    for day_name, opts in (("Saturday", disc.saturday),
                           ("Sunday", disc.sunday)):
        day_lines = []
        for o in opts:
            key = o.activity.casefold()
            if key in seen:
                continue                     # de-dup across days/sources
            seen.add(key)
            day_lines.append(f"  • {o.render()}")
        if day_lines:
            lines += ["", f"*{day_name}*"] + day_lines
    if len(seen) == 0:
        return ("Nothing solid found for next weekend — try again later, "
                "or `/weekend_plan` for the offline plan.")
    lines += ["", "_Scope: next weekend, family-friendly, near "
                  f"{location}. Say `/weekend_plan` for a sequenced plan._"]
    return "\n".join(lines)


# ─────────────────────────── /digest ──────────────────────────────────
def handle_digest(now: datetime | None = None) -> str:
    """On-demand weekend digest — the same message the daily 8am
    proactive send delivers (bridges.events.digest builds both).
    Honest when empty: says so, points at the refresh schedule and the
    live-search alternative instead of inventing events."""
    try:
        from bridges.events.digest import build_digest
        text = build_digest(now)
    except Exception:  # noqa: BLE001 — surface degrades, never crashes
        text = None
    if text:
        return text
    return ("Nothing verified in your event feeds for the weekend yet — "
            "the feeds refresh at 7:00, 12:30 and 18:00. `/whatson` does "
            "a live search right now, or just tell me what you feel like "
            "and I'll dig.")


# ───────────────────── swap (PRD J1 step 5: iterate) ──────────────────
def handle_swap(query: str) -> str:
    """Swap a remembered alternate into the latest saved plan —
    deterministic, charter-gated re-commit. The generate-then-iterate
    loop's step 5: the humans pick, Genie refines.

    Match: case-insensitive substring of the alternate's activity name.
    The displaced outing goes BACK into the alternates pool, so swaps
    are reversible.
    """
    query = (query or "").strip().strip(".!?")
    if not query:
        return "Tell me what to swap in, e.g. `swap in Happy Hollow`."
    plan = latest_weekend_plan()
    if plan is None:
        return "No saved plan yet — say `/weekend_plan` first."
    alts = last_alternates(plan.weekend_of)
    if not alts:
        return ("No alternates remembered for the current plan — say "
                "`/weekend_plan` to build a fresh one with options.")

    q = query.casefold()
    match = next((a for a in alts
                  if q in str(a.get("activity", "")).casefold()), None)
    if match is None:
        names = ", ".join(str(a.get("activity")) for a in alts[:6])
        return (f"Couldn't find “{query}” among the alternates. "
                f"I have: {names}.")

    from agents.genie.live_plan import LiveOption
    incoming = LiveOption(
        time=str(match.get("time") or "morning"),
        activity=str(match.get("activity") or ""),
        place=str(match.get("place") or ""),
        why=str(match.get("why") or ""),
        source=str(match.get("source") or ""))
    day_attr = "saturday" if match.get("day") == "Sat" else "sunday"
    day_lines = list(getattr(plan, day_attr))

    # Replace the first real outing line of that day (not the nap block,
    # not the wind-down). If none, insert at the front.
    def _is_home_block(s: str) -> bool:
        return "naps protected" in s or "wind-down" in s

    displaced: str | None = None
    for i, ln in enumerate(day_lines):
        if not _is_home_block(ln):
            displaced = ln
            day_lines[i] = incoming.render()
            break
    else:
        day_lines.insert(0, incoming.render())

    # Re-sort by slot so an afternoon swap-in doesn't render before the
    # nap block (each line leads with "Morning:/Midday:/Afternoon:/…").
    _slot_rank = {"morning": 0, "midday": 1, "afternoon": 2, "evening": 3}

    def _rank(s: str) -> int:
        head = s.split(":", 1)[0].strip().casefold()
        return _slot_rank.get(head, 0)

    day_lines.sort(key=_rank)
    setattr(plan, day_attr, day_lines)
    plan.notes = (plan.notes.rstrip(".") +
                  f" · swapped in: {incoming.activity}.")

    written, verdict = commit_weekend_plan(plan)
    if not written:
        return f"⚠️ Swap not saved — charter veto: {verdict.reason}"

    # Keep the pool honest: consume the used alternate, return the
    # displaced outing to the pool (reversible swaps).
    remaining = [a for a in alts if a is not match]
    if displaced:
        remaining.append({"day": match.get("day"), "time": incoming.time,
                          "activity": displaced.split(" — ")[0]
                          .split(": ", 1)[-1].split(" at ")[0],
                          "place": "", "why": "", "source": ""})
    remember_alternates(plan.weekend_of, remaining)

    day_title = "Saturday" if day_attr == "saturday" else "Sunday"
    out = [f"🔁 Swapped in *{incoming.activity}* — updated {day_title}:", ""]
    out += [f"  • {ln}" for ln in day_lines]
    out += ["", "✅ Plan saved."]
    return "\n".join(out)


# ───────────────── idea capture (PRD core loop step 4) ────────────────
def _is_freeform(msg: str) -> bool:
    """A long / multi-line non-command message is the humans handing
    back their OWN plan ('here are some thoughts…'), not a command.
    Live incident 2026-08-09: keyword intents fired on 'adults only' and
    'high' buried in a 6-weekend proposal and hijacked it into a wrong
    date-night plan. Free-form preempts keyword routing."""
    m = (msg or "").strip()
    if m.startswith("/"):
        return False
    return len(m) > 200 or m.count("\n") >= 2


def handle_capture(msg: str, *, chat_id: str | int | None = None,
                   llm=None) -> str:
    """Capture the humans' own plan proposals (weekends + date-night
    rotation). The LLM only ELICITS structure; the save is deterministic
    and charter-gated; the raw text is NEVER lost (fallback stores it
    verbatim as a note)."""
    by = (household_role_for(chat_id) if chat_id is not None else "") or ""
    today_iso = datetime.now().strftime("%Y-%m-%d")

    ideas = None
    if _live_plan_enabled():
        # Wire-vs-hermetic is ideas.elicit's concern (it refuses the
        # wire under RAHAT_TEST_MODE unless a seam is injected).
        from agents.genie import ideas as _ideas
        ideas = _ideas.elicit(msg, today_iso=today_iso, llm=llm)

    if ideas is None:
        # Honest fallback: keep their words, admit the limitation.
        ok, reason = save_household_ideas(
            {"weekends": [], "date_nights": [],
             "notes": [msg.strip()[:500]]}, by_role=by)
        if not ok:
            return f"⚠️ Couldn't save that — {reason}"
        return ("📥 Saved your notes word-for-word (I couldn't parse them "
                "into weekends just now). They're in the household ideas — "
                "I'll use them as context.")

    ok, reason = save_household_ideas(ideas, by_role=by)
    if not ok:
        return f"⚠️ Couldn't save that — charter veto: {reason}"

    who = f" (from {by.capitalize()})" if by else ""
    lines = [f"📥 Captured your plan{who} — nothing booked, just noted:"]
    for w in ideas.get("weekends", []):
        when = w.get("weekend_of") or "date TBD"
        label = w.get("label", "")
        extra = f" · with {w['companions']}" if w.get("companions") else ""
        lines.append(f"  • {when}: {label}{extra}")
        for item in w.get("items", [])[:4]:
            lines.append(f"      – {item}")
    dn = ideas.get("date_nights", [])
    if dn:
        lines.append(f"  • Friday date-night rotation: {len(dn)} ideas "
                     f"(first up: {dn[0]})")
    lines += ["",
              "These now anchor planning: `/weekend_plan` for a listed "
              "weekend starts from YOUR pick, and date nights pull from "
              "the rotation. `/whatson` still shows what else is around."]
    return "\n".join(lines)


# ─────────────────────────── /family_log ──────────────────────────────
# "/family_log toddler: loved the park, melted down by noon"
# "/family_log spouse - wants a quieter Saturday"
_FAMILY_LOG_RE = re.compile(
    r"^/family_log\s+"
    r"(primary|spouse|toddler|newborn|senior)\s*[:\-]\s*"
    r"(.+)$",
    re.I | re.DOTALL)


def handle_family_log(subject_role: str, text: str, *,
                      logged_by: str = "") -> str:
    """Append a household observation against a Subject role —
    charter-gated via state.append_family_log. `logged_by` is the
    household ROLE of whoever wrote it (both adults write now that the
    Genie bot exists) — attribution, never a name."""
    role = subject_role.strip().lower()
    if role not in FAMILY_ROLES:
        return (f"❌ Unknown role `{subject_role}`. "
                f"Pick one of: {', '.join(FAMILY_ROLES)}.")
    note = text.strip()
    if not note:
        return "❌ Nothing to log. Try `/family_log toddler: loved the park`."
    entry = FamilyLogEntry(subject_role=role, text=note,
                           logged_by=(logged_by or "").strip().lower())
    written, verdict = append_family_log(entry)
    if not written:
        return f"⚠️ Not logged — charter veto: {verdict.reason}"
    # Find the display label without leaking a name (it's role-derived).
    subjects = load_family_subjects()
    display = next((s.display for s in subjects if s.role == role), role.capitalize())
    by = f" (by {entry.logged_by.capitalize()})" if entry.logged_by else ""
    return f"✅ Logged for {display}{by}: \"{note}\""


# ─────────────────────────── Slash dispatch ───────────────────────────
SLASH_COMMANDS: dict[str, Any] = {
    "/weekend_plan": lambda: handle_weekend_plan(),
    "/genie": lambda: handle_genie(""),
}

# Live-incident 2026-08-08 (Telegram): users type the command NAME
# without the slash ("Weekend_plan" — iOS capitalizes it) and as a
# /genie subcommand ("/genie weekend_plan"). Both fell through to the
# greeting / the synth. The token regex tolerates space / underscore /
# hyphen between the words, so "weekend_plan", "weekend plan",
# "Weekend-Plan" and "weekendplan" all resolve to the plan handler.
# Intent patterns: SINGLE source of truth in agents/genie/intents.py —
# Miya's classifier imports the same objects (2026-08-10 single-brain
# refactor: two channels, one codebase; the regression test asserts
# object identity so a re-fork fails CI).
from agents.genie.intents import (  # noqa: E402
    WEEKEND_PLAN_TOKEN_RE as _WEEKEND_PLAN_TOKEN_RE,
    FAMILY_LOG_TOKEN_RE as _FAMILY_LOG_TOKEN_RE,
    WHATS_ON_RE as _WHATS_ON_RE,
    SWAP_RE as _SWAP_RE,
    WEEKEND_NL_RE as _WEEKEND_NL_RE,
    FAMILY_NL_RE as _FAMILY_NL_RE,
    WHY_NOT_RE as _WHY_NOT_RE,
    REPLAN_TODAY_RE as _REPLAN_TODAY_RE,
    GO_WITH_RE as _GO_WITH_RE,
    OPTIONS_ARG_RE as _OPTIONS_ARG_RE,
    COUPLE_ONLY_RE as _COUPLE_ONLY_RE,
    EVENING_HINT_RE as _EVENING_HINT_RE,
    parse_attendees,
)

_FAMILY_LOG_USAGE = ("To log a household note, use "
                     "`/family_log <role>: <note>` "
                     "(roles: primary, spouse, toddler, newborn).")


# Energy override in command args ("/weekend_plan high" — live ask
# 2026-08-08: "I have high household energy"). One word, validated.
_ENERGY_ARG_RE = re.compile(r"\b(high|medium|low)\b(?:\s+energy)?", re.I)


def _energy_arg(text: str) -> str | None:
    m = _ENERGY_ARG_RE.search(text or "")
    return m.group(1).lower() if m else None


def _genie_subcommand(rest: str) -> str | None:
    """Resolve `/genie <rest>` to a known subcommand, or None to fall
    back to the greeting. Deterministic, tolerant of the command-name
    spellings users actually type."""
    if not rest:
        return None
    if _WEEKEND_PLAN_TOKEN_RE.search(rest):
        return handle_weekend_plan(energy_override=_energy_arg(rest))
    if _FAMILY_LOG_TOKEN_RE.search(rest):
        return _FAMILY_LOG_USAGE
    return None


def _try_slash_command(msg: str,
                       chat_id: str | int | None = None) -> str | None:
    """If `msg` is a recognized Genie slash command, run it and return
    the response. Otherwise None so route() can fall through.

    Args-bearing commands (/genie <text>, /family_log <role>: <text>)
    are peeled off before the zero-arg table lookup. `chat_id` resolves
    the writer's household role for family-log attribution.
    """
    if not msg:
        return None
    norm = msg.strip()
    if not norm.startswith("/"):
        return None
    low = norm.lower()

    # /family_log — args-bearing; attributed to the writer's role.
    if low.startswith("/family_log"):
        m = _FAMILY_LOG_RE.match(norm)
        if m:
            by = household_role_for(chat_id) if chat_id is not None else ""
            return handle_family_log(m.group(1), m.group(2),
                                     logged_by=by or "")
        return ("❌ `/family_log` needs a role and a note, e.g. "
                "`/family_log toddler: loved the park`.")

    # /genie [text] — subcommand dispatch first ("/genie weekend_plan"
    # must return the PLAN, not the greeting — live incident 2026-08-08),
    # then the greeting catch-all.
    if low.startswith("/genie"):
        rest = norm[len("/genie"):].strip()
        sub = _genie_subcommand(rest)
        if sub is not None:
            return sub
        return handle_genie(rest)

    # /weekend_plan [high|medium|low] [options] [audience words].
    if low.startswith("/weekend_plan"):
        rest = norm[len("/weekend_plan"):].strip()
        return handle_weekend_plan(
            energy_override=_energy_arg(rest),
            audience_text=rest,
            want_options=bool(_OPTIONS_ARG_RE.search(rest)))

    # /whatson — J5 raw list.
    if _WHATS_ON_RE.match(norm):
        return handle_whats_on()

    # /swap <name> — iterate the saved plan.
    m = _SWAP_RE.match(norm)
    if m:
        return handle_swap(next(g for g in m.groups() if g))

    # /why [not] <name> — glass-box drill-down.
    m = _WHY_NOT_RE.match(norm)
    if m:
        return handle_why_not(next(g for g in m.groups() if g))

    # /replan_day — J4-lite day-of replan.
    if _REPLAN_TODAY_RE.match(norm):
        return handle_replan_today()

    # /digest — the weekend events summary, on demand (the same message
    # the daily 8am proactive send delivers).
    if low.startswith("/digest"):
        return handle_digest()

    # /family [set location <x>] — J6 profile surface.
    if low.startswith("/family") and not low.startswith("/family_log"):
        rest = norm[len("/family"):].strip()
        m = re.match(r"set\s+location\s+(.+)$", rest, re.I)
        if m:
            return handle_set_location(m.group(1))
        return handle_family_show()

    return None


# ─────────────────────────── Top-level route ──────────────────────────
def route(msg: str, *, chat_id: str | int | None = None) -> str:
    """Top-level inbound dispatcher.

    Order:
      1. Slash commands → deterministic handler.
      2. Precise NL intents (go-with, swap, why-not, replan, what's-on).
      3. Weekend-plan intents (audience + energy + options parsed from
         the message itself — PRD J1's "who is this outing for?").
      4. Default → the /genie greeting (with family context).

    `chat_id` resolves the writer's household ROLE for attribution
    (family-log `logged_by`). The deterministic surface always returns
    a non-empty reply.
    """
    if not msg or not msg.strip():
        return handle_genie("")

    slash = _try_slash_command(msg, chat_id)
    if slash is not None:
        return slash

    # Free-form preempts ALL keyword intents (live incident 2026-08-09:
    # 'adults only' + 'high' buried in a six-weekend proposal hijacked
    # it into a wrong date-night plan). A long message IS the content —
    # capture it; commands are short.
    if _is_freeform(msg):
        return handle_capture(msg, chat_id=chat_id)

    low = msg.lower()
    stripped = msg.strip()
    # go-with first: it's the pending-decision hand-back (J1 step 4).
    m = _GO_WITH_RE.match(stripped)
    if m:
        return handle_go_with(m.group(1))
    # Swap — "swap in the farmers market" could mention "weekend"/"plan".
    m = _SWAP_RE.match(stripped)
    if m:
        return handle_swap(next(g for g in m.groups() if g))
    # Glass-box drill-down ("why not the zoo").
    m = _WHY_NOT_RE.match(stripped)
    if m:
        return handle_why_not(next(g for g in m.groups() if g))
    # J4-lite ("we're running late", "venue closed").
    if _REPLAN_TODAY_RE.search(low):
        return handle_replan_today()

    # ── Concierge (2026-08-10, the model-first layer) ──────────────
    # Everything conversational goes to the reasoner: it asks what it
    # needs (who's coming, start / be-back times, preferences), then
    # builds a grounded, timed plan. Deterministic keyword routes below
    # become the FALLBACK for when the layer is unavailable (flag off,
    # hermetic without a seam, LLM down) — same shape as ADR-013's
    # dispatcher-then-reasoner arc on the Miya plane.
    from agents.genie import concierge as _concierge
    if _concierge.enabled():
        reply = _concierge.step(chat_id if chat_id is not None else "solo",
                                msg)
        if reply:
            return reply
    # J5 raw list ("what's on this weekend").
    if _WHATS_ON_RE.search(low):
        return handle_whats_on()
    # Weekend plan — bare token ("Weekend_plan", live incident
    # 2026-08-08), NL phrases, and couple-only asks ("date night
    # Saturday", "plan something just us tonight" — J2). The message
    # itself carries audience + energy + options.
    if (_WEEKEND_PLAN_TOKEN_RE.search(low) or _WEEKEND_NL_RE.search(low)
            or _COUPLE_ONLY_RE.search(low)):
        return handle_weekend_plan(
            energy_override=_energy_arg(low),
            audience_text=msg,
            want_options=bool(_OPTIONS_ARG_RE.search(low)))
    if _FAMILY_LOG_TOKEN_RE.search(low) or _FAMILY_NL_RE.search(low):
        return _FAMILY_LOG_USAGE

    return handle_genie(msg)


def start() -> None:
    """Legacy hook. Genie does NOT own its own bot loop — it runs under
    Miya, like Fraser."""
    print("[genie.handler] start() is a no-op — Genie runs under Miya.")
