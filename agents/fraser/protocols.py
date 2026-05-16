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
ENTITY_SOURCE_WORKOUT = "fraser_source_workout"
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
    ENTITY_SOURCE_WORKOUT,
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


# ─────────────────────────── System-prompt version ────────────────────
# Stamped onto every `fraser_workout` entity body via
# `state.commit_workout`. The bisectability story: when quality
# regresses, query workouts by version, find when the regression
# started, blame the prompt change.
#
# Bump trigger: any STRUCTURAL change to the system prompt — the
# preamble shape, the tool-catalog format, the input-mode classifier
# instructions, the rule-injection ordering. Pure content edits to
# `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` do NOT bump (the transcript
# IS the prompt content; structural transforms wrap it).
#
# When you bump:
#   • Increment the constant ("v1" → "v2").
#   • Add a one-line entry to the version-history block below.
#   • Write a regression eval case that exercises the structural
#     change so future bumps don't silently break the case.
#
# Version history:
#   v1 — initial structural shape (Day-3 wiring 2026-05-14):
#        transcript + FRASER_CHARTER_RULE_SPECS + TOOL_CATALOG +
#        InputMode classification block.
#   v2 — adapter pivot (Day-5 directive 2026-05-14): Fraser is an
#        ADAPTATION engine, not a generator. Default mode reads
#        today's `fraser_source_workout` from the SugarWOD substrate
#        and personalizes it. System prompt now leads with the
#        adaptation contract; `get_todays_source_workout` is the
#        primary read tool. See §2.4 of spec.
#   v3 — kcal-target pivot (Day-6 directive 2026-05-14): Kobe owns
#        the daily kcal target; Fraser reads it via
#        `get_kobe_kcal_target` and scales the adapted card up/down
#        to land within ±20% of the target. The Kobe→Fraser
#        contract is now load-bearing on this dimension. System
#        prompt explicitly lists the scaling rules.
FRASER_SYSTEM_PROMPT_VERSION: str = "v3"


# ─────────────────────────── Source-workout freshness ───────────────
# `get_todays_source_workout` returns STALE_SOURCE_WORKOUT when the
# ingestion's `fetched_at` is older than this threshold. Past
# incidents (DOM-class rename, "MON"/"Mon" case bug) silently broke
# the scrape for weeks — the freshness gate exists so Fraser surfaces
# the gap explicitly rather than producing stale-data cards.
SOURCE_WORKOUT_STALE_AFTER_DAYS: int = 7


# Sentinel for stale-source signal. Distinct from `None` (which means
# "no source workout for today — rest day or no scrape this week").
# Test the return value with `is STALE_SOURCE_WORKOUT` for identity
# comparison; callers MUST NOT serialize this sentinel.
class _StaleSourceSentinel:
    def __repr__(self) -> str:
        return "<STALE_SOURCE_WORKOUT>"
    def __bool__(self) -> bool:
        return False


STALE_SOURCE_WORKOUT = _StaleSourceSentinel()


# ─────────────────────────── Substitution conditions ────────────────────
# Stable string vocabulary keyed off (movement, condition) pairs in
# SubstitutionRuleBody. Strings + tuple validation, NOT Enum — mirrors
# the `dislikes.SCOPES` and `BLACKLIST` patterns elsewhere in the repo.
#
# Promotion trigger (per ADR-004 §"Substitution conditions"): if the
# number of call sites with exhaustive `match condition:` blocks
# exceeds five, promote to `Enum`. That's a real trigger, not a vibe:
# at five exhaustive matches the cost of "is this string in the set"
# guards starts outpacing the cost of an enum-import round.
#
# Add new conditions here in alphabetical order; touch the seed in
# state.DEFAULT_SUBSTITUTION_SEED in the same PR.
SUBSTITUTION_CONDITIONS: tuple[str, ...] = (
    "equipment_missing",     # the gear isn't available (no rope, no rack, no box)
    "format_incompatible",   # movement doesn't fit the requested WOD format
    "mobility_limit",        # injury or mobility issue blocks this movement
    "recovery_gate",         # HRV/sleep state blocks this intensity
    "rx_unavailable",        # the prescribed load/scale isn't doable
    "time_constrained",      # WOD time budget too tight for this movement at rx
    "user_dislike",          # user declared a fraser_preference dislike
)


def _validate_condition(c: str) -> str:
    """Return the condition unchanged if it's in the vocabulary; raise
    otherwise. Called from `SubstitutionRuleBody.to_payload` so a typo
    fails LOUDLY at write-time rather than becoming an unfindable orphan
    in the substrate.

    Promote to an `Enum` when the trigger in ADR-004 fires.
    """
    if c not in SUBSTITUTION_CONDITIONS:
        raise ValueError(
            f"unknown substitution condition {c!r}. "
            f"Allowed: {SUBSTITUTION_CONDITIONS}. "
            f"Add new conditions to protocols.SUBSTITUTION_CONDITIONS "
            f"(see ADR-004 §'Substitution conditions').")
    return c


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
        """Write-time validation of `condition` against
        `SUBSTITUTION_CONDITIONS` — a typo here fails LOUDLY now,
        not silently in production. The same instinct as
        `dislikes._normalize_movement` / `dislikes.SCOPES`."""
        return {
            "movement": normalize_movement(self.movement),
            "condition": _validate_condition(self.condition),
            "replacements": [normalize_movement(r) for r in self.replacements],
            "reason_template": self.reason_template,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "SubstitutionRuleBody":
        # Read path is permissive — old rows from before a vocabulary
        # change shouldn't crash the reader. Validation lives on writes.
        return cls(
            movement=normalize_movement(d.get("movement", "")),
            condition=d.get("condition", ""),
            replacements=[normalize_movement(r) for r in d.get("replacements", [])],
            reason_template=d.get("reason_template", ""),
        )


# ─────────────────────────── Parsed source-workout types ──────────────
@dataclass
class ParsedMovement:
    """One movement inside a parsed source-workout section.

    `raw_text` carries the original line so a parser-bug postmortem
    can replay through better extraction later (see "store BOTH raw +
    parsed" in §11.5)."""
    name: str                            # normalized lower_snake
    reps_or_time: str = ""               # "18", "15/11 cal", "400m", etc.
    load_text: str = ""                  # "53/35lb, 24/16kg" — raw, unparsed
    raw_text: str = ""                   # the original line


@dataclass
class ParsedSection:
    """One section of a SugarWOD day: WOD body / Strength / Levels /
    PRVN Reset / Optional Accessories / Specific Prep."""
    title: str                           # original title verbatim
    section_kind: str = "unknown"        # "strength" | "prep" | "wod" | "levels" | "reset" | "accessory" | "rest"
    format: str = ""                     # "For Time" | "EMOM" | "AMRAP" | "Every X:XX x N Sets" | "For Quality" | ""
    cap_min: int = 0                     # parsed cap; 0 = unbounded
    rounds_or_structure: str = ""        # "21-15-9", "4 Sets", "AMRAP 15"
    movements: list[ParsedMovement] = field(default_factory=list)
    raw_description: str = ""            # original description for round-trip
    is_blacklisted: bool = False         # Kobe BLACKLIST hit at parse time
    blacklist_reason: str = ""           # which term matched
    is_skip_section: bool = False        # Kobe SKIP_SECTION_TITLES hit


@dataclass
class ParsedWorkout:
    """A whole day's parsed source-workout — one or more sections.

    `is_rest_day` is True when the SugarWOD response was empty or
    contained only a "Rest Day" / "Active Recovery" placeholder.
    `primary_wod_index` points to the section that's the day's main
    WOD (the one Fraser adapts); rest are warm-up / accessory."""
    date_int: str                        # "20260514"
    header: str = ""                     # "THU 14"
    is_rest_day: bool = False
    rest_day_label: str = ""             # "Rest Day" or "Active Recovery"
    sections: list[ParsedSection] = field(default_factory=list)
    primary_wod_index: int = -1          # -1 if no clear primary WOD
    blacklisted_section_count: int = 0


@dataclass
class FraserSourceWorkoutBody:
    """Entity body for `fraser_source_workout`. Stores BOTH raw archive
    snippet AND parsed structure (§11.5 doctrine — future-proofs the
    parser; reparse from raw when extraction improves)."""
    date_int: str                        # "20260514"
    header: str = ""                     # "THU 14"
    fetched_at_iso: str = ""             # archive's fetched_at field
    gym_program_name: str = "workout-of-the-day"
    ingestion_method: str = "sugarwod_bookmarklet"
    workouts_raw: list[dict] = field(default_factory=list)   # [{title, description}, …]
    parsed: ParsedWorkout | None = None
    supersedes_entity_id: int | None = None

    def to_payload(self) -> dict:
        out = {
            "date_int": self.date_int,
            "header": self.header,
            "fetched_at_iso": self.fetched_at_iso,
            "gym_program_name": self.gym_program_name,
            "ingestion_method": self.ingestion_method,
            "workouts_raw": list(self.workouts_raw),
            "supersedes_entity_id": self.supersedes_entity_id,
        }
        if self.parsed is not None:
            out["parsed"] = {
                "date_int": self.parsed.date_int,
                "header": self.parsed.header,
                "is_rest_day": self.parsed.is_rest_day,
                "rest_day_label": self.parsed.rest_day_label,
                "primary_wod_index": self.parsed.primary_wod_index,
                "blacklisted_section_count": self.parsed.blacklisted_section_count,
                "sections": [
                    {
                        "title": s.title, "section_kind": s.section_kind,
                        "format": s.format, "cap_min": s.cap_min,
                        "rounds_or_structure": s.rounds_or_structure,
                        "raw_description": s.raw_description,
                        "is_blacklisted": s.is_blacklisted,
                        "blacklist_reason": s.blacklist_reason,
                        "is_skip_section": s.is_skip_section,
                        "movements": [
                            {"name": m.name, "reps_or_time": m.reps_or_time,
                             "load_text": m.load_text, "raw_text": m.raw_text}
                            for m in s.movements
                        ],
                    }
                    for s in self.parsed.sections
                ],
            }
        return out

    @classmethod
    def from_payload(cls, d: dict) -> "FraserSourceWorkoutBody":
        parsed = None
        pd = d.get("parsed")
        if pd:
            sections = []
            for s in pd.get("sections", []):
                movs = [
                    ParsedMovement(
                        name=m.get("name", ""),
                        reps_or_time=m.get("reps_or_time", ""),
                        load_text=m.get("load_text", ""),
                        raw_text=m.get("raw_text", ""),
                    )
                    for m in s.get("movements", [])
                ]
                sections.append(ParsedSection(
                    title=s.get("title", ""),
                    section_kind=s.get("section_kind", "unknown"),
                    format=s.get("format", ""),
                    cap_min=int(s.get("cap_min", 0) or 0),
                    rounds_or_structure=s.get("rounds_or_structure", ""),
                    movements=movs,
                    raw_description=s.get("raw_description", ""),
                    is_blacklisted=bool(s.get("is_blacklisted", False)),
                    blacklist_reason=s.get("blacklist_reason", ""),
                    is_skip_section=bool(s.get("is_skip_section", False)),
                ))
            parsed = ParsedWorkout(
                date_int=pd.get("date_int", ""),
                header=pd.get("header", ""),
                is_rest_day=bool(pd.get("is_rest_day", False)),
                rest_day_label=pd.get("rest_day_label", ""),
                sections=sections,
                primary_wod_index=int(pd.get("primary_wod_index", -1)),
                blacklisted_section_count=int(
                    pd.get("blacklisted_section_count", 0) or 0),
            )
        return cls(
            date_int=d.get("date_int", ""),
            header=d.get("header", ""),
            fetched_at_iso=d.get("fetched_at_iso", ""),
            gym_program_name=d.get("gym_program_name", "workout-of-the-day"),
            ingestion_method=d.get("ingestion_method", "sugarwod_bookmarklet"),
            workouts_raw=list(d.get("workouts_raw", [])),
            parsed=parsed,
            supersedes_entity_id=d.get("supersedes_entity_id"),
        )


@dataclass
class WorkoutBody:
    """fraser_workout payload — the full card plus light denormalized
    fields the substrate's status / valid_until filters key off."""
    date_iso: str
    source_id: int | None = None         # link back to fraser_source_workout
    completion_status: CompletionStatus = CompletionStatus.PLANNED
    target_kcal: int = 0
    target_minutes: int = 0
    card: WorkoutCard = field(default_factory=WorkoutCard)
    # Set on log_session — the post-workout truth.
    actual_kcal: int | None = None
    actual_rpe: int | None = None
    actual_volume_summary: str | None = None
    # Stamped by `state.commit_workout` from `FRASER_SYSTEM_PROMPT_VERSION`.
    # Defaults to None so old rows (pre-Day-4) don't fail to deserialize.
    # See protocols.FRASER_SYSTEM_PROMPT_VERSION for the bump policy.
    system_prompt_version: str | None = None

    def to_payload(self) -> dict:
        return {
            "date_iso": self.date_iso,
            "source_id": self.source_id,
            "completion_status": self.completion_status.value,
            "target_kcal": self.target_kcal,
            "target_minutes": self.target_minutes,
            "card": self.card.to_dict(),
            "actual_kcal": self.actual_kcal,
            "actual_rpe": self.actual_rpe,
            "actual_volume_summary": self.actual_volume_summary,
            "system_prompt_version": self.system_prompt_version,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "WorkoutBody":
        return cls(
            date_iso=d.get("date_iso", ""),
            source_id=d.get("source_id"),
            completion_status=CompletionStatus(
                d.get("completion_status", CompletionStatus.PLANNED.value)),
            target_kcal=d.get("target_kcal", 0),
            target_minutes=d.get("target_minutes", 0),
            card=WorkoutCard.from_dict(d.get("card") or {}),
            actual_kcal=d.get("actual_kcal"),
            actual_rpe=d.get("actual_rpe"),
            actual_volume_summary=d.get("actual_volume_summary"),
            system_prompt_version=d.get("system_prompt_version"),
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


# ─────────────────────────── Tool catalog ──────────────────────────────
# Hand-rolled manifests for the reasoner's tool-call surface. Per the
# Day-4 directive (2026-05-14), this is NOT auto-generated from
# inspect.signature — the LLM needs stable parameter names independent
# of Python kwarg renames, curated "WHEN to use" descriptions (not
# Python docstrings, which are usually implementation notes), and
# explicit JSON-shaped schemas (Python type hints don't always JSON-
# serialize cleanly).
#
# Adding a new tool means TWO edits: the implementation in tools.py
# AND an entry here. The coverage test
# `tests/test_fraser_tool_catalog.py::test_every_public_tool_has_manifest_entry`
# enforces this by failing if either side drifts.

# ─────────────────────────── Movement kcal model ─────────────────────
# Per Day-6 finding #1: distance-heavy WODs (run, row, farmers carry)
# were under-predicting burn because reps/time alone don't model them.
# This table assigns one or more burn dimensions per movement:
#
#     per_rep_kcal     — for rep-bounded movements (KB swing, burpee)
#     per_meter_kcal   — for distance work (run, row, bike, carry)
#     per_second_kcal  — for isometric / time-bounded work (wall sit,
#                        plank, dead hang)
#
# `compute_predicted_burn` consults this table BEFORE falling back to
# the kcal-per-minute coefficients in `tools.KCAL_PER_MIN_BY_MOVEMENT`.
# Multiple dimensions can fire for a single movement (e.g., the
# rare "reps + distance" framings); the burn estimator sums them.
#
# Numbers are coach-tunable defaults — empirical references in the
# `notes` field. Reference body weight ≈ 75 kg; per-user calibration
# lands in a `fraser_preference` key down the road.
@dataclass(frozen=True)
class MovementKcalProfile:
    per_rep_kcal: float = 0.0
    per_meter_kcal: float = 0.0
    per_second_kcal: float = 0.0
    notes: str = ""


MOVEMENT_KCAL_MODEL: dict[str, MovementKcalProfile] = {
    # Distance-based — the Day-6 #1 bug. 400m run @ 0.075 = 30 kcal.
    "run":              MovementKcalProfile(per_meter_kcal=0.075,
                                            notes="~75 kcal/km @ 75kg jog pace"),
    "z2_run":           MovementKcalProfile(per_meter_kcal=0.060,
                                            notes="zone-2 pace ~60 kcal/km"),
    "row":              MovementKcalProfile(per_meter_kcal=0.10,
                                            notes="~50 kcal per 500m"),
    "echo_bike":        MovementKcalProfile(per_meter_kcal=0.04,
                                            notes="~40 kcal/km"),
    "assault_bike":     MovementKcalProfile(per_meter_kcal=0.04),
    "ski_erg":          MovementKcalProfile(per_meter_kcal=0.10),
    "farmers_carry":    MovementKcalProfile(per_meter_kcal=0.15,
                                            notes="load-weighted; ~75kg dual DB"),
    "shuttle_run":      MovementKcalProfile(per_meter_kcal=0.10,
                                            notes="higher than steady run — pivots"),
    # Time-based isometric.
    "wall_sit":         MovementKcalProfile(per_second_kcal=0.083,
                                            notes="~5 kcal/min"),
    "plank":            MovementKcalProfile(per_second_kcal=0.050,
                                            notes="~3 kcal/min"),
    "dead_hang":        MovementKcalProfile(per_second_kcal=0.083),
    "l_sit":            MovementKcalProfile(per_second_kcal=0.083),
    # Rep-based — augment the per-minute model with per-rep granularity.
    "burpee":           MovementKcalProfile(per_rep_kcal=1.0),
    "burpee_box_jump":  MovementKcalProfile(per_rep_kcal=1.3),
    "kettlebell_swing": MovementKcalProfile(per_rep_kcal=0.5),
    "thruster":         MovementKcalProfile(per_rep_kcal=1.2),
    "wall_ball":        MovementKcalProfile(per_rep_kcal=0.7),
    "pull_up":          MovementKcalProfile(per_rep_kcal=0.5),
    "chin_up":          MovementKcalProfile(per_rep_kcal=0.5),
    "push_up":          MovementKcalProfile(per_rep_kcal=0.3),
    "air_squat":        MovementKcalProfile(per_rep_kcal=0.3),
    "box_jump":         MovementKcalProfile(per_rep_kcal=0.8),
    "box_step_up":      MovementKcalProfile(per_rep_kcal=0.5),
    "deadlift":         MovementKcalProfile(per_rep_kcal=1.0,
                                            notes="moderate load assumed"),
    "sumo_deadlift":    MovementKcalProfile(per_rep_kcal=1.0),
    "back_squat":       MovementKcalProfile(per_rep_kcal=0.8),
    "front_squat":      MovementKcalProfile(per_rep_kcal=0.9),
    "bench":            MovementKcalProfile(per_rep_kcal=0.5),
    "strict_press":     MovementKcalProfile(per_rep_kcal=0.5),
    "push_press":       MovementKcalProfile(per_rep_kcal=0.8),
    "clean":            MovementKcalProfile(per_rep_kcal=1.3),
    "power_snatch":     MovementKcalProfile(per_rep_kcal=1.5),
    "snatch":           MovementKcalProfile(per_rep_kcal=1.5),
    "double_under":     MovementKcalProfile(per_rep_kcal=0.15),
    "jump_rope":        MovementKcalProfile(per_rep_kcal=0.10),
    "single_under":     MovementKcalProfile(per_rep_kcal=0.08),
    "penguin_jump":     MovementKcalProfile(per_rep_kcal=0.20),
    "lateral_hop":      MovementKcalProfile(per_rep_kcal=0.18),
    "abmat_sit_up":     MovementKcalProfile(per_rep_kcal=0.25),
    "toes_to_bar":      MovementKcalProfile(per_rep_kcal=0.7),
    "handstand_push_up": MovementKcalProfile(per_rep_kcal=0.8),
    "devil_press":      MovementKcalProfile(per_rep_kcal=1.4),
    "ring_row":         MovementKcalProfile(per_rep_kcal=0.4),
    "trx_row":          MovementKcalProfile(per_rep_kcal=0.4),
}


# ─────────────────────────── BW-scaling model ─────────────────────────
# Per Day-6 finding #3: movements without a 1RM (KB swing, wall sit,
# push-up, plank, BW farmers carry) need a tier-driven Rx, not a %
# of 1RM. This table maps movement → tier → prescription. The handler
# picks the user's tier's value and stamps the rationale on the
# Movement so the card surfaces it.
@dataclass(frozen=True)
class BodyweightScaling:
    by_tier: dict                            # tier name → value
    units: str = ""                          # "kg" / "s" / "reps" / "kg per hand"
    rationale_template: str = (
        "{movement} @ {value}{units} — Rx for {tier} tier"
    )


BW_SCALING_MODEL: dict[str, BodyweightScaling] = {
    "kettlebell_swing": BodyweightScaling(
        by_tier={"hammer": 32, "zone2": 24, "deload": 16,
                 "survival": 12},
        units=" kg"),
    "wall_sit": BodyweightScaling(
        by_tier={"hammer": 90, "zone2": 60, "deload": 45,
                 "survival": 30},
        units="s"),
    "plank": BodyweightScaling(
        by_tier={"hammer": 90, "zone2": 60, "deload": 45,
                 "survival": 30},
        units="s"),
    "push_up": BodyweightScaling(
        by_tier={"hammer": 25, "zone2": 15, "deload": 10,
                 "survival": 5},
        units=" reps"),
    "farmers_carry": BodyweightScaling(
        by_tier={"hammer": 32, "zone2": 22.5, "deload": 15,
                 "survival": 10},
        units=" kg per hand"),
    "dead_hang": BodyweightScaling(
        by_tier={"hammer": 60, "zone2": 45, "deload": 30,
                 "survival": 20},
        units="s"),
}


# ─────────────────────────── Kobe-target scaling thresholds ───────────
# Per Day-6 finding #4: Fraser hits Kobe's target_kcal within ±20%.
# Scaling decisions outside the band are mechanical:
#   predicted < target × KCAL_TARGET_BAND_LOW → scale UP
#   predicted > target × KCAL_TARGET_BAND_HIGH → scale DOWN
#   else → leave card unchanged
KCAL_TARGET_BAND_LOW: float  = 0.80
KCAL_TARGET_BAND_HIGH: float = 1.20


@dataclass(frozen=True)
class ToolManifest:
    """Description of one tool that the reasoner can call.

    Fields:
        name:             Python function name in `agents/fraser/tools.py`
                          (must match exactly — the coverage test keys
                          off this).
        description:      "WHEN to use this" — speak to the LLM, not to
                          a Python reader. Lead with the situation, not
                          the implementation. ~1–3 sentences.
        args_schema:      JSON-shaped per-arg spec:
                          {arg_name: {"type": ..., "description": ...,
                                      "required": bool}}.
                          Use JSON-schema-ish types: string / number /
                          integer / object / array / boolean.
        returns_schema:   JSON-shaped return spec:
                          {"type": ..., "description": ...}.
                          For complex returns, use {"type": "object",
                          "properties": {...}}.
    """
    name: str
    description: str
    args_schema: dict
    returns_schema: dict


TOOL_CATALOG: tuple[ToolManifest, ...] = (
    ToolManifest(
        name="get_kobe_kcal_target",
        description=(
            "Use to read today's calorie target set by Kobe. "
            "Returns a float (kcal) or None. Fraser's adapted card "
            "must land within ±20% of this target — if it doesn't, "
            "scale up (add round / increase load / lengthen cap) or "
            "scale down (drop round / lighten load / shorten cap) "
            "to fit. Surface the predicted-vs-target math in the "
            "Workout Card NOTES."
        ),
        args_schema={},
        returns_schema={
            "type": "number",
            "description": (
                "Today's target kcal from Kobe, or null if no "
                "target is set."
            ),
        },
    ),
    ToolManifest(
        name="get_todays_source_workout",
        description=(
            "Use FIRST in Default mode. Returns today's gym-prescribed "
            "workout from SugarWOD ingestion — the source you ADAPT. "
            "Three return shapes: a parsed-workout dict (normal path), "
            "None (rest day or no scrape this week), or the STALE "
            "sentinel (last ingest > 7d old — surface 'click the "
            "bookmarklet' to the user, do NOT use stale data)."
        ),
        args_schema={},
        returns_schema={
            "type": "object",
            "description": (
                "FraserSourceWorkoutBody payload OR None OR STALE "
                "sentinel. Caller MUST handle all three explicitly."
            ),
        },
    ),
    ToolManifest(
        name="get_source_workout",
        description=(
            "Historical lookup of a specific date's source workout. "
            "Use for 'did we already do back squats this week?' "
            "reasoning. No freshness gate — historical data is "
            "historical. Pass `date_int` as YYYYMMDD."
        ),
        args_schema={
            "date_int": {
                "type": "string",
                "description": "YYYYMMDD (e.g., '20260514').",
                "required": True,
            },
        },
        returns_schema={
            "type": "object",
            "description": "FraserSourceWorkoutBody payload OR None.",
        },
    ),
    ToolManifest(
        name="compute_target_weight",
        description=(
            "Use when you need the working weight in kg for a "
            "percentage of a user's 1RM. The 1RM and percentage come "
            "from your reasoning (e.g., '70% of deadlift after HRV "
            "scaling'); this tool just does the math + snaps to the "
            "2.5-kg plate grid (rounds DOWN so you never program "
            "more weight than the math implies)."
        ),
        args_schema={
            "lift": {
                "type": "string",
                "description": (
                    "Canonical lift name. Aliases accepted "
                    "(DL→deadlift, BS→back_squat, Bench Press→bench)."
                ),
                "required": True,
            },
            "percentage": {
                "type": "number",
                "description": "0–150. Above 100 allowed for overload work.",
                "required": True,
            },
            "one_rm_kg": {
                "type": "number",
                "description": "The user's tested 1RM in kg.",
                "required": True,
            },
            "plate_increment_kg": {
                "type": "number",
                "description": (
                    "Plate granularity for the snap. Default 2.5; "
                    "pass 5.0 for Olympic-lift contexts where the "
                    "bar is loaded with full plates only."
                ),
                "required": False,
            },
        },
        returns_schema={
            "type": "number",
            "description": (
                "Target weight in kg, snapped DOWN to the plate grid. "
                "Returns 0.0 if 1RM or percentage is non-positive."
            ),
        },
    ),
    ToolManifest(
        name="compute_predicted_burn",
        description=(
            "Use when you need a LOW/HIGH calorie band for a designed "
            "Workout Card, and when the user asks 'why does X burn "
            "more than Y?' Returns per-movement breakdown so you can "
            "quote the math. The estimate is conservative — tune the "
            "midpoint against actual log data over time."
        ),
        args_schema={
            "card": {
                "type": "object",
                "description": (
                    "A WorkoutCard dict (serialized via "
                    "WorkoutCard.to_dict()) OR an in-memory "
                    "WorkoutCard instance. Both round-trip cleanly."
                ),
                "required": True,
            },
        },
        returns_schema={
            "type": "object",
            "properties": {
                "total_low": {
                    "type": "integer",
                    "description": "Lower-bound kcal sum.",
                },
                "total_high": {
                    "type": "integer",
                    "description": "Upper-bound kcal sum.",
                },
                "by_movement": {
                    "type": "array",
                    "description": (
                        "Ordered list of "
                        "{movement, minutes_estimated, kcal_low, "
                        "kcal_high}. Quote these when the user "
                        "questions the predicted burn."
                    ),
                },
            },
        },
    ),
    ToolManifest(
        name="lookup_movement_cues",
        description=(
            "Use whenever you program a movement that touches a known "
            "category (pressing → neck-guard + HBP; squat → ankle-"
            "check + HBP; pull → hunch; Olympic → all three + HBP). "
            "Surface the returned cues in the WORKOUT CARD's NOTES "
            "section. Unknown movements get the HBP rule by default "
            "— that's the cue that's never harmful to surface."
        ),
        args_schema={
            "movement": {
                "type": "string",
                "description": (
                    "Movement name. Both 'Bench Press' (lift form) "
                    "and 'bench' (normalized form) accepted."
                ),
                "required": True,
            },
        },
        returns_schema={
            "type": "array",
            "description": (
                "List of cue strings. Always non-empty (at least HBP "
                "fallback)."
            ),
        },
    ),
    ToolManifest(
        name="parse_user_workout",
        description=(
            "Use when the user supplies a workout (Murph, Cindy, "
            "Fran, a pasted rep scheme, or AMRAP/EMOM/Tabata "
            "directive). Returns a structured WorkoutCard with the "
            "movements, format, cap, and weight prescriptions. "
            "Apply context (HRV, tier, injuries, equipment) "
            "AFTER parsing — this tool just converts freeform input "
            "to structure, it does not scale for the user's state."
        ),
        args_schema={
            "raw_text": {
                "type": "string",
                "description": "The user's freeform workout description.",
                "required": True,
            },
            "one_rms_kg": {
                "type": "object",
                "description": (
                    "Dict of {lift_name → weight_kg}. Pass the "
                    "result of get_1rms() so benchmark weights "
                    "scale to the user's actual numbers. Optional; "
                    "omit and the function falls back to benchmark "
                    "defaults."
                ),
                "required": False,
            },
        },
        returns_schema={
            "type": "object",
            "description": (
                "WorkoutCard with input_mode=USER_SUPPLIED_WORKOUT. "
                "Unparseable input returns a skeleton card with the "
                "raw text in NOTES for LLM fallback."
            ),
        },
    ),
)


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
    "ENTITY_SOURCE_WORKOUT",
    "ENTITY_WORKOUT", "ENTITY_MOVEMENT", "ENTITY_INJURY",
    "ENTITY_PRVN_CYCLE", "ENTITY_PROGRESSION",
    "ENTITY_WARMUP", "ENTITY_COOLDOWN", "ENTITY_SUBSTITUTION",
    "ENTITY_ONE_REP_MAX", "ENTITY_PREFERENCE", "ENTITY_ROUTE",
    "ALL_ENTITY_TYPES",
    # Source-workout types
    "ParsedMovement", "ParsedSection", "ParsedWorkout",
    "FraserSourceWorkoutBody",
    "SOURCE_WORKOUT_STALE_AFTER_DAYS", "STALE_SOURCE_WORKOUT",
    # Day-6: kcal model + BW scaling + target-band thresholds
    "MovementKcalProfile", "MOVEMENT_KCAL_MODEL",
    "BodyweightScaling", "BW_SCALING_MODEL",
    "KCAL_TARGET_BAND_LOW", "KCAL_TARGET_BAND_HIGH",
    # Charter kinds
    "CHARTER_COMMIT_WORKOUT", "CHARTER_LOG_SESSION",
    "CHARTER_REGISTER_INJURY", "CHARTER_RESOLVE_INJURY",
    "CHARTER_UPDATE_1RM", "CHARTER_INGEST_1RM_BATCH",
    "CHARTER_RECORD_PREFERENCE", "CHARTER_RECORD_ROUTE",
    "CHARTER_PROPOSE_SUBSTITUTE", "CHARTER_ADVANCE_PRVN",
    "CHARTER_ADVANCE_PROGRESSION",
    "ALL_CHARTER_KINDS",
    "CharterRuleSpec", "FRASER_CHARTER_RULE_SPECS",
    # Tool catalog
    "ToolManifest", "TOOL_CATALOG",
    # Enums
    "InputMode", "RecoveryColor", "KobeTier", "WodFormat",
    "Polarity", "Severity", "OneRMSource", "CompletionStatus",
    # Lift & movement vocab
    "LIFTS", "PRESSING_MOVEMENTS", "PULLING_MOVEMENTS",
    "POSTERIOR_CHAIN", "OVERHEAD_MOVEMENTS",
    # Staleness thresholds
    "ONE_RM_WARN_AFTER_DAYS", "ONE_RM_BLOCK_AFTER_DAYS",
    # System-prompt version (stamped on every workout body)
    "FRASER_SYSTEM_PROMPT_VERSION",
    # Substitution vocabulary
    "SUBSTITUTION_CONDITIONS", "_validate_condition",
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
