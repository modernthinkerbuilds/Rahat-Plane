"""core.athlete_profile — structured athlete data for Fraser's adaptations.

ADR-010 (2026-05-19, Option C follow-up): Fraser cannot personalize sessions
without structured data about the athlete. The Gemini chat (see specs/
FRASER_GEMINI_CHAT_REFERENCE.md) shows that every quality session output
required:

  - 1RM benchmarks (deadlift, squat, bench, clean, snatch, etc.)
  - Equipment available (and NOT available — rower yes, jump rope no, etc.)
  - Mobility constraints (posture, hamstring/ankle mobility, neck/trap
    tension) — personal specifics live in the gitignored vault profile
  - A standing health-caution flag — specifics in the vault profile
  - Movement blacklist (snatch in strength, handstand, muscle-up, etc.)

This module is the canonical store for STABLE athlete attributes — things
that change weekly or monthly, not daily.

WHAT THIS MODULE OWNS:
  - 1RMs and working maxes
  - Height, anthropometrics
  - Standing health flags (BP, mobility issues)
  - Equipment inventory
  - Movement blacklist

WHAT THIS MODULE DOES NOT OWN:
  - HRV, sleep hours, soreness  → Huberman owns these (see core/huberman_bridge.py)
  - Today's pain / niggles      → Pain reporting module (see core/pain_state.py)
  - Today's calorie burn         → Kobe owns daily vitals
  - Workout plan / cadence       → Kobe owns scheduling

USAGE:
    from core import athlete_profile
    profile = athlete_profile.get()
    print(profile.one_rms['back_squat'])    # 100 kg
    print(profile.bp_status)                 # health-caution flag (real value in vault)
    print(profile.has_equipment('rower'))    # True
    print(profile.has_equipment('jump_rope')) # False (depends on current setup)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


# ─────────────────────────── Data shapes ───────────────────────────


@dataclass(frozen=True)
class OneRM:
    """A single 1RM. weight in kg, optional notes."""
    lift: str          # canonical name: 'deadlift', 'back_squat', 'bench_press', ...
    weight_kg: float
    notes: str = ""


@dataclass(frozen=True)
class MobilityConstraint:
    """A standing mobility/posture issue Fraser must address in every session."""
    name: str               # 'the_hunch', 'tight_hamstrings', 'ankle_stiffness'
    description: str        # Human-readable
    coaching_rule: str      # The fix Fraser must apply
    # Example:
    #   name='the_hunch', description='Forward head posture and protracted shoulders',
    #   coaching_rule='Always cue "Chest Up" and "Shoulders Back"'


@dataclass(frozen=True)
class HealthFlag:
    """A standing health constraint that affects programming."""
    name: str
    description: str
    coaching_rule: str


@dataclass(frozen=True)
class Substitution:
    """Movement A becomes movement B when A isn't available or blacklisted."""
    target: str            # canonical movement name being substituted
    replacement: str       # canonical replacement
    reason: str            # 'no_equipment', 'blacklist', 'pain', 'health'
    notes: str = ""


@dataclass
class AthleteProfile:
    """All stable athlete attributes Fraser reads to personalize sessions.

    DO NOT add fields here for daily state (HRV, sleep, pain). Those live
    in core/huberman_bridge.py and core/pain_state.py.
    """
    name: str = "Alex Rivera"
    height_cm: int = 185           # 6'1"
    one_rms: dict[str, float] = field(default_factory=dict)  # canonical_name → kg
    health_flags: list[HealthFlag] = field(default_factory=list)
    mobility_constraints: list[MobilityConstraint] = field(default_factory=list)
    equipment_available: set[str] = field(default_factory=set)
    equipment_unavailable: set[str] = field(default_factory=set)
    movement_blacklist: set[str] = field(default_factory=set)
    substitutions: list[Substitution] = field(default_factory=list)
    notes: str = ""

    def has_equipment(self, item: str) -> bool:
        """Did the athlete confirm this equipment is available?"""
        return item.lower() in {e.lower() for e in self.equipment_available}

    def lacks_equipment(self, item: str) -> bool:
        """Did the athlete confirm this equipment is NOT available?"""
        return item.lower() in {e.lower() for e in self.equipment_unavailable}

    def is_blacklisted(self, movement: str) -> bool:
        """Is this movement on the don't-do list?"""
        return movement.lower() in {m.lower() for m in self.movement_blacklist}

    def get_1rm(self, lift: str) -> float | None:
        """Return the 1RM for a lift in kg, or None if not recorded."""
        return self.one_rms.get(lift.lower())

    def working_weight(self, lift: str, percentage: float) -> float | None:
        """Compute a working weight from a percentage. Returns kg, None if no 1RM."""
        rm = self.get_1rm(lift)
        if rm is None:
            return None
        weight = rm * (percentage / 100.0)
        # Round to nearest 2.5 kg (smallest plate pair).
        return round(weight / 2.5) * 2.5

    def substitution_for(self, movement: str) -> str | None:
        """Find a replacement for a movement, or None if none registered."""
        movement_lower = movement.lower()
        for sub in self.substitutions:
            if sub.target.lower() == movement_lower:
                return sub.replacement
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["equipment_available"] = sorted(self.equipment_available)
        d["equipment_unavailable"] = sorted(self.equipment_unavailable)
        d["movement_blacklist"] = sorted(self.movement_blacklist)
        return d


# ─────────────────────────── Canonical profile ──────────────────────
# Built from the Gemini chat. This is the seed; future edits go through
# the /profile slash command (Day-11+).

_DEFAULT = AthleteProfile(
    name="Alex Rivera",
    height_cm=185,
    one_rms={
        "deadlift": 200.0,
        "back_squat": 150.0,
        "squat_clean": 70.0,
        "power_clean": 60.0,
        "bench_press": 60.0,
        "push_press": 60.0,
        "strict_press": 47.5,
        "snatch": 42.0,
    },
    health_flags=[
        HealthFlag(
            name="cardio_caution",
            description=(
                "Standing cardiovascular-caution flag "
                "(specifics in the private vault profile)."
            ),
            coaching_rule=(
                "CRITICAL: use caution with high-intensity breath-holding "
                "(Valsalva). Prioritize steady, rhythmic breathing. Exhale "
                "forcefully on the concentric (up) portion of every lift. "
                "Never hold breath through a full rep."
            ),
        ),
    ],
    mobility_constraints=[
        MobilityConstraint(
            name="the_hunch",
            description=(
                "Forward head posture and protracted shoulders."
            ),
            coaching_rule='Always cue "Chest Up" and "Shoulders Back".',
        ),
        MobilityConstraint(
            name="lower_body_stiffness",
            description=(
                "Extremely tight hamstrings and poor ankle/hip mobility."
            ),
            coaching_rule=(
                "Always recommend a heel lift (lifting shoes or 2.5 lb "
                "plates under heels) for all squatting movements."
            ),
        ),
        MobilityConstraint(
            name="neck_traps",
            description=(
                "Tension accumulates in neck/traps during runs and "
                "high-rep pulling."
            ),
            coaching_rule=(
                "Mandatory trap/neck release and CNS down-regulation in "
                "every cool-down. Avoid heavy shrugging and overhead "
                "loading when neck is flared."
            ),
        ),
        MobilityConstraint(
            name="upper_pressing_deficit",
            description=(
                "Bench press and push-ups significantly weaker than "
                "lower-body / pulling movements. Push-up plateau ~6-7 reps."
            ),
            coaching_rule=(
                "Program progressive chest/triceps hypertrophy. Use tempo "
                "work and incline variations to break the rep plateau."
            ),
        ),
    ],
    # Equipment confirmed from the Gemini chat. Edit via /equipment commands.
    equipment_available={
        "barbell", "plates", "dumbbells", "kettlebells",
        "rower", "air_bike", "treadmill", "plyo_box",
        "bench", "rack",
    },
    equipment_unavailable={
        "wall_balls", "med_ball",        # no medicine ball
        "pull_up_rig", "pull_up_bar",    # no gymnastics rig at home
        "jump_rope",                     # confirmed multiple times
    },
    # Movement blacklist — derived from Gemini chat sessions.
    # User can add via /dislike, remove via /tolerate.
    movement_blacklist={
        "muscle_up",
        "handstand",
        "overhead_squat",
        "snatch_in_strength",  # heavy snatches; technique only OK
    },
    substitutions=[
        Substitution(
            target="wall_ball",
            replacement="db_thruster",
            reason="no_equipment",
            notes="Use 15 kg / 35 lb DBs per hand. Heel lifts mandatory.",
        ),
        Substitution(
            target="wall_ball",
            replacement="goblet_thruster",
            reason="no_equipment",
        ),
        Substitution(
            target="pull_up",
            replacement="heavy_db_row",
            reason="no_equipment",
            notes="22.5 kg / 50 lb DB. Pull to hip, not chest, to spare traps.",
        ),
        Substitution(
            target="pull_up",
            replacement="barbell_row",
            reason="no_equipment",
        ),
        Substitution(
            target="pull_up",
            replacement="ring_row",
            reason="no_equipment",
            notes="If rings are available.",
        ),
        Substitution(
            target="double_under",
            replacement="lateral_line_hop",
            reason="no_equipment",
        ),
        Substitution(
            target="double_under",
            replacement="penguin_jump",
            reason="no_equipment",
            notes="1:1 ratio. Jump high, tap thighs twice mid-air.",
        ),
        Substitution(
            target="double_under",
            replacement="burpee",
            reason="no_equipment",
            notes="High-cadence to keep intensity equivalent.",
        ),
        Substitution(
            target="double_under",
            replacement="high_cadence_bike",
            reason="no_equipment",
        ),
    ],
    notes=(
        "Athlete uses sodium-free electrolyte mix for hydration. "
        "History of low HRV and CNS fatigue. "
        "Push-up plateau is a long-term progression target."
    ),
)


# ─────────────────────────── Public API ───────────────────────────


_cached_profile: AthleteProfile | None = None

# Substrate namespace for persisted 1RM edits made via `/profile set`.
# One row per (lift, value); newest active row per lift wins on read.
_PROFILE_AGENT = "fraser"
_ONE_RM_TYPE = "profile_1rm"


def get(refresh: bool = False) -> AthleteProfile:
    """Return the athlete profile. Cached for the process lifetime; pass
    refresh=True to re-read persisted `/profile set` edits from substrate.

    The returned profile is `_DEFAULT` with any persisted 1RM overrides
    merged on top, so a user's `/profile set deadlift 160` survives
    process restarts and is reflected in every weight Fraser computes."""
    global _cached_profile
    if _cached_profile is None or refresh:
        _cached_profile = _build_profile()
    return _cached_profile


def _build_profile(db_path: str | None = None) -> AthleteProfile:
    """Compose the live profile = deep copy of _DEFAULT + persisted 1RM
    overrides. Deep-copies so we never mutate the module-level seed."""
    import copy
    p = copy.deepcopy(_DEFAULT)
    overrides = _load_1rm_overrides(db_path=db_path)
    if overrides:
        p.one_rms.update(overrides)
    return p


def _load_1rm_overrides(db_path: str | None = None) -> dict[str, float]:
    """Read persisted 1RM edits from substrate. Newest active row per
    lift wins. Soft-fails to {} if substrate is unavailable."""
    out: dict[str, float] = {}
    try:
        from core import memory as _mem
        rows = _mem.list_entities(
            agent=_PROFILE_AGENT, type=_ONE_RM_TYPE, status="active",
            include_expired=False, limit=200, db_path=db_path)
    except Exception:
        return out
    # list_entities returns newest-first; first value seen per lift wins.
    for row in rows:
        payload = row.get("payload") or {}
        lift = (payload.get("lift") or "").strip().lower()
        kg = payload.get("weight_kg")
        if lift and kg is not None and lift not in out:
            try:
                out[lift] = float(kg)
            except (TypeError, ValueError):
                continue
    return out


# Common ways users name lifts → canonical 1RM keys, so "/profile set
# back squat 120" / "backsquat" / "bench" all land on the key the
# composer actually reads.
_LIFT_ALIASES = {
    "squat": "back_squat", "backsquat": "back_squat", "bs": "back_squat",
    "frontsquat": "front_squat",
    "bench": "bench_press", "benchpress": "bench_press",
    "dl": "deadlift", "dead_lift": "deadlift",
    "powerclean": "power_clean", "clean": "power_clean",
    "squatclean": "squat_clean",
    "pushpress": "push_press",
    "strictpress": "strict_press", "press": "strict_press",
    "ohp": "strict_press", "overhead_press": "strict_press",
    "shoulder_press": "strict_press",
}


def _canonical_lift(name: str) -> str:
    """Normalize a user-typed lift name to a canonical 1RM key.
    'Back Squat' / 'back-squat' / 'backsquat' → 'back_squat'; 'bench' →
    'bench_press'. Unknown lifts pass through cleaned (custom lifts OK)."""
    key = "_".join((name or "").strip().lower().replace("-", " ").split())
    return _LIFT_ALIASES.get(key, key)


def set_one_rm(lift: str, weight_kg: float,
               db_path: str | None = None) -> str:
    """Persist a 1RM override (from `/profile set`). Returns the CANONICAL
    lift name it was stored under.

    Canonicalizes the lift name, validates the weight, writes to
    substrate, and resets the process cache so the next get() reflects
    the change. Raises ValueError on bad input."""
    lift = _canonical_lift(lift)
    if not lift:
        raise ValueError("lift name is required")
    try:
        weight_kg = float(weight_kg)
    except (TypeError, ValueError):
        raise ValueError(f"weight must be a number, got {weight_kg!r}")
    if not (0 < weight_kg <= 1000):
        raise ValueError("weight must be between 0 and 1000 kg")

    from datetime import datetime, timedelta, timezone
    from core import memory as _mem
    # No natural expiry for a 1RM; park it far in the future so the
    # active-window filter never drops it.
    far_future = datetime.now(timezone.utc) + timedelta(days=3650)
    _mem.put_entity(
        agent=_PROFILE_AGENT, type=_ONE_RM_TYPE,
        payload={"lift": lift, "weight_kg": weight_kg},
        valid_until=far_future,
        rationale=f"user set {lift} 1RM = {weight_kg:g}kg via /profile set",
        # Multiple lifts coexist; don't supersede other lifts' overrides.
        supersede_existing=False,
        db_path=db_path)
    reset()
    return lift


def reset() -> None:
    """Test helper / cache invalidator. Reset the cached profile so the
    next get() rebuilds from _DEFAULT + persisted overrides."""
    global _cached_profile
    _cached_profile = None


def to_system_prompt_block() -> str:
    """Render the profile as a prompt block for Fraser's reasoner.

    Fraser includes this in every workout-design prompt so the LLM has
    structured context about who it's programming for. The Gemini chat
    is the template — see specs/FRASER_GEMINI_CHAT_REFERENCE.md.
    """
    p = get()
    lines = [
        "═══ ATHLETE PROFILE ═══",
        f"Name: {p.name}    Height: {p.height_cm} cm ({_cm_to_imperial(p.height_cm)})",
        "",
        "1RMs (kg):",
    ]
    for lift, kg in sorted(p.one_rms.items()):
        lbs = round(kg * 2.2046)
        lines.append(f"  - {lift}: {kg} kg ({lbs} lbs)")

    lines.append("")
    lines.append("HEALTH FLAGS:")
    for hf in p.health_flags:
        lines.append(f"  - {hf.name}: {hf.description}")
        lines.append(f"      Rule: {hf.coaching_rule}")

    lines.append("")
    lines.append("MOBILITY CONSTRAINTS:")
    for mc in p.mobility_constraints:
        lines.append(f"  - {mc.name}: {mc.description}")
        lines.append(f"      Rule: {mc.coaching_rule}")

    lines.append("")
    lines.append("EQUIPMENT AVAILABLE: " + ", ".join(sorted(p.equipment_available)))
    lines.append("EQUIPMENT UNAVAILABLE: " + ", ".join(sorted(p.equipment_unavailable)))
    lines.append("MOVEMENT BLACKLIST: " + ", ".join(sorted(p.movement_blacklist)))

    lines.append("")
    lines.append("KEY SUBSTITUTIONS:")
    for sub in p.substitutions:
        lines.append(f"  - {sub.target} → {sub.replacement} ({sub.reason})")
        if sub.notes:
            lines.append(f"      {sub.notes}")

    if p.notes:
        lines.append("")
        lines.append(f"NOTES: {p.notes}")

    return "\n".join(lines)


def _cm_to_imperial(cm: int) -> str:
    """185 cm → '6'1\"'"""
    total_inches = cm / 2.54
    feet = int(total_inches // 12)
    inches = round(total_inches - feet * 12)
    return f"{feet}'{inches}\""


__all__ = [
    "AthleteProfile",
    "OneRM",
    "MobilityConstraint",
    "HealthFlag",
    "Substitution",
    "get",
    "set_one_rm",
    "reset",
    "to_system_prompt_block",
]
