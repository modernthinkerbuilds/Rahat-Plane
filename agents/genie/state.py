"""genie.state — Genie's stateful (file-backed) data layer.

This module owns Genie's I/O boundary. Two substrates:

    1. **Family Subjects** — read-only, from a NEW gitignored
       `vault/family_profile.json`. ROLE-based entries only (primary /
       spouse / toddler / newborn). NO real names / PII ever land in the
       repo; the JSON file lives under vault/ (gitignored) and ships with
       PII-free placeholders when absent. Mirrors the
       core.user_profile overlay pattern (vault/user_profile.json).

    2. **Family log + committed plans** — append/commit, to a gitignored
       `vault/genie_household.json`. EVERY write here passes through
       core.charter.review() first (the policy chokepoint, PM thesis §3
       rule "Charter as policy chokepoint"). The review() call always
       writes one row to governance_log — the audit trail.

Hermetic guarantee: when RAHAT_TEST_MODE=1 the file paths redirect to a
per-process sandbox under the test vault dir, so a buggy test cannot
touch the real vault (the 2026-05-08 corruption incident this guard
exists to prevent — see [[rahat_live_db_safety]]).

Importing rule:
    from agents.genie.state import (
        load_family_subjects, append_family_log, commit_weekend_plan, ...,
    )
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core import charter as _charter

from agents.genie.protocols import (
    AGENT,
    FAMILY_ROLES,
    ROLE_PRIMARY,
    ROLE_SPOUSE,
    ROLE_TODDLER,
    ROLE_NEWBORN,
    KIND_WEEKEND_PLAN_COMMIT,
    KIND_FAMILY_LOG_APPEND,
    FamilySubject,
    WeekendPlan,
    FamilyLogEntry,
)

logger = logging.getLogger(__name__)

__all__ = [
    "family_profile_path",
    "household_store_path",
    "load_family_subjects",
    "append_family_log",
    "read_family_log",
    "commit_weekend_plan",
    "latest_weekend_plan",
    "_charter_gate",
    "DEFAULT_FAMILY_PROFILE",
]


# ─────────────────────────── PII-free default profile ─────────────────
# Shipped when vault/family_profile.json is absent. ROLE-based ONLY —
# placeholders, never real names. The real overlay (with the user's
# actual household shape) lives in vault/family_profile.json (gitignored),
# exactly like vault/user_profile.json carries the user's real 1RMs.
DEFAULT_FAMILY_PROFILE: dict = {
    "subjects": [
        {"role": ROLE_PRIMARY, "subject_id": "subj_primary",
         "display": "Primary", "constraints": [], "preferences": []},
        {"role": ROLE_SPOUSE, "subject_id": "subj_spouse",
         "display": "Spouse", "constraints": [], "preferences": []},
        {"role": ROLE_TODDLER, "subject_id": "subj_toddler",
         "display": "Toddler",
         "constraints": ["short attention span", "naps midday"],
         "preferences": []},
        {"role": ROLE_NEWBORN, "subject_id": "subj_newborn",
         "display": "Newborn",
         "constraints": ["unpredictable schedule", "low household energy"],
         "preferences": []},
    ],
    "_profile_source": "pii-free-default-no-real-names-committed",
    "_profile_note": (
        "Role-based placeholders only. Real household data (if any) "
        "lives in vault/family_profile.json (gitignored). NEVER commit "
        "real names or PII to the repo."
    ),
}


# ─────────────────────────── Path resolution ──────────────────────────
def _vault_dir() -> Path:
    """The vault directory. RAHAT_TEST_MODE=1 redirects to a sandbox so
    tests never touch the real vault (2026-05-08 incident guard)."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox)
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")).resolve()


def family_profile_path() -> Path:
    """Path to the gitignored ROLE-based family profile JSON."""
    override = os.getenv("RAHAT_FAMILY_PROFILE_JSON")
    if override:
        return Path(override).resolve()
    return _vault_dir() / "family_profile.json"


def household_store_path() -> Path:
    """Path to the gitignored household store (family log + plans)."""
    override = os.getenv("RAHAT_GENIE_STORE_JSON")
    if override:
        return Path(override).resolve()
    return _vault_dir() / "genie_household.json"


# ─────────────────────────── Family Subjects (read) ───────────────────
def load_family_subjects() -> list[FamilySubject]:
    """Read ROLE-based family Subjects from vault/family_profile.json.

    Never raises. If the file is absent or unreadable, returns the
    PII-free default profile so Genie always has a household shape to
    plan against. Only known roles (FAMILY_ROLES) are accepted — a
    stray role string is skipped so the multi-subject contract holds.
    """
    path = family_profile_path()
    raw: dict
    if not path.exists():
        logger.info("family_profile.json missing; using PII-free defaults")
        raw = dict(DEFAULT_FAMILY_PROFILE)
    else:
        try:
            with path.open() as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning("family_profile.json read failed: %s; defaults", e)
            raw = dict(DEFAULT_FAMILY_PROFILE)

    subjects: list[FamilySubject] = []
    for entry in raw.get("subjects", []) or []:
        role = str(entry.get("role", "")).strip().lower()
        if role not in FAMILY_ROLES:
            # Unknown role — skip rather than admit an off-contract Subject.
            continue
        subjects.append(FamilySubject(
            role=role,
            subject_id=str(entry.get("subject_id") or f"subj_{role}"),
            display=str(entry.get("display") or ""),
            constraints=list(entry.get("constraints") or []),
            preferences=list(entry.get("preferences") or []),
        ))
    return subjects


# ─────────────────────────── Charter gate (write chokepoint) ──────────
def _charter_gate(kind: str, payload: dict, *,
                  ctx: dict | None = None,
                  requester: str = AGENT,
                  priority: int = 5,
                  trace_id: str | None = None,
                  db_path: str | None = None) -> _charter.Verdict:
    """Single point where every Genie write meets the policy plane.

    Build a WorkOrder, call charter.review(), return the verdict. The
    review() call ALWAYS writes one row to governance_log — the audit
    trail (PM thesis §3 "Charter as policy chokepoint"). Mirrors
    fraser.state._charter_gate so the convention is uniform across agents.

    `priority<=2` is the single urgent-lane axis across the Charter
    (quiet-hours bypass etc.); no parallel `_override_*` flag exists.
    """
    wo = _charter.WorkOrder(
        kind=kind, payload=dict(payload),
        requester=requester, priority=priority, trace_id=trace_id)
    return _charter.review(wo, ctx=ctx or {}, db_path=db_path)


# ─────────────────────────── Household store (read/write) ─────────────
def _read_store() -> dict:
    """Read the household store JSON, or an empty shell if absent."""
    path = household_store_path()
    if not path.exists():
        return {"family_log": [], "weekend_plans": []}
    try:
        with path.open() as f:
            data = json.load(f)
        data.setdefault("family_log", [])
        data.setdefault("weekend_plans", [])
        return data
    except Exception as e:
        logger.warning("genie_household.json read failed: %s; empty", e)
        return {"family_log": [], "weekend_plans": []}


def _write_store(data: dict) -> None:
    path = household_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2)


def append_family_log(entry: FamilyLogEntry, *,
                      ctx: dict | None = None,
                      priority: int = 5,
                      trace_id: str | None = None,
                      db_path: str | None = None) -> tuple[bool, _charter.Verdict]:
    """Append a FamilyLogEntry — CHARTER-GATED.

    Returns (written, verdict). On veto, nothing is persisted and
    written is False; the verdict carries the reason.
    """
    verdict = _charter_gate(
        KIND_FAMILY_LOG_APPEND,
        {"subject_role": entry.subject_role, "text": entry.text},
        ctx=ctx, priority=priority, trace_id=trace_id, db_path=db_path)
    if not verdict.approved:
        return False, verdict
    data = _read_store()
    data["family_log"].append(entry.to_dict())
    _write_store(data)
    return True, verdict


def read_family_log(*, subject_role: str | None = None,
                    limit: int = 50) -> list[FamilyLogEntry]:
    """Read recent family-log entries (newest first), optionally filtered
    to one Subject role. Read-only — no charter gate."""
    data = _read_store()
    rows = data.get("family_log", [])
    out: list[FamilyLogEntry] = []
    for r in reversed(rows):
        if subject_role and r.get("subject_role") != subject_role:
            continue
        out.append(FamilyLogEntry(
            subject_role=r.get("subject_role", ""),
            text=r.get("text", ""),
            ts=r.get("ts", ""),
            tags=list(r.get("tags") or []),
        ))
        if len(out) >= limit:
            break
    return out


def commit_weekend_plan(plan: WeekendPlan, *,
                        ctx: dict | None = None,
                        priority: int = 5,
                        trace_id: str | None = None,
                        db_path: str | None = None) -> tuple[bool, _charter.Verdict]:
    """Commit a WeekendPlan — CHARTER-GATED.

    Returns (written, verdict). On veto, nothing is persisted.
    """
    verdict = _charter_gate(
        KIND_WEEKEND_PLAN_COMMIT,
        {"weekend_of": plan.weekend_of, "subjects": list(plan.subjects)},
        ctx=ctx, priority=priority, trace_id=trace_id, db_path=db_path)
    if not verdict.approved:
        return False, verdict
    data = _read_store()
    data["weekend_plans"].append(plan.to_dict())
    _write_store(data)
    return True, verdict


def latest_weekend_plan() -> WeekendPlan | None:
    """Most recently committed WeekendPlan, or None. Read-only."""
    data = _read_store()
    plans = data.get("weekend_plans", [])
    if not plans:
        return None
    p = plans[-1]
    return WeekendPlan(
        weekend_of=p.get("weekend_of", ""),
        saturday=list(p.get("saturday") or []),
        sunday=list(p.get("sunday") or []),
        subjects=list(p.get("subjects") or []),
        energy=p.get("energy", "medium"),
        notes=p.get("notes", ""),
    )
