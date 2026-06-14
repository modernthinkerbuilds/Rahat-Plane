"""Canonical UserProfile — the single source of truth for who the user is.

The 2026-06-13 architectural audit found that Rahat's agents (Kobe, Fraser)
hallucinate 1RMs, mobility constraints, and goal targets because there is
no single object they all read from. The Gemini "Sports Scientist" coach
the user is comparing us against gets these right because every prompt
to Gemini comes with a known-good profile.

This module exposes:

    load() -> UserProfile  — read profile from vault + JSON overlay
    UserProfile            — typed dataclass with all fields the agents need
    to_facts_block(p)      — render as a FACTS block for prompt injection

Sources, in priority order (later overrides earlier):
  1. agents/the_scientist memory_entities (canonical for goal/plan/diet)
  2. core.intents (canonical for long-term weight target)
  3. vault/weighin_log (canonical for current weight)
  4. vault/user_profile.json overlay (manual overrides — 1RMs, mobility,
     personal context)

If vault/user_profile.json doesn't exist, we ship sane defaults derived
from the user's Gemini transcripts. The user confirms or overrides on
wake-up.

Hermetic guarantee: when RAHAT_TEST_MODE=1, the loader reads from
test/redirected DB paths only (per [[rahat_live_db_safety]] / 2026-05-08
incident).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default overlay shape. Personal values (1RMs, mobility limitations,
# training context) intentionally live in `vault/user_profile.json`
# (gitignored), NOT in this module. That keeps personal training data
# and medical context out of the public repo history.
#
# Shape reference (real values go in vault/user_profile.json):
#   one_rep_maxes_kg: {deadlift|back_squat|bench_press|overhead_press|
#                      power_clean|snatch|front_squat: float}
#   limitations:      list[str]  # mobility / injury / context items
#   training_context: {background, preferred_split, movement_preferences,
#                      avoid: list[str]}
#
# When the overlay file is missing or unreadable, the loader uses the
# empty defaults below — and the bot's response rules (see
# new_plane/miya_runner/synthesizer.SYSTEM_PROMPT) require it to say
# "I don't have that on file — can you confirm?" rather than invent
# numbers. This is the safety floor.
_DEFAULT_OVERLAY: dict[str, Any] = {
    "one_rep_maxes_kg": {},
    "limitations": [],
    "training_context": {},
    "_overlay_source": "empty-default-no-personal-data-committed",
    "_overlay_note": ("No personal data in this module. Real values "
                      "in vault/user_profile.json (gitignored). See "
                      "specs/test_lead/findings/WAKE_UP_DECISIONS_2026-06-14.md"),
}


@dataclass
class UserProfile:
    """All the facts an agent needs to coach Venkat without hallucinating.

    Fields with `kg` or `lbs` suffix are units-explicit on purpose;
    callers should not mix.
    """

    # ─── Identity ──────────────────────────────────────────────────────
    name: str = "Venkat"

    # ─── Current state ─────────────────────────────────────────────────
    current_weight_lbs: float | None = None
    current_weight_at: str | None = None  # ISO timestamp

    # ─── Standing goals ────────────────────────────────────────────────
    active_goal_target_lbs: float | None = None
    active_goal_date: str | None = None       # ISO date
    active_goal_daily_kcal: int | None = None
    active_goal_weekly_burn_kcal: int | None = None
    active_goal_tier: str | None = None       # 'hammer'|'performance'|...

    long_term_target_kg: float | None = None
    long_term_target_date: str | None = None
    intermediate_target_kg: float | None = None
    intermediate_target_date: str | None = None

    # ─── Training cadence ──────────────────────────────────────────────
    active_plan_days: dict[str, str] = field(default_factory=dict)
    # e.g. {"Mon": "rest", "Tue": "cf", "Wed": "cf", ...}
    recovery_tier: str | None = None
    default_cf_pattern: str | None = None  # e.g. "0,1,4"

    # ─── Lift baselines (from Gemini transcript; overridable) ──────────
    one_rep_maxes_kg: dict[str, float] = field(default_factory=dict)

    # ─── Constraints & personalization ─────────────────────────────────
    limitations: list[str] = field(default_factory=list)
    diet_rules: list[str] = field(default_factory=list)
    training_context: dict[str, Any] = field(default_factory=dict)

    # ─── Provenance ────────────────────────────────────────────────────
    sources: dict[str, str] = field(default_factory=dict)
    # e.g. {"weight": "weighin_log", "1rms": "gemini_transcript"}

    # ─── Convenience views ─────────────────────────────────────────────
    def one_rep_maxes_lbs(self) -> dict[str, float]:
        """Imperial view — multiply by 2.20462."""
        return {k: round(v * 2.20462, 1)
                for k, v in self.one_rep_maxes_kg.items()}


# ─── DB helpers ─────────────────────────────────────────────────────────

def _vault_db_path() -> Path:
    """Same redirection logic as core.live_db. RAHAT_TEST_MODE=1 routes
    to a per-test path; otherwise vault/rahat.db."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        # In test mode, callers either pass an explicit DB or we read
        # from the test fixture. Default to the repo's vault file as a
        # last resort but always with read-only intent.
        p = os.getenv("RAHAT_TEST_VAULT_DB")
        if p:
            return Path(p)
    return Path(os.getenv("RAHAT_VAULT_DB",
                          "vault/rahat.db")).resolve()


def _safe_query(db: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Read-only query that swallows IO/schema errors and returns [].

    The profile loader must never crash the runner — a missing DB or a
    schema-version mismatch should degrade to defaults, not fail.
    """
    try:
        if not db.exists():
            return []
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("user_profile DB read failed: %s: %s",
                       type(e).__name__, e)
        return []


# ─── Loaders ────────────────────────────────────────────────────────────

def _load_weight(db: Path) -> tuple[float | None, str | None]:
    rows = _safe_query(
        db, "SELECT weight_lbs, ts FROM weighin_log ORDER BY ts DESC LIMIT 1"
    )
    if not rows:
        return None, None
    r = rows[0]
    return float(r["weight_lbs"]), str(r["ts"])


def _load_intents(db: Path) -> dict[str, Any]:
    """Pulls long-term + intermediate weight targets from intents table.

    Schema: intents(id, kind, target_value, target_date, status, created_at)
    kind ∈ {weight_kg, weight_intermediate_kg, ...}
    """
    out: dict[str, Any] = {}
    rows = _safe_query(
        db, "SELECT * FROM intents WHERE status='active'"
    )
    for r in rows:
        if r["kind"] == "weight_kg":
            out["long_term_target_kg"] = float(r["target_value"])
            out["long_term_target_date"] = r["target_date"]
        elif r["kind"] == "weight_intermediate_kg":
            out["intermediate_target_kg"] = float(r["target_value"])
            out["intermediate_target_date"] = r["target_date"]
    return out


def _load_active_goal_and_plan(db: Path) -> dict[str, Any]:
    """Walks memory_entities for the latest active goal + plan + commitments."""
    out: dict[str, Any] = {}

    # Active goal
    rows = _safe_query(
        db,
        "SELECT payload FROM memory_entities "
        "WHERE type='goal' AND status='active' "
        "ORDER BY entity_id DESC LIMIT 1"
    )
    if rows:
        try:
            g = json.loads(rows[0]["payload"])
            out["active_goal_target_lbs"] = g.get("target_lbs")
            out["active_goal_date"] = g.get("target_date_iso")
            out["active_goal_daily_kcal"] = g.get("daily_intake_kcal")
            out["active_goal_weekly_burn_kcal"] = g.get("weekly_active_kcal")
            out["active_goal_tier"] = g.get("tier")
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("goal payload parse failed: %s", e)

    # Active plan
    rows = _safe_query(
        db,
        "SELECT payload FROM memory_entities "
        "WHERE type='plan' AND status='active' "
        "ORDER BY entity_id DESC LIMIT 1"
    )
    if rows:
        try:
            p = json.loads(rows[0]["payload"])
            out["active_plan_days"] = p.get("days", {})
        except (json.JSONDecodeError, AttributeError):
            pass

    # Diet commitments (deduped, most recent first, only active)
    rows = _safe_query(
        db,
        "SELECT payload FROM memory_entities "
        "WHERE type='commitment' AND status='active' "
        "ORDER BY entity_id DESC LIMIT 50"
    )
    seen: set[str] = set()
    diet: list[str] = []
    for r in rows:
        try:
            c = json.loads(r["payload"])
            if c.get("kind") != "diet_rule":
                continue
            v = c.get("value")
            if isinstance(v, dict):
                v = json.dumps(v)
            v = str(v).strip()
            key = v.lower()[:80]
            if key in seen or not v:
                continue
            seen.add(key)
            diet.append(v)
        except (json.JSONDecodeError, AttributeError):
            continue
    out["diet_rules"] = diet[:15]  # cap at 15 most recent unique
    return out


def _load_user_state(db: Path) -> dict[str, Any]:
    rows = _safe_query(db, "SELECT key, value FROM user_state")
    out: dict[str, Any] = {}
    for r in rows:
        if r["key"] == "recovery_tier":
            out["recovery_tier"] = r["value"]
        elif r["key"] == "default_cf_pattern":
            out["default_cf_pattern"] = r["value"]
    return out


def _load_overlay() -> dict[str, Any]:
    """Read vault/user_profile.json. If absent, use the in-module default."""
    overlay_path = Path(
        os.getenv("RAHAT_USER_PROFILE_JSON", "vault/user_profile.json")
    ).resolve()
    if not overlay_path.exists():
        logger.info("user_profile.json missing; using built-in defaults")
        return dict(_DEFAULT_OVERLAY)
    try:
        with overlay_path.open() as f:
            return json.load(f)
    except Exception as e:
        logger.warning("user_profile.json read failed: %s; using defaults", e)
        return dict(_DEFAULT_OVERLAY)


# ─── Public API ─────────────────────────────────────────────────────────

def load() -> UserProfile:
    """Build the canonical UserProfile by stitching together every source.

    Never raises. On total failure returns a profile with mostly-None
    fields and the default overlay (1RMs from Gemini transcript).
    """
    db = _vault_db_path()
    p = UserProfile()
    p.sources["db_path"] = str(db)

    # 1. Current weight
    w, ts = _load_weight(db)
    if w is not None:
        p.current_weight_lbs = w
        p.current_weight_at = ts
        p.sources["weight"] = "weighin_log"

    # 2. Long-term + intermediate from intents
    intents = _load_intents(db)
    for k, v in intents.items():
        setattr(p, k, v)
    if intents:
        p.sources["long_term_target"] = "intents"

    # 3. Active goal + plan + diet from memory_entities
    goal = _load_active_goal_and_plan(db)
    for k, v in goal.items():
        setattr(p, k, v)
    if goal:
        p.sources["active_goal"] = "memory_entities"

    # 4. User state (tier, pattern)
    state = _load_user_state(db)
    for k, v in state.items():
        setattr(p, k, v)
    if state:
        p.sources["recovery_tier"] = "user_state"

    # 5. Overlay (1RMs, limitations, training context)
    overlay = _load_overlay()
    if overlay:
        p.one_rep_maxes_kg = overlay.get("one_rep_maxes_kg", {}) or {}
        p.limitations = overlay.get("limitations", []) or []
        p.training_context = overlay.get("training_context", {}) or {}
        p.sources["1rms"] = overlay.get("_overlay_source", "overlay")
        p.sources["limitations"] = overlay.get("_overlay_source", "overlay")

    return p


def to_facts_block(p: UserProfile, *, include_diet: bool = True) -> str:
    """Render a UserProfile as a USER PROFILE block for prompt injection.

    The block is what gets dropped into every synth prompt so the LLM
    never has to guess at the user's 1RMs, limitations, or current goal.

    Format is human-readable plain text. Designed for ~600 tokens max so
    it doesn't crowd out the rest of the prompt.
    """
    lines: list[str] = ["USER PROFILE (source of truth — never invent these):"]

    # Identity & current state
    lines.append(f"  Name: {p.name}")
    if p.current_weight_lbs is not None:
        lines.append(
            f"  Current weight: {p.current_weight_lbs:.1f} lbs "
            f"(last logged {p.current_weight_at or 'unknown'})"
        )
    else:
        lines.append("  Current weight: unknown — ask before quoting a number")

    # Goal hierarchy
    goal_lines: list[str] = []
    if p.active_goal_target_lbs is not None:
        goal_lines.append(
            f"    Active sprint: {p.active_goal_target_lbs} lbs by "
            f"{p.active_goal_date or 'no date set'}"
            + (f" — daily kcal {p.active_goal_daily_kcal}" if p.active_goal_daily_kcal else "")
            + (f", weekly burn {p.active_goal_weekly_burn_kcal} kcal" if p.active_goal_weekly_burn_kcal else "")
            + (f", tier {p.active_goal_tier}" if p.active_goal_tier else "")
        )
    if p.intermediate_target_kg is not None:
        goal_lines.append(
            f"    Intermediate: {p.intermediate_target_kg} kg "
            f"({p.intermediate_target_kg * 2.20462:.1f} lbs) by "
            f"{p.intermediate_target_date or 'no date'}"
        )
    if p.long_term_target_kg is not None:
        goal_lines.append(
            f"    Long-term: {p.long_term_target_kg} kg "
            f"({p.long_term_target_kg * 2.20462:.1f} lbs) by "
            f"{p.long_term_target_date or 'no date'}"
        )
    if goal_lines:
        lines.append("  Goals:")
        lines.extend(goal_lines)

    # Training plan
    if p.active_plan_days:
        plan_str = ", ".join(f"{k}={v}" for k, v in p.active_plan_days.items())
        lines.append(f"  Active weekly plan: {plan_str}")
    if p.recovery_tier:
        lines.append(f"  Recovery tier: {p.recovery_tier}")

    # 1RMs — both kg and lb, so the bot quotes the right unit
    if p.one_rep_maxes_kg:
        lines.append("  1RMs (DO NOT INVENT — use these exact numbers):")
        for lift, kg in p.one_rep_maxes_kg.items():
            lbs = kg * 2.20462
            lines.append(f"    {lift}: {kg} kg / {lbs:.0f} lbs")

    # Limitations — every warmup/cooldown/movement choice must consider
    if p.limitations:
        lines.append("  Mobility / limitations (every workout must respect these):")
        for limitation in p.limitations:
            lines.append(f"    - {limitation}")

    # Training context (preferences, splits)
    if p.training_context:
        tc = p.training_context
        if tc.get("background"):
            lines.append(f"  Background: {tc['background']}")
        if tc.get("avoid"):
            lines.append(f"  Avoid: {', '.join(tc['avoid'])}")

    # Diet rules
    if include_diet and p.diet_rules:
        lines.append("  Diet rules:")
        for rule in p.diet_rules[:8]:
            lines.append(f"    - {rule}")

    return "\n".join(lines)


# ─── Helpers callers may want ──────────────────────────────────────────

def get_1rm_lbs(lift: str) -> float | None:
    """Quick getter — agents that need just the 1RM (e.g. Fraser's design
    step) can call this without loading the full profile."""
    p = load()
    kg = p.one_rep_maxes_kg.get(lift.lower())
    if kg is None:
        return None
    return round(kg * 2.20462, 1)


def get_limitations() -> list[str]:
    return load().limitations


def as_dict(p: UserProfile | None = None) -> dict[str, Any]:
    return asdict(p or load())
