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
    FAMILY_ROLES,
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
        f"Try `/weekend_plan` for a plan, `/whatson` for the raw list, "
        f"`swap in <name>` to adjust the plan, or "
        f"`/family_log <role>: <note>` to log a household observation."
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


def handle_weekend_plan(*, now: datetime | None = None,
                        commit: bool = True,
                        energy_override: str | None = None,
                        llm=None) -> str:
    """Propose a household weekend plan FOR the family Subjects on file.

    2026-08-09 (PRD Phase-0): when a household location is configured,
    the plan is built from LIVE discovery — the LLM's only job is
    grounded Google-Search research returned as typed candidates
    (live_plan.discover_options); a DETERMINISTIC sequencer then sizes
    the day (energy caps, nap-window protection) and surfaces what it
    ruled out (glass-box). The LLM never schedules in its head — the
    PRD's non-negotiable. On ANY live failure (no location, no key,
    budget cap, bad JSON) the static offline menus ship instead, so the
    command can never go silent.

    `energy_override` ("low"/"medium"/"high") lets the user overrule the
    profile-derived budget for one plan ("/weekend_plan high" — live ask
    2026-08-08). `llm` is the test seam passed through to discovery;
    under RAHAT_TEST_MODE the wire is never touched unless a seam is
    injected.

    When `commit` is True (default) the plan is persisted via
    state.commit_weekend_plan — which is charter-gated. A veto is
    surfaced to the user rather than silently dropped.
    """
    subjects = load_family_subjects()
    profile_energy = energy_for_subjects(subjects)
    override = (energy_override or "").strip().lower() or None
    if override not in _VALID_ENERGY:
        override = None
    energy = override or profile_energy
    saturday = _next_saturday(now)
    sunday = saturday + timedelta(days=1)
    roles = [s.role for s in subjects]

    # ─── live discovery (LLM proposes) + deterministic sequencing ───
    live_sat: list[str] | None = None
    live_sun: list[str] | None = None
    alternates: list[str] = []
    typed_alternates: list[dict] = []
    violations: list[str] = []
    weather_line = ""
    location = household_location()
    live_ok = (_live_plan_enabled() and location
               and (llm is not None or not _hermetic()))
    if live_ok:
        from agents.genie import live_plan as lp
        constraints = [c for s in subjects for c in s.constraints]
        disc = lp.discover_options(
            location=location,
            sat_iso=saturday.strftime("%Y-%m-%d"),
            sun_iso=sunday.strftime("%Y-%m-%d"),
            energy=energy, roles=roles, constraints=constraints,
            llm=llm)
        if disc is not None:
            protect_nap = any(r in ("toddler", "newborn") for r in roles)
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

    live = live_sat is not None or live_sun is not None
    note_bits = [f"Sized to {energy} household energy"]
    if override:
        note_bits.append(f"(your override — profile says {profile_energy})")
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

    plan = WeekendPlan(
        weekend_of=saturday.strftime("%Y-%m-%d"),
        saturday=([ln.strip("• ").strip() for ln in live_sat] if live_sat
                  else _static(_SATURDAY_BY_ENERGY)),
        sunday=([ln.strip("• ").strip() for ln in live_sun] if live_sun
                else _static(_SUNDAY_BY_ENERGY)),
        subjects=roles,
        energy=energy,
        notes=plan_notes,
    )

    header = f"*Weekend plan — week of {plan.weekend_of}*"
    if live:
        header += f" · live options for {location}"
    lines = [header,
             f"For: {family_context_line(subjects)} (energy: {energy})."]
    if weather_line:
        lines.append(weather_line)
    lines += ["", "*Saturday*"]
    lines += (live_sat if live_sat else [f"  • {a}" for a in plan.saturday])
    lines += ["", "*Sunday*"]
    lines += (live_sun if live_sun else [f"  • {a}" for a in plan.sunday])
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
                # Remember the choices so "swap in <name>" can iterate
                # the saved plan without a fresh discovery call.
                try:
                    remember_alternates(plan.weekend_of, typed_alternates)
                except Exception:  # noqa: BLE001 — cache only, never fatal
                    pass
    return "\n".join(lines)


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
    if not (_live_plan_enabled() and location
            and (llm is not None or not _hermetic())):
        return ("I need a home area to look things up — set "
                "RAHAT_GENIE_LOCATION in .env (e.g. \"San Jose, CA\"), "
                "then ask me again. `/weekend_plan` works offline.")

    from agents.genie import live_plan as lp
    subjects = load_family_subjects()
    saturday = _next_saturday(now)
    sunday = saturday + timedelta(days=1)
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


# ─────────────────────────── /family_log ──────────────────────────────
# "/family_log toddler: loved the park, melted down by noon"
# "/family_log spouse - wants a quieter Saturday"
_FAMILY_LOG_RE = re.compile(
    r"^/family_log\s+"
    r"(primary|spouse|toddler|newborn)\s*[:\-]\s*"
    r"(.+)$",
    re.I | re.DOTALL)


def handle_family_log(subject_role: str, text: str) -> str:
    """Append a household observation against a Subject role —
    charter-gated via state.append_family_log."""
    role = subject_role.strip().lower()
    if role not in FAMILY_ROLES:
        return (f"❌ Unknown role `{subject_role}`. "
                f"Pick one of: {', '.join(FAMILY_ROLES)}.")
    note = text.strip()
    if not note:
        return "❌ Nothing to log. Try `/family_log toddler: loved the park`."
    entry = FamilyLogEntry(subject_role=role, text=note)
    written, verdict = append_family_log(entry)
    if not written:
        return f"⚠️ Not logged — charter veto: {verdict.reason}"
    # Find the display label without leaking a name (it's role-derived).
    subjects = load_family_subjects()
    display = next((s.display for s in subjects if s.role == role), role.capitalize())
    return f"✅ Logged for {display}: \"{note}\""


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
_WEEKEND_PLAN_TOKEN_RE = re.compile(r"\bweekend[\s_-]*plan\b", re.I)
_FAMILY_LOG_TOKEN_RE = re.compile(r"\bfamily[\s_-]*log\b", re.I)

# J5 raw-list intent (2026-08-10): "/whatson", "what's on this weekend",
# "any events this week". Token-tolerant like the others.
_WHATS_ON_RE = re.compile(
    r"^\s*/\s*what[\s_-]*s?[\s_-]*on\b"
    r"|\bwhat'?s\s+on\b"
    r"|\bwhat\s+is\s+on\b"
    r"|\bevents?\b.*\b(week|weekend|today|saturday|sunday)\b"
    r"|\b(week|weekend)\b.*\bevents?\b",
    re.I,
)

# Swap intent (2026-08-10, PRD J1 step 5): "swap in Happy Hollow",
# "/swap Happy Hollow", "swap the zoo in". Deliberately requires the
# word "in" OR the slash form so Kobe's "swap Mon with Tue" plan
# mutation (which never reaches Genie anyway) can't be confused.
_SWAP_RE = re.compile(
    r"^\s*/\s*swap\s+(?:in\s+)?(.+)$"
    r"|^\s*swap\s+in\s+(.+)$"
    r"|^\s*swap\s+(.+?)\s+in\s*$",
    re.I,
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


def _try_slash_command(msg: str) -> str | None:
    """If `msg` is a recognized Genie slash command, run it and return
    the response. Otherwise None so route() can fall through.

    Args-bearing commands (/genie <text>, /family_log <role>: <text>)
    are peeled off before the zero-arg table lookup.
    """
    if not msg:
        return None
    norm = msg.strip()
    if not norm.startswith("/"):
        return None
    low = norm.lower()

    # /family_log — args-bearing.
    if low.startswith("/family_log"):
        m = _FAMILY_LOG_RE.match(norm)
        if m:
            return handle_family_log(m.group(1), m.group(2))
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

    # /weekend_plan [high|medium|low] — optional energy override.
    if low.startswith("/weekend_plan"):
        rest = norm[len("/weekend_plan"):].strip()
        return handle_weekend_plan(energy_override=_energy_arg(rest))

    # /whatson — J5 raw list.
    if _WHATS_ON_RE.match(norm):
        return handle_whats_on()

    # /swap <name> — iterate the saved plan.
    m = _SWAP_RE.match(norm)
    if m:
        return handle_swap(next(g for g in m.groups() if g))

    return None


# ─────────────────────────── Top-level route ──────────────────────────
def route(msg: str, *, chat_id: str | int | None = None) -> str:
    """Top-level inbound dispatcher.

    Order:
      1. Slash commands → deterministic handler.
      2. Keyword routing (weekend-plan / family-log intents in NL).
      3. Default → the /genie greeting (with family context).

    The deterministic surface always returns a non-empty reply; the LLM
    overlay (richer plan voice) lands in a later phase.
    """
    if not msg or not msg.strip():
        return handle_genie("")

    slash = _try_slash_command(msg)
    if slash is not None:
        return slash

    low = msg.lower()
    # Swap first — "swap in the farmers market" contains no other intent
    # words but a swap of a plan item could mention "weekend"/"plan".
    m = _SWAP_RE.match(msg.strip())
    if m:
        return handle_swap(next(g for g in m.groups() if g))
    # J5 raw list ("what's on this weekend").
    if _WHATS_ON_RE.search(low):
        return handle_whats_on()
    # Bare command-name token ("Weekend_plan" — the underscore defeats
    # \b word boundaries in the phrase patterns below; live incident
    # 2026-08-08). NL energy override honored ("high household energy
    # ... weekend plan" — live ask 2026-08-08).
    if _WEEKEND_PLAN_TOKEN_RE.search(low):
        return handle_weekend_plan(energy_override=_energy_arg(low))
    if re.search(r"\b(weekend|saturday|sunday)\b.*\bplan\b|\bplan\b.*\bweekend\b", low):
        return handle_weekend_plan(energy_override=_energy_arg(low))
    if (_FAMILY_LOG_TOKEN_RE.search(low)
            or re.search(r"\blog\s+(?:for\s+)?(?:the\s+)?(?:toddler|newborn|spouse)\b", low)):
        return _FAMILY_LOG_USAGE

    return handle_genie(msg)


def start() -> None:
    """Legacy hook. Genie does NOT own its own bot loop — it runs under
    Miya, like Fraser."""
    print("[genie.handler] start() is a no-op — Genie runs under Miya.")
