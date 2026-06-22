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
        f"Try `/weekend_plan` for a plan, or "
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


def handle_weekend_plan(*, now: datetime | None = None,
                        commit: bool = True) -> str:
    """Propose a household weekend plan sized to the household energy
    budget, FOR the family Subjects on file (multi-subject hookup).

    When `commit` is True (default) the plan is persisted via
    state.commit_weekend_plan — which is charter-gated. A veto is
    surfaced to the user rather than silently dropped.
    """
    subjects = load_family_subjects()
    energy = energy_for_subjects(subjects)
    saturday = _next_saturday(now)
    roles = [s.role for s in subjects]

    plan = WeekendPlan(
        weekend_of=saturday.strftime("%Y-%m-%d"),
        saturday=list(_SATURDAY_BY_ENERGY.get(energy, _SATURDAY_BY_ENERGY["medium"])),
        sunday=list(_SUNDAY_BY_ENERGY.get(energy, _SUNDAY_BY_ENERGY["medium"])),
        subjects=roles,
        energy=energy,
        notes=(f"Sized to {energy} household energy — set by "
               f"{', '.join(s.display for s in subjects if s.is_constraint_setter) or 'the household'}."),
    )

    lines = [
        f"*Weekend plan — week of {plan.weekend_of}*",
        f"For: {family_context_line(subjects)} (energy: {energy}).",
        "",
        "*Saturday*",
    ]
    lines += [f"  • {a}" for a in plan.saturday]
    lines += ["", "*Sunday*"]
    lines += [f"  • {a}" for a in plan.sunday]
    lines += ["", f"_{plan.notes}_"]

    if commit:
        written, verdict = commit_weekend_plan(plan)
        if not written:
            lines.append("")
            lines.append(f"⚠️ Not saved — charter veto: {verdict.reason}")
        else:
            lines.append("")
            lines.append("✅ Plan saved.")
    return "\n".join(lines)


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

    # /genie [text] — args-bearing greeting.
    if low.startswith("/genie"):
        rest = norm[len("/genie"):].strip()
        return handle_genie(rest)

    # /weekend_plan — zero-arg.
    if low.startswith("/weekend_plan"):
        return handle_weekend_plan()

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
    if re.search(r"\b(weekend|saturday|sunday)\b.*\bplan\b|\bplan\b.*\bweekend\b", low):
        return handle_weekend_plan()
    if re.search(r"\b(family\s*log|log\s+(?:for\s+)?(?:the\s+)?(?:toddler|newborn|spouse))\b", low):
        return ("To log a household note, use "
                "`/family_log <role>: <note>` "
                "(roles: primary, spouse, toddler, newborn).")

    return handle_genie(msg)


def start() -> None:
    """Legacy hook. Genie does NOT own its own bot loop — it runs under
    Miya, like Fraser."""
    print("[genie.handler] start() is a no-op — Genie runs under Miya.")
