"""genie.protocols — pure dataclasses + constants for Genie.

Genie is the household / weekend-planning agent (PM thesis §3). This
module is the *only* place that defines Genie's typed shapes:

    • FamilySubject   — a ROLE-based household Subject (NO real names/PII).
    • WeekendPlan     — a proposed Saturday/Sunday household plan.
    • FamilyLogEntry  — a single household observation/log line.

Storage doctrine (multi-subject, PM thesis §3 rule #1): family members
are Subjects with a role and a stable subject_id. Nothing in the core
reads or writes outside the Subject interface — the personal artifact
instantiates Subjects as family members; an enterprise artifact would
instantiate them as customers/accounts on the SAME data model. So this
module hard-codes NO real names: only roles + opaque placeholders.

This file is import-safe everywhere — pure types and pure functions, no
DB, no LLM, no I/O. Other agents (Bourdain, Disney, Ramsay) can import
these shapes without pulling Genie's runtime dependencies.

Importing rule for downstream agents:
    from agents.genie.protocols import (
        FamilySubject, WeekendPlan, FamilyLogEntry, FAMILY_ROLES, ...,
    )

See also
--------
- agents/genie/state.py   — substrate wrappers that consume these types.
- agents/genie/handler.py — slash + routing.
- specs/agents/GENIE_AGENT_SPEC.md — full interface contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ─────────────────────────── Agent identity ───────────────────────────
AGENT = "genie"


# ─────────────────────────── Family roles ─────────────────────────────
# The ROLE vocabulary for household Subjects. These are roles, NOT names —
# the multi-subject rule (#1) says nothing in the core may hard-code a
# real person. A personal artifact maps these to family members; an
# enterprise artifact maps the SAME Subject shape to accounts/employees.
ROLE_PRIMARY = "primary"      # the account owner
ROLE_SPOUSE = "spouse"
ROLE_TODDLER = "toddler"
ROLE_NEWBORN = "newborn"

FAMILY_ROLES: tuple[str, ...] = (
    ROLE_PRIMARY,
    ROLE_SPOUSE,
    ROLE_TODDLER,
    ROLE_NEWBORN,
)

# Roles whose energy/constraints dominate weekend planning. Genie's
# thesis (§3c): "Saturday-morning energy is the household's constraint."
# The youngest Subjects (newborn, toddler) set the ceiling on ambition,
# so the planner weights them when proposing a plan.
CONSTRAINT_ROLES: tuple[str, ...] = (ROLE_NEWBORN, ROLE_TODDLER)


# ─────────────────────────── Charter write kinds ──────────────────────
# Every Genie state write passes through core.charter.review() under one
# of these kind strings (gated in state.py via _charter_gate). Listed
# here so policies in core/charter.py stay discoverable and the audit
# log (governance_log.subject) carries a stable vocabulary.
KIND_WEEKEND_PLAN_COMMIT = "genie.weekend_plan.commit"
KIND_FAMILY_LOG_APPEND = "genie.family_log.append"

ALL_CHARTER_KINDS: tuple[str, ...] = (
    KIND_WEEKEND_PLAN_COMMIT,
    KIND_FAMILY_LOG_APPEND,
)


# ─────────────────────────── Dataclasses ──────────────────────────────
@dataclass
class FamilySubject:
    """A household member as a ROLE-based Subject.

    Carries NO real name / PII — `display` is an opaque placeholder
    (e.g. "Spouse", "Toddler") derived from the role. `subject_id` is a
    stable opaque handle so other agents can cross-reference a Subject
    without learning who it is.

    `constraints` and `preferences` are free-form so Bourdain/Disney/
    Ramsay signals can fold in over time (cross-pollination, §3c).
    """

    role: str
    subject_id: str
    display: str = ""
    constraints: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.display:
            # Placeholder label from the role — never a real name.
            self.display = self.role.capitalize()

    @property
    def is_constraint_setter(self) -> bool:
        """True for the youngest Subjects whose energy caps the plan."""
        return self.role in CONSTRAINT_ROLES

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "subject_id": self.subject_id,
            "display": self.display,
            "constraints": list(self.constraints),
            "preferences": list(self.preferences),
        }


@dataclass
class WeekendPlan:
    """A proposed household weekend plan.

    `subjects` are the FamilySubject roles the plan was built for, so a
    reader can see WHICH Subjects' constraints shaped it (multi-subject
    hookup). `saturday` / `sunday` are ordered activity lines. `energy`
    is the household energy budget the plan was sized against
    ('low' | 'medium' | 'high') — driven by the constraint-setting
    Subjects (newborn/toddler).
    """

    weekend_of: str                       # ISO date of the Saturday
    saturday: list[str] = field(default_factory=list)
    sunday: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)   # roles in scope
    energy: str = "medium"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "weekend_of": self.weekend_of,
            "saturday": list(self.saturday),
            "sunday": list(self.sunday),
            "subjects": list(self.subjects),
            "energy": self.energy,
            "notes": self.notes,
        }


@dataclass
class FamilyLogEntry:
    """A single household observation logged against a Subject role.

    These are the raw signal Genie learns from (and that the engine
    cross-pollinates to Disney/Ramsay/Bourdain). `subject_role` ties the
    entry to a Subject WITHOUT naming a person. `ts` is ISO 8601.
    """

    subject_role: str
    text: str
    ts: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_role": self.subject_role,
            "text": self.text,
            "ts": self.ts,
            "tags": list(self.tags),
        }


# ─────────────────────────── Pure helpers ─────────────────────────────
def energy_for_subjects(subjects: list[FamilySubject]) -> str:
    """Derive the household energy budget from the Subjects in scope.

    The youngest Subjects cap the household's weekend ambition (§3c:
    "Saturday-morning energy is the household's constraint"):
        • a newborn in the mix  → 'low'
        • a toddler (no newborn) → 'medium'
        • otherwise              → 'high'
    Pure function — same input, same output, no I/O.
    """
    roles = {s.role for s in subjects}
    if ROLE_NEWBORN in roles:
        return "low"
    if ROLE_TODDLER in roles:
        return "medium"
    return "high"


def family_context_line(subjects: list[FamilySubject]) -> str:
    """One-line, PII-free summary of who the plan is for — injected into
    the /genie greeting and into plan prompts (multi-subject hookup)."""
    if not subjects:
        return "no family Subjects on file yet"
    parts = [s.display for s in subjects]
    return f"{len(subjects)} family Subjects: {', '.join(parts)}"
