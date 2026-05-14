"""fraser.protocols — type contract, schemas, constants.

Fraser is the CrossFit programming & performance agent (per
`specs/FRASER_REQUIREMENTS.md`). This module is the *only* place that
defines:

    1. The 11 entity-type strings stored in `memory_entities`
       (`type` column) with `agent="fraser"`.
    2. The dataclass shape of each entity body (serialized to the
       `payload` JSON column).
    3. The canonical Workout Card schema (§2.5 of the spec) — the
       single artifact Fraser emits.
    4. The InputMode router enum (§2.4) and the recovery / source /
       polarity / severity enums Fraser writes use.
    5. Charter-rule kind strings (`fraser.*`) so policies live in
       `core/charter.py` and stay discoverable.

This file is import-safe — pure types and pure functions. No DB, no
LLM, no I/O. Other agents can import it without pulling Fraser's
runtime dependencies.

Storage doctrine (ADR-003)
--------------------------
Fraser is a post-ADR-003 agent. It owns ZERO dedicated tables. Every
entity below is one row in `memory_entities` with `agent="fraser"`
and `type=<one of the 11 ENTITY_* constants>`. The Workout Card lives
as JSON inside that row's `payload`. See `agents/the_scientist/
dislikes.py` for the canonical post-ADR-003 pattern this module mirrors.

See also
--------
- `agents/fraser/state.py` — substrate wrappers that consume these types.
- `agents/fraser/handler.py` — reasoner loop + input-mode router.
- `specs/FRASER_REQUIREMENTS.md` — full requirements (24kB).
"""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

# Repo root on path so this module loads cleanly under importlib
# (`fraser` short-name) AND as a package member. Idempotent. Mirrors
# the dislikes.py / scientist main.py pattern.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ─────────────────────────── Agent identity ───────────────────────────
AGENT = "fraser"


# ─────────────────────────── Entity types ─────────────────────────────
# These are the `type` values written into `memory_entities`. NEVER add
# a new one without an ADR — the test_storage_convention guardrail
# enumerates the substrate API surface, and downstream tools (the
# eval suite, /memory debug endpoints) snapshot these strings.
ENTITY_WORKOUT       = "fraser_workout"
ENTITY_MOVEMENT      = "fraser_movement"
ENTITY_INJURY        = "fraser_injury"
ENTITY_PRVN_CYCLE    = "fraser_prvn_cycle"
ENTITY_PROGRESSION   = "fraser_progression"
ENTITY_WARMUP        = "fraser_warmup"
ENTITY_COOLDOWN      = "fraser_cooldown"
ENTITY_SUBSTITUTION  = "fraser_substitution"
ENTITY_ONE_REP_MAX   = "fraser_1rm"
ENTITY_PREFERENCE    = "fraser_preference"
ENTITY_ROUTE         = "fraser_route"

ALL_ENTITY_TYPES: tuple[str, ...] = (
    ENTITY_WORKOUT,
    ENTITY_MOVEMENT,
    ENTITY_INJURY,
    ENTITY_PRVN_CYCLE,
    ENTITY_PROGRESSION,
    ENTITY_WARMUP,
    ENTITY_COOLDOWN,
    ENTITY_SUBSTITUTION,
    ENTITY_ONE_REP_MAX,
    ENTITY_PREFERENCE,
    ENTITY_ROUTE,
)


# ─────────────────────────── Charter kinds ────────────────────────────
# Each `fraser.*` write work-order kind. Policies in `core/charter.py`
# can glob-match these to gate behavior (quiet hours, HRV-red blocks,
# family-priority overrides). See spec §2.2 for the full table.
CHARTER_COMMIT_WORKOUT       = "fraser.workout.commit"
CHARTER_LOG_SESSION          = "fraser.session.log"
CHARTER_REGISTER_INJURY      = "fraser.injury.register"
CHARTER_RESOLVE_INJURY       = "fraser.injury.resolve"
CHARTER_UPDATE_1RM           = "fraser.1rm.update"
CHARTER_INGEST_1RM_BATCH     = "fraser.1rm.ingest_batch"
CHARTER_RECORD_PREFERENCE    = "fraser.preference.record"
CHARTER_RECORD_ROUTE         = "fraser.route.record"
CHARTER_PROPOSE_SUBSTITUTE   = "fraser.substitute.propose"
CHARTER_ADVANCE_PRVN         = "fraser.prvn.advance"
CHARTER_ADVANCE_PROGRESSION  = "fraser.progression.advance"

ALL_CHARTER_KINDS: tuple[str, ...] = (
    CHARTER_COMMIT_WORKOUT,
    CHARTER_LOG_SESSION,
    CHARTER_REGISTER_INJURY,
    CHARTER_RESOLVE_INJURY,
    CHARTER_UPDATE_1RM,
    CHARTER_INGEST_1RM_BATCH,
    CHARTER_RECORD_PREFERENCE,
    CHARTER_RECORD_ROUTE,
    CHARTER_PROPOSE_SUBSTITUTE,
    CHARTER_ADVANCE_PRVN,
    CHARTER_ADVANCE_PROGRESSION,
)


# ─────────────────────────── Enums ────────────────────────────────────
class InputMode(str, Enum):
    """Spec §2.4. The reasoner's first decision per turn.

    Mis-classification has high cost: classifying USER_SUPPLIED as
    DEFAULT causes Fraser to override a workout the user explicitly
    asked for. Classifying DEFAULT as USER_REQUESTED_FORMAT causes
    Fraser to honor a phantom format that wasn't actually requested.
    The router lives in `handler.py` and is regression-tested.
    """
    DEFAULT                = "default"
    USER_SUPPLIED_WORKOUT  = "user_supplied_workout"
    USER_REQUESTED_FORMAT  = "user_requested_format"


class RecoveryColor(str, Enum):
    """Huberman's three-state recovery signal. Drives Fraser's gating."""
    GREEN  = "green"
    AMBER  = "amber"
    RED    = "red"


class KobeTier(str, Enum):
    """Kobe's training-tier signal (read-only from Fraser's POV)."""
    HAMMER     = "hammer"
    ZONE2      = "zone2"
    DELOAD     = "deload"
    SURVIVAL   = "survival"


class WodFormat(str, Enum):
    """Workout-of-the-day formats Fraser composes. Spec §2.4 / §2.5."""
    FOR_TIME      = "for_time"
    AMRAP         = "amrap"
    EMOM          = "emom"
    TABATA        = "tabata"
    SMASH_FORMAT  = "smash_format"
    INTERVALS     = "intervals"
    STRENGTH_ONLY = "strength_only"


class Polarity(str, Enum):
    """fraser_preference polarity — like / dislike."""
    LIKE     = "like"
    DISLIKE  = "dislike"


class Severity(str, Enum):
    """fraser_injury severity — drives ETA defaults and mute breadth."""
    MILD      = "mild"
    MODERATE  = "moderate"
    SEVERE    = "severe"


class OneRMSource(str, Enum):
    """Where a 1RM came from. Drives the % math's trust weighting:
    tested > observed > estimated > user_provided (spec §11)."""
    TESTED         = "tested"
    OBSERVED       = "observed"
    ESTIMATED      = "estimated"
    USER_PROVIDED  = "user_provided"


class CompletionStatus(str, Enum):
    """Session completion state — written by log_session, read by Kobe
    for recalibration math (spec §10)."""
    PLANNED       = "planned"
    COMPLETED     = "completed"
    PARTIAL       = "partial"
    SKIPPED       = "skipped"


# ─────────────────────────── Canonical lifts ──────────────────────────
# The seven lifts Fraser tracks 1RMs for. Order matches spec §11 path A.
LIFTS: tuple[str, ...] = (
    "back_squat", "deadlift", "bench", "strict_press",
    "push_press", "clean", "snatch",
)

# Cross-cutting movement names — the postural-cue lookup and
# substitution rule index key off these canonical strings.
PRESSING_MOVEMENTS = frozenset({
    "bench", "strict_press", "push_press", "overhead_press",
    "dumbbell_press", "floor_press", "incline_press",
})
PULLING_MOVEMENTS = frozenset({
    "deadlift", "sumo_deadlift", "barbell_row", "pendlay_row",
    "pull_up", "chin_up", "trx_row", "ring_row", "dumbbell_row",
})
POSTERIOR_CHAIN = frozenset({
    "deadlift", "sumo_deadlift", "rdl", "good_morning",
    "back_squat", "front_squat", "kettlebell_swing", "hip_thrust",
})
OVERHEAD_MOVEMENTS = frozenset({
    "strict_press", "push_press", "overhead_press", "snatch",
    "jerk", "thruster", "wall_ball", "handstand_push_up",
})


# ─────────────────────────── Staleness thresholds ─────────────────────
# 1RM staleness drives both a soft warn and a hard block on PR-attempts
# (spec §10). User asked Day 1 whether these are aggressive — flagged
# in FRASER_OPEN_QUESTIONS.md.
ONE_RM_WARN_AFTER_DAYS  = 90
ONE_RM_BLOCK_AFTER_DAYS = 180


# ─────────────────────────── Normalizers ──────────────────────────────
def normalize_lift_name(name: str) -> str:
    """Canonicalize a lift name to one of `LIFTS` (or the input lower-
    snaked if unrecognized — keeping data flow forgiving).

    Mirrors the spirit of `dislikes._normalize_movement` but bound to
    the seven-lift vocabulary so weight math sees one string per lift.
    """
    if not name:
        return ""
    n = name.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "bs": "back_squat", "squat": "back_squat", "back_squats": "back_squat",
        "dl": "deadlift", "deadlifts": "deadlift",
        "bp": "bench", "bench_press": "bench", "bench_presses": "bench",
        "sp": "strict_press", "press": "strict_press", "ohp": "strict_press",
        "pp": "push_press",
        "snatches": "snatch",
        "cleans": "clean",
    }
    return aliases.get(n, n)


def normalize_movement(name: str) -> str:
    """Lower-snake-case a movement name. Looser than lift normalization —
    Fraser handles dozens of movement names, not a fixed seven. Used
    by the dislike filter and the substitution-rule index."""
    if not name:
        return ""
    n = name.strip().lower().replace("-", "_")
    n = re.sub(r"\s+", "_", n)
    if n.endswith("s") and len(n) > 3 and not n.endswith("ss"):
        n = n[:-1]
    return n


def is_pressing(movement: str) -> bool:
    return normalize_movement(movement) in PRESSING_MOVEMENTS


def is_pulling(movement: str) -> bool:
    return normalize_movement(movement) in PULLING_MOVEMENTS


def is_overhead(movement: str) -> bool:
    return normalize_movement(movement) in OVERHEAD_MOVEMENTS


def loads_posterior_chain(movement: str) -> bool:
    return normalize_movement(movement) in POSTERIOR_CHAIN


# ─────────────────────────── Workout Card schema ──────────────────────
# Spec §2.5. The single canonical artifact Fraser emits. Miya wraps it
# for conversational delivery but the CARD itself is what gets persisted
# to `fraser_workout`'s payload column.

@dataclass
class Movement:
    """One movement instance inside a block. Substitution metadata is
    inline so the card carries its own audit trail."""
    name: str                           # canonical lower_snake (normalize_movement)
    reps_or_time: str = ""              # "15", "30s", "5 each leg", "AMRAP-2min"
    load_kg: float | None = None        # optional — bodyweight movements use None
    percent_1rm: float | None = None    # 0..100 — only set for strength lifts
    substitution_reason: str | None = None  # "no rope → penguin jumps"


@dataclass
class StrengthLift:
    """One programmed lift in the STRENGTH block. Carries the ramp-up
    schedule and the HBP cue inline (spec §5 item 5 + §5 item 3)."""
    name: str
    working_sets: int = 1
    working_reps: int = 1
    working_weight_kg: float = 0.0
    percent_1rm: float | None = None
    ramp_up_kg: list[float] = field(default_factory=list)
    hbp_cue: str = "Exhale on the up portion. Never max Valsalva."
    notes: str = ""


@dataclass
class WarmUpBlock:
    duration_min: int = 0
    movements: list[Movement] = field(default_factory=list)
    postural_cues: list[str] = field(default_factory=list)


@dataclass
class StrengthBlock:
    duration_min: int = 0
    lifts: list[StrengthLift] = field(default_factory=list)


@dataclass
class WODBlock:
    format: WodFormat = WodFormat.FOR_TIME
    cap_min: int = 0
    movements: list[Movement] = field(default_factory=list)
    rounds_or_structure: str = ""       # "5 RFT", "21-15-9", "EMOM 18"
    substitutions_applied: list[str] = field(default_factory=list)
    predicted_burn_kcal_low: int = 0
    predicted_burn_kcal_high: int = 0


@dataclass
class CoolDownBlock:
    duration_min: int = 0
    movements: list[Movement] = field(default_factory=list)
    breathing_protocol: str = ""        # "legs-up-the-wall 5min", "4-7-8 × 6"


@dataclass
class ContextSnapshot:
    """The cross-agent state Fraser read when composing this card. Stored
    inline so the audit log shows what Fraser saw at decision time."""
    hrv: int | None = None
    sleep_hours: float | None = None
    kobe_tier: str | None = None
    recovery_color: str | None = None
    active_injuries: list[str] = field(default_factory=list)
    equipment: list[str] = field(default_factory=list)
    time_of_day: str | None = None      # "morning", "evening", "10pm", etc.


@dataclass
class WorkoutNotes:
    why_this_design: str = ""
    deltas_from_request: list[str] = field(default_factory=list)
    prvn_position: str | None = None    # "W4D2"
    chest_progression_position: str | None = None  # "W6, target 8 reps"


@dataclass
class WorkoutCard:
    """The canonical artifact. Spec §2.5.

    Two-way JSON: `to_dict()` produces a payload-ready dict for the
    entity body; `from_dict(d)` reconstructs the dataclass tree. The
    round-trip test (tests/agents/fraser/test_protocols.py) pins both
    directions so a schema drift breaks the test loudly.
    """
    date_iso: str = ""                  # YYYY-MM-DD
    time_of_day: str = ""               # human label
    target_kcal: int = 0
    target_minutes: int = 0
    context: ContextSnapshot = field(default_factory=ContextSnapshot)
    warm_up: WarmUpBlock = field(default_factory=WarmUpBlock)
    strength: StrengthBlock = field(default_factory=StrengthBlock)
    wod: WODBlock = field(default_factory=WODBlock)
    cool_down: CoolDownBlock = field(default_factory=CoolDownBlock)
    notes: WorkoutNotes = field(default_factory=WorkoutNotes)
    input_mode: InputMode = InputMode.DEFAULT

    def to_dict(self) -> dict:
        """Plain-dict snapshot for JSON serialization (via asdict).
        Enum values are coerced to their `.value` strings so the
        substrate stores plain JSON, not Python repr."""
        d = asdict(self)
        # asdict() recurses through dataclasses but leaves Enum values
        # as Enum instances — coerce to their string values for JSON.
        d["input_mode"] = self.input_mode.value
        d["wod"]["format"] = self.wod.format.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorkoutCard":
        """Reconstruct from a plain dict (e.g., entity.payload). Forgiving
        of missing keys — partial cards (handler stubs) round-trip too."""
        if not d:
            return cls()
        ctx_d = d.get("context") or {}
        warm_d = d.get("warm_up") or {}
        str_d = d.get("strength") or {}
        wod_d = d.get("wod") or {}
        cool_d = d.get("cool_down") or {}
        notes_d = d.get("notes") or {}

        def _movs(raw: list | None) -> list[Movement]:
            return [Movement(**m) for m in (raw or [])]

        warm = WarmUpBlock(
            duration_min=warm_d.get("duration_min", 0),
            movements=_movs(warm_d.get("movements")),
            postural_cues=list(warm_d.get("postural_cues", [])),
        )
        strength = StrengthBlock(
            duration_min=str_d.get("duration_min", 0),
            lifts=[StrengthLift(**l) for l in (str_d.get("lifts") or [])],
        )
        wod = WODBlock(
            format=WodFormat(wod_d.get("format", WodFormat.FOR_TIME.value)),
            cap_min=wod_d.get("cap_min", 0),
            movements=_movs(wod_d.get("movements")),
            rounds_or_structure=wod_d.get("rounds_or_structure", ""),
            substitutions_applied=list(wod_d.get("substitutions_applied", [])),
            predicted_burn_kcal_low=wod_d.get("predicted_burn_kcal_low", 0),
            predicted_burn_kcal_high=wod_d.get("predicted_burn_kcal_high", 0),
        )
        cool = CoolDownBlock(
            duration_min=cool_d.get("duration_min", 0),
            movements=_movs(cool_d.get("movements")),
            breathing_protocol=cool_d.get("breathing_protocol", ""),
        )
        ctx = ContextSnapshot(**{k: ctx_d.get(k) for k in
                                 ("hrv", "sleep_hours", "kobe_tier",
                                  "recovery_color", "time_of_day")
                                 if ctx_d.get(k) is not None})
        ctx.active_injuries = list(ctx_d.get("active_injuries", []))
        ctx.equipment = list(ctx_d.get("equipment", []))
        notes = WorkoutNotes(
            why_this_design=notes_d.get("why_this_design", ""),
            deltas_from_request=list(notes_d.get("deltas_from_request", [])),
            prvn_position=notes_d.get("prvn_position"),
            chest_progression_position=notes_d.get("chest_progression_position"),
        )
        return cls(
            date_iso=d.get("date_iso", ""),
            time_of_day=d.get("time_of_day", ""),
            target_kcal=d.get("target_kcal", 0),
            target_minutes=d.get("target_minutes", 0),
            context=ctx,
            warm_up=warm,
            strength=strength,
            wod=wod,
            cool_down=cool,
            notes=notes,
            input_mode=InputMode(d.get("input_mode", InputMode.DEFAULT.value)),
        )


# ─────────────────────────── Entity body schemas ──────────────────────
# One dataclass per ENTITY_* constant. Each carries a `to_payload()` /
# `from_payload()` pair so callers in `state.py` don't reach into raw
# dicts. The shape is also what the eval suite asserts on.

@dataclass
class InjuryBody:
    body_part: str
    severity: Severity = Severity.MILD
    onset_iso: str = ""                          # YYYY-MM-DD
    mute_movements: list[str] = field(default_factory=list)  # normalized names
    eta_iso: str | None = None                   # YYYY-MM-DD expected resolution
    resolution_status: str = "active"            # active | healed | re-flared
    rationale: str | None = None

    def to_payload(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_payload(cls, d: dict) -> "InjuryBody":
        return cls(
            body_part=d.get("body_part", ""),
            severity=Severity(d.get("severity", Severity.MILD.value)),
            onset_iso=d.get("onset_iso", ""),
            mute_movements=[normalize_movement(m) for m in d.get("mute_movements", [])],
            eta_iso=d.get("eta_iso"),
            resolution_status=d.get("resolution_status", "active"),
            rationale=d.get("rationale"),
        )


@dataclass
class OneRepMaxBody:
    lift: str
    weight_kg: float
    tested_on_iso: str                           # YYYY-MM-DD
    source: OneRMSource = OneRMSource.USER_PROVIDED
    notes: str | None = None
    prior_entity_id: int | None = None           # for the supersede chain

    def to_payload(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["lift"] = normalize_lift_name(self.lift)
        return d

    @classmethod
    def from_payload(cls, d: dict) -> "OneRepMaxBody":
        return cls(
            lift=normalize_lift_name(d.get("lift", "")),
            weight_kg=float(d.get("weight_kg", 0.0)),
            tested_on_iso=d.get("tested_on_iso", ""),
            source=OneRMSource(d.get("source", OneRMSource.USER_PROVIDED.value)),
            notes=d.get("notes"),
            prior_entity_id=d.get("prior_entity_id"),
        )


@dataclass
class PreferenceBody:
    """fraser_preference. `target` is a movement name OR a format name
    (we store both kinds in the same entity type for query simplicity;
    `target_kind` discriminates)."""
    target: str
    target_kind: str = "movement"                # "movement" | "format"
    polarity: Polarity = Polarity.DISLIKE
    reason: str | None = None
    declared_on_iso: str = ""

    def to_payload(self) -> dict:
        d = asdict(self)
        d["polarity"] = self.polarity.value
        if self.target_kind == "movement":
            d["target"] = normalize_movement(self.target)
        return d

    @classmethod
    def from_payload(cls, d: dict) -> "PreferenceBody":
        return cls(
            target=d.get("target", ""),
            target_kind=d.get("target_kind", "movement"),
            polarity=Polarity(d.get("polarity", Polarity.DISLIKE.value)),
            reason=d.get("reason"),
            declared_on_iso=d.get("declared_on_iso", ""),
        )


@dataclass
class RouteBody:
    """Versioned route metadata. User confirmed Day 1: keep history.
    The substrate handles versioning via supersede_existing=True on
    same (agent, type, name) — `prior_entity_id` carries the chain."""
    name: str
    distance_km: float
    terrain: str = ""                            # "road", "trail", "mixed"
    gear_notes: str | None = None
    corrected_from_distance_km: float | None = None  # the "10k → 7.8k" story
    prior_entity_id: int | None = None
    declared_on_iso: str = ""

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, d: dict) -> "RouteBody":
        return cls(
            name=d.get("name", ""),
            distance_km=float(d.get("distance_km", 0.0)),
            terrain=d.get("terrain", ""),
            gear_notes=d.get("gear_notes"),
            corrected_from_distance_km=d.get("corrected_from_distance_km"),
            prior_entity_id=d.get("prior_entity_id"),
            declared_on_iso=d.get("declared_on_iso", ""),
        )


@dataclass
class PRVNPositionBody:
    """Current position in the PRVN cycle. One active at a time per
    user (supersede_existing=True for the new-position writes)."""
    week: int
    day: int
    phase: str = ""                              # "build", "intensify", "peak", "deload"
    last_completed_iso: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, d: dict) -> "PRVNPositionBody":
        return cls(
            week=int(d.get("week", 1)),
            day=int(d.get("day", 1)),
            phase=d.get("phase", ""),
            last_completed_iso=d.get("last_completed_iso"),
        )


@dataclass
class ChestProgressionBody:
    """10-week chest progression state. Single active row per cycle."""
    week: int
    day: int
    target_reps: int
    plateau_status: str = "advancing"            # advancing | stalled | regressing
    last_completed_iso: str | None = None
    cycle_start_iso: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, d: dict) -> "ChestProgressionBody":
        return cls(
            week=int(d.get("week", 1)),
            day=int(d.get("day", 1)),
            target_reps=int(d.get("target_reps", 0)),
            plateau_status=d.get("plateau_status", "advancing"),
            last_completed_iso=d.get("last_completed_iso"),
            cycle_start_iso=d.get("cycle_start_iso"),
        )


@dataclass
class SubstitutionRuleBody:
    """An equipment- or state-driven swap rule. `condition` is a short
    key the lookup index keys off (e.g., 'no_rope', 'no_pull_up_bar',
    'overhead_blocked'). `replacements` is ordered by preference —
    the first equipment-feasible option wins."""
    movement: str                                # canonical lower_snake
    condition: str
    replacements: list[str] = field(default_factory=list)
    reason_template: str = ""                    # "no rope → {replacement}"

    def to_payload(self) -> dict:
        return {
            "movement": normalize_movement(self.movement),
            "condition": self.condition,
            "replacements": [normalize_movement(r) for r in self.replacements],
            "reason_template": self.reason_template,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "SubstitutionRuleBody":
        return cls(
            movement=normalize_movement(d.get("movement", "")),
            condition=d.get("condition", ""),
            replacements=[normalize_movement(r) for r in d.get("replacements", [])],
            reason_template=d.get("reason_template", ""),
        )


@dataclass
class WorkoutBody:
    """fraser_workout payload — the full card plus light denormalized
    fields the substrate's status / valid_until filters key off."""
    date_iso: str
    completion_status: CompletionStatus = CompletionStatus.PLANNED
    target_kcal: int = 0
    target_minutes: int = 0
    card: WorkoutCard = field(default_factory=WorkoutCard)
    # Set on log_session — the post-workout truth.
    actual_kcal: int | None = None
    actual_rpe: int | None = None
    actual_volume_summary: str | None = None

    def to_payload(self) -> dict:
        return {
            "date_iso": self.date_iso,
            "completion_status": self.completion_status.value,
            "target_kcal": self.target_kcal,
            "target_minutes": self.target_minutes,
            "card": self.card.to_dict(),
            "actual_kcal": self.actual_kcal,
            "actual_rpe": self.actual_rpe,
            "actual_volume_summary": self.actual_volume_summary,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "WorkoutBody":
        return cls(
            date_iso=d.get("date_iso", ""),
            completion_status=CompletionStatus(
                d.get("completion_status", CompletionStatus.PLANNED.value)),
            target_kcal=d.get("target_kcal", 0),
            target_minutes=d.get("target_minutes", 0),
            card=WorkoutCard.from_dict(d.get("card") or {}),
            actual_kcal=d.get("actual_kcal"),
            actual_rpe=d.get("actual_rpe"),
            actual_volume_summary=d.get("actual_volume_summary"),
        )


@dataclass
class MovementInstanceBody:
    """fraser_movement payload — one logged execution of a movement.
    Used by the recent-volume queries to track posterior-chain density
    across the week (spec §5 item 11)."""
    workout_entity_id: int
    movement: str                                # canonical lower_snake
    load_kg: float | None = None
    reps: int | None = None
    executed_volume_kcal: int | None = None
    substitution_reason: str | None = None
    logged_at_iso: str = ""

    def to_payload(self) -> dict:
        d = asdict(self)
        d["movement"] = normalize_movement(self.movement)
        return d

    @classmethod
    def from_payload(cls, d: dict) -> "MovementInstanceBody":
        return cls(
            workout_entity_id=int(d.get("workout_entity_id", 0)),
            movement=normalize_movement(d.get("movement", "")),
            load_kg=d.get("load_kg"),
            reps=d.get("reps"),
            executed_volume_kcal=d.get("executed_volume_kcal"),
            substitution_reason=d.get("substitution_reason"),
            logged_at_iso=d.get("logged_at_iso", ""),
        )


@dataclass
class WarmUpBody:
    """fraser_warmup payload — a reusable warm-up template (not the
    per-card warm-up block, which lives inside WorkoutCard.warm_up)."""
    name: str
    duration_min: int
    movements: list[Movement] = field(default_factory=list)
    postural_targets: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "duration_min": self.duration_min,
            "movements": [asdict(m) for m in self.movements],
            "postural_targets": list(self.postural_targets),
        }

    @classmethod
    def from_payload(cls, d: dict) -> "WarmUpBody":
        return cls(
            name=d.get("name", ""),
            duration_min=int(d.get("duration_min", 0)),
            movements=[Movement(**m) for m in d.get("movements", [])],
            postural_targets=list(d.get("postural_targets", [])),
        )


@dataclass
class CoolDownBody:
    """fraser_cooldown payload — reusable recovery template."""
    name: str
    duration_min: int
    movements: list[Movement] = field(default_factory=list)
    breathing_protocol: str = ""

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "duration_min": self.duration_min,
            "movements": [asdict(m) for m in self.movements],
            "breathing_protocol": self.breathing_protocol,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "CoolDownBody":
        return cls(
            name=d.get("name", ""),
            duration_min=int(d.get("duration_min", 0)),
            movements=[Movement(**m) for m in d.get("movements", [])],
            breathing_protocol=d.get("breathing_protocol", ""),
        )


# ─────────────────────────── Charter-rule schemas ─────────────────────
@dataclass
class CharterRuleSpec:
    """A declarative spec for the Fraser-specific Charter policies that
    `core/charter.py` should register at boot. P2 (Day 1) wires the
    actual policy functions; today we publish the SHAPE so the
    architect-review can see what rules will exist before the writes
    are implemented."""
    kind: str                                    # one of ALL_CHARTER_KINDS
    description: str
    blocks_during_quiet_hours: bool = False
    blocks_during_hrv_red: bool = False
    blocks_during_family_priority: bool = False
    requires_huberman_green_for_increase: bool = False
    notes: str | None = None


FRASER_CHARTER_RULE_SPECS: tuple[CharterRuleSpec, ...] = (
    CharterRuleSpec(
        kind=CHARTER_COMMIT_WORKOUT,
        description="Designed-workout commit. Quiet-hour & HRV-red guarded.",
        blocks_during_quiet_hours=True,
        blocks_during_hrv_red=True,
        notes="HRV-red bypass requires explicit user override token in payload.",
    ),
    CharterRuleSpec(
        kind=CHARTER_LOG_SESSION,
        description="Post-workout session log. Always allowed.",
    ),
    CharterRuleSpec(
        kind=CHARTER_REGISTER_INJURY,
        description="Injury registration. Always allowed; mutes movements.",
    ),
    CharterRuleSpec(
        kind=CHARTER_RESOLVE_INJURY,
        description="Injury resolution. Always allowed; requires explicit user signal.",
    ),
    CharterRuleSpec(
        kind=CHARTER_UPDATE_1RM,
        description="Single-lift 1RM write. Increases require Huberman green.",
        requires_huberman_green_for_increase=True,
    ),
    CharterRuleSpec(
        kind=CHARTER_INGEST_1RM_BATCH,
        description="Bulk 1RM batch. Per-record Huberman gate; full batch logged.",
        requires_huberman_green_for_increase=True,
    ),
    CharterRuleSpec(
        kind=CHARTER_RECORD_PREFERENCE,
        description="Like/dislike capture. Always allowed.",
    ),
    CharterRuleSpec(
        kind=CHARTER_RECORD_ROUTE,
        description="Route correction. Always allowed.",
    ),
    CharterRuleSpec(
        kind=CHARTER_PROPOSE_SUBSTITUTE,
        description="Substitution proposal. Always allowed; logs rationale.",
    ),
    CharterRuleSpec(
        kind=CHARTER_ADVANCE_PRVN,
        description="PRVN cycle advancement. Requires last session completed.",
    ),
    CharterRuleSpec(
        kind=CHARTER_ADVANCE_PROGRESSION,
        description="Chest progression advancement. Requires last week's reps hit.",
    ),
)


# ─────────────────────────── Public surface ───────────────────────────
__all__ = [
    # Identity
    "AGENT",
    # Entity types
    "ENTITY_WORKOUT", "ENTITY_MOVEMENT", "ENTITY_INJURY",
    "ENTITY_PRVN_CYCLE", "ENTITY_PROGRESSION",
    "ENTITY_WARMUP", "ENTITY_COOLDOWN", "ENTITY_SUBSTITUTION",
    "ENTITY_ONE_REP_MAX", "ENTITY_PREFERENCE", "ENTITY_ROUTE",
    "ALL_ENTITY_TYPES",
    # Charter kinds
    "CHARTER_COMMIT_WORKOUT", "CHARTER_LOG_SESSION",
    "CHARTER_REGISTER_INJURY", "CHARTER_RESOLVE_INJURY",
    "CHARTER_UPDATE_1RM", "CHARTER_INGEST_1RM_BATCH",
    "CHARTER_RECORD_PREFERENCE", "CHARTER_RECORD_ROUTE",
    "CHARTER_PROPOSE_SUBSTITUTE", "CHARTER_ADVANCE_PRVN",
    "CHARTER_ADVANCE_PROGRESSION",
    "ALL_CHARTER_KINDS",
    "CharterRuleSpec", "FRASER_CHARTER_RULE_SPECS",
    # Enums
    "InputMode", "RecoveryColor", "KobeTier", "WodFormat",
    "Polarity", "Severity", "OneRMSource", "CompletionStatus",
    # Lift & movement vocab
    "LIFTS", "PRESSING_MOVEMENTS", "PULLING_MOVEMENTS",
    "POSTERIOR_CHAIN", "OVERHEAD_MOVEMENTS",
    # Staleness thresholds
    "ONE_RM_WARN_AFTER_DAYS", "ONE_RM_BLOCK_AFTER_DAYS",
    # Normalizers
    "normalize_lift_name", "normalize_movement",
    "is_pressing", "is_pulling", "is_overhead", "loads_posterior_chain",
    # Workout Card schema
    "Movement", "StrengthLift",
    "WarmUpBlock", "StrengthBlock", "WODBlock", "CoolDownBlock",
    "ContextSnapshot", "WorkoutNotes", "WorkoutCard",
    # Entity body schemas
    "WorkoutBody", "MovementInstanceBody", "InjuryBody",
    "PRVNPositionBody", "ChestProgressionBody",
    "WarmUpBody", "CoolDownBody", "SubstitutionRuleBody",
    "OneRepMaxBody", "PreferenceBody", "RouteBody",
]
