"""fraser.tools — computational tools (pure transforms, no DB I/O).

Day-2 split: the four read-tools from spec §4 that aren't state-reads
land here instead of `state.py`. The rationale (ADR-005 — five-file
pattern, drafted alongside this file):

    state.py   — DB I/O. Substrate-backed. Charter-gated writes.
    tools.py   — PURE transforms. Static tables. % math. Coefficients.
    handler.py — Orchestration. Reasoner loop. Input-mode routing.

`tools.py` MUST NOT import `state.py` — that would put DB work behind
a "looks pure" name and bite us later. The tools accept their inputs
explicitly (1RMs as a dict, structure as a card) so the handler can
compose state→tool→reasoner without `tools` reaching into the substrate.

Four tools land here:

    compute_target_weight(lift, percentage, one_rm_kg)
    compute_predicted_burn(card)
    lookup_movement_cues(movement)
    parse_user_workout(raw_text, one_rms)

All four are unit-testable in isolation (`tests/test_fraser_tools.py`).
None of them write to the substrate — every persistence path goes
through `state.py`'s Charter-gated writes.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.fraser.protocols import (  # noqa: E402
    Movement, StrengthLift, WorkoutCard, WODBlock, WarmUpBlock,
    StrengthBlock, CoolDownBlock, WorkoutNotes, ContextSnapshot,
    InputMode, WodFormat, normalize_lift_name, normalize_movement,
    MOVEMENT_KCAL_MODEL,
)


# ─────────────────────────── Constants ────────────────────────────────
# Plate granularity for the % math. 2.5 kg = one pair of 1.25 kg plates.
# Below this the math implies a fractional plate that doesn't exist in
# the gym.
PLATE_INCREMENT_KG = 2.5


# Per-minute kcal coefficient table for the predicted-burn math.
# Numbers come from the same MET tables used in spec §2.3 / §5
# (CrossFit Journal + ACSM). They're intentionally conservative —
# the Workout Card surfaces a LOW/HIGH range, the midpoint is what
# we tune against the user's actual log data on Day 5+.
#
# Movements not in this table fall back to MIN_KCAL_PER_MIN — the
# burn math underestimates rather than overestimates, by design.
KCAL_PER_MIN_BY_MOVEMENT: dict[str, tuple[float, float]] = {
    # (low, high) kcal/min — the WOD block emits a band, not a point.
    "thruster":         (14.0, 18.0),
    "burpee":           (14.0, 16.0),
    "burpee_box_jump":  (15.0, 18.0),
    "pull_up":          (8.0,  12.0),
    "chin_up":          (8.0,  12.0),
    "trx_row":          (7.0,  10.0),
    "ring_row":         (7.0,  10.0),
    "box_jump":         (12.0, 15.0),
    "box_step_up":      (8.0,  11.0),
    "kettlebell_swing": (10.0, 13.0),
    "wall_ball":        (11.0, 14.0),
    "row":              (12.0, 15.0),
    "assault_bike":     (16.0, 20.0),
    "deadlift":         (8.0,  10.0),
    "sumo_deadlift":    (8.0,  10.0),
    "back_squat":       (7.0,  9.0),
    "front_squat":      (7.0,  9.0),
    "bench":            (6.0,  8.0),
    "strict_press":     (6.0,  8.0),
    "push_press":       (8.0,  11.0),
    "clean":            (10.0, 13.0),
    "snatch":           (11.0, 14.0),
    "run":              (10.0, 12.0),
    "z2_run":           (9.0,  11.0),
    "jump_rope":        (12.0, 14.0),
    "double_under":     (14.0, 17.0),
    "penguin_jump":     (11.0, 13.0),
    "lateral_hop":      (10.0, 13.0),
    "devil_press":      (14.0, 17.0),
    "dual_db_front_squat": (10.0, 13.0),
    "db_thruster":      (13.0, 16.0),
    "dumbbell_press":   (7.0,  9.0),
    "face_pull":        (3.0,  5.0),
    "cat_cow":          (2.0,  3.0),
    "chin_tuck":        (1.0,  2.0),
}
MIN_KCAL_PER_MIN = (5.0, 7.0)


# Per-rep estimate for rep-bounded movements (helps when the structure
# is `21-15-9` style and we know reps but not minutes). Rough rule of
# thumb: 1 rep at typical CrossFit pace ≈ 3–4 seconds, so 60 sec / 3.5 =
# ~17 reps/min. Movement-specific numbers below override the default.
SECS_PER_REP_BY_MOVEMENT: dict[str, float] = {
    "thruster":   4.0,
    "burpee":     4.5,
    "pull_up":    3.0,
    "wall_ball":  3.5,
    "box_jump":   3.5,
    "kettlebell_swing": 2.5,
    "deadlift":   4.0,
    "back_squat": 4.0,
    "bench":      4.5,
    "clean":      5.0,
    "snatch":     6.0,
    "devil_press": 5.5,
}
DEFAULT_SECS_PER_REP = 3.5


# Movement coaching cues — the four canonical templates from the
# Gemini transcript referenced in spec §5 items 2, 3, 7. Each cue
# is short, prescriptive, and survives copy/paste into the
# Workout Card NOTES block.
HUNCH_CUE       = "Hunch reset — shoulders pinned, lats engaged, ribs down."
NECK_GUARD_CUE  = "Neck Guard — neutral spine, chin tucked, no Valsalva on the up."
CARDIO_CUE         = "Cardio-caution — exhale on the up portion. Never max Valsalva."
ANKLE_CHECK_CUE = "Ankle Check — mid-foot pressure, knee tracks toe, no heel pop."

# Cue assignment by movement category. Each movement gets the cues
# that apply — pressing movements get neck-guard + cardio-caution; squat patterns
# get ankle-check + cardio-caution; pulls get hunch + neck-guard. Free-text per
# the Gemini transcript: "every heavy lift carries an explicit exhale
# cue. Never max Valsalva."
CUE_TABLE: dict[str, tuple[str, ...]] = {
    # Pressing
    "bench":          (NECK_GUARD_CUE, CARDIO_CUE),
    "strict_press":   (NECK_GUARD_CUE, CARDIO_CUE),
    "push_press":     (NECK_GUARD_CUE, CARDIO_CUE),
    "floor_press":    (NECK_GUARD_CUE, CARDIO_CUE),
    "dumbbell_press": (NECK_GUARD_CUE, CARDIO_CUE),
    # Pulling
    "deadlift":       (HUNCH_CUE, CARDIO_CUE),
    "sumo_deadlift":  (HUNCH_CUE, CARDIO_CUE),
    "pull_up":        (HUNCH_CUE,),
    "chin_up":        (HUNCH_CUE,),
    "trx_row":        (HUNCH_CUE,),
    "ring_row":       (HUNCH_CUE,),
    # Squat patterns
    "back_squat":     (ANKLE_CHECK_CUE, CARDIO_CUE),
    "front_squat":    (ANKLE_CHECK_CUE, CARDIO_CUE),
    "thruster":       (ANKLE_CHECK_CUE, NECK_GUARD_CUE, CARDIO_CUE),
    # Olympic
    "clean":          (HUNCH_CUE, ANKLE_CHECK_CUE, CARDIO_CUE),
    "snatch":         (HUNCH_CUE, ANKLE_CHECK_CUE, CARDIO_CUE),
    # Warm-up
    "face_pull":      (HUNCH_CUE,),
    "cat_cow":        (HUNCH_CUE,),
    "chin_tuck":      (NECK_GUARD_CUE,),
}


# ─────────────────────────── Benchmark registry ───────────────────────
# Minimal seed set. Future: load from a YAML fixture so the long tail
# can grow without code edits.
@dataclass
class _BenchmarkSpec:
    name: str
    format: WodFormat
    cap_min: int
    rounds_or_structure: str
    # List of (movement_name, base_reps_or_time, base_load_kg, lift_ref).
    # `lift_ref` is the canonical lift for % math (None if N/A).
    movements: list[tuple[str, str, float | None, str | None]]


BENCHMARKS: dict[str, _BenchmarkSpec] = {
    "murph": _BenchmarkSpec(
        name="Murph", format=WodFormat.FOR_TIME, cap_min=60,
        rounds_or_structure="1 mile run → 100 pull-ups → 200 push-ups → 300 air squats → 1 mile run",
        movements=[
            ("run", "1 mile",     None, None),
            ("pull_up", "100",    None, None),
            ("push_up", "200",    None, None),
            ("air_squat", "300",  None, None),
            ("run", "1 mile",     None, None),
        ],
    ),
    "cindy": _BenchmarkSpec(
        name="Cindy", format=WodFormat.AMRAP, cap_min=20,
        rounds_or_structure="AMRAP 20: 5 pull-ups / 10 push-ups / 15 air squats",
        movements=[
            ("pull_up", "5",     None, None),
            ("push_up", "10",    None, None),
            ("air_squat", "15",  None, None),
        ],
    ),
    "fran": _BenchmarkSpec(
        name="Fran", format=WodFormat.FOR_TIME, cap_min=10,
        rounds_or_structure="21-15-9 thrusters + pull-ups",
        movements=[
            ("thruster", "21-15-9", 43.0, "thruster"),
            ("pull_up",  "21-15-9", None, None),
        ],
    ),
    "helen": _BenchmarkSpec(
        name="Helen", format=WodFormat.FOR_TIME, cap_min=15,
        rounds_or_structure="3 rounds: 400m run / 21 KB swings / 12 pull-ups",
        movements=[
            ("run", "400m",            None, None),
            ("kettlebell_swing", "21", 24.0, None),
            ("pull_up", "12",          None, None),
        ],
    ),
    "grace": _BenchmarkSpec(
        name="Grace", format=WodFormat.FOR_TIME, cap_min=8,
        rounds_or_structure="30 clean and jerks for time",
        movements=[
            ("clean", "30", 61.0, "clean"),
        ],
    ),
    "diane": _BenchmarkSpec(
        name="Diane", format=WodFormat.FOR_TIME, cap_min=10,
        rounds_or_structure="21-15-9 deadlift + handstand push-up",
        movements=[
            ("deadlift", "21-15-9", 150.0, "deadlift"),
            ("handstand_push_up", "21-15-9", None, None),
        ],
    ),
}


# Rep-scheme regex for pasted workouts ("21-15-9 / Thrusters / Pull-ups").
_REP_SCHEME_RE = re.compile(r"\b(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\b")
_RFT_RE        = re.compile(r"\b(\d+)\s*(?:rft|rounds for time)\b", re.I)
_AMRAP_RE      = re.compile(r"\bamrap\s+(\d+)\b", re.I)
_EMOM_RE       = re.compile(r"\bemom\s+(\d+)\b", re.I)


# ─────────────────────────── compute_target_weight ────────────────────
def compute_target_weight(lift: str, percentage: float, one_rm_kg: float,
                         *, plate_increment_kg: float = PLATE_INCREMENT_KG
                         ) -> float:
    """Multiply a 1RM by `percentage` and snap to the plate grid.

    Spec §2.2 / §4: every working set Fraser programs is derived from
    a 1RM. The caller supplies the 1RM (read from state.get_1rms()
    upstream) so this function stays pure — no DB.

    Args:
        lift:               Canonical lift name (will be normalized).
        percentage:         0–150 (above-1RM allowed for overload work).
        one_rm_kg:          The user's tested 1RM in kg.
        plate_increment_kg: Granularity of the snap. Default 2.5 kg.

    Returns:
        The target weight in kg, rounded DOWN to the nearest plate
        increment. Rounding down is the safer default — we never
        program more weight than the math implies.

    Examples:
        >>> compute_target_weight("deadlift", 70, 200.0)
        140.0
        >>> compute_target_weight("Back Squat", 92.5, 130.0)
        120.0
    """
    if one_rm_kg <= 0:
        return 0.0
    if percentage <= 0:
        return 0.0
    raw = one_rm_kg * (percentage / 100.0)
    snapped = (raw // plate_increment_kg) * plate_increment_kg
    return float(snapped)


# ─────────────────────────── compute_predicted_burn ───────────────────
@dataclass
class BurnBreakdown:
    """Per-movement contribution to the predicted burn — the data the
    user sees when they ask 'wouldn't SDHP burn lower than thrusters?'
    (spec §5 item 16, §9 case fraser_015)."""
    movement: str
    minutes_estimated: float
    kcal_low: float
    kcal_high: float


@dataclass
class BurnEstimate:
    total_low: int
    total_high: int
    by_movement: list[BurnBreakdown]


def _coeff_for(movement: str) -> tuple[float, float]:
    """Resolve the kcal/min band for a movement, falling back to the
    conservative default if the movement isn't in the table."""
    return KCAL_PER_MIN_BY_MOVEMENT.get(
        normalize_movement(movement), MIN_KCAL_PER_MIN)


def _parse_reps_or_time(token: str) -> tuple[int, float]:
    """Coerce a `reps_or_time` token to (reps, seconds). The Workout
    Card schema is intentionally loose here ("15", "30s", "5 each leg",
    "AMRAP-2min", "400m", "1:00") so this resolver handles the common
    shapes; the rest fall through with (0, 0) and the caller treats
    as time-bounded with no signal.

    Order matters — `min` / `minute` patterns must precede the bare
    `m` (meters) pattern, or "400m Run" gets parsed as 400 minutes.
    (Day-5 demo surfaced this bug: predicted burn was 5000+ kcal
    for a 60-min workout because every meter became a minute.)
    """
    t = (token or "").strip().lower()
    if not t:
        return 0, 0.0
    # 'Xsec' / 'Xsecs' / 'Xs' — seconds
    m = re.match(r"^(\d+)\s*sec?s?$", t)
    if m:
        return 0, float(m.group(1))
    m = re.match(r"^(\d+)\s*s$", t)
    if m:
        return 0, float(m.group(1))
    # 'X:YY' — minutes:seconds (e.g., '1:00' wall sit)
    m = re.match(r"^(\d+):(\d{2})$", t)
    if m:
        return 0, int(m.group(1)) * 60.0 + int(m.group(2))
    # 'Xmin' / 'Xminute' / 'Xminutes' — explicit minute units only.
    m = re.match(r"^(\d+)\s*(?:min|minute)s?$", t)
    if m:
        return 0, float(m.group(1)) * 60.0
    # 'Xm' (meters) / 'Xft' (feet) / 'Xkm' — distance work. We return
    # (0, 0) here for backward compatibility; the new
    # `_parse_dimensions` helper extracts distance for the
    # MOVEMENT_KCAL_MODEL pass. The "1 mile" form is also handled
    # there (mile → meters conversion).
    m = re.match(r"^(\d+)\s*m$", t)
    if m:
        return 0, 0.0
    m = re.match(r"^(\d+)\s*ft$", t)
    if m:
        return 0, 0.0
    m = re.match(r"^(\d+)\s*km$", t)
    if m:
        return 0, 0.0
    # Bare reps
    m = re.match(r"^(\d+)(?:\s*each|\s*reps?)?$", t)
    if m:
        return int(m.group(1)), 0.0
    # Rep-scheme like "21-15-9" — treat as total reps.
    rep_scheme = _REP_SCHEME_RE.search(t)
    if rep_scheme:
        a, b, c = (int(g) for g in rep_scheme.groups())
        return a + b + c, 0.0
    return 0, 0.0


def _parse_dimensions(token: str) -> tuple[int, float, float]:
    """Extract (reps, seconds, meters) from a `reps_or_time` token.

    Distinct from `_parse_reps_or_time` (which returns reps+seconds)
    because the Day-6 kcal model needs distance as a first-class
    dimension. Returns 0 for any dimension that doesn't apply.

    Handles:
        - "12" / "21-15-9" / "12 reps"   → reps
        - "30s" / "1:00" / "5 min"       → seconds
        - "400m" / "1 mile" / "0.5 km"   → meters
    """
    t = (token or "").strip().lower()
    if not t:
        return 0, 0.0, 0.0
    # Reps + seconds first (delegate to the existing parser).
    reps, secs = _parse_reps_or_time(t)
    # Meters: explicit suffixes — m, km, mile(s), ft.
    meters = 0.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*m$", t)
    if m:
        meters = float(m.group(1))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*km$", t)
    if m:
        meters = float(m.group(1)) * 1000.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*mile?s?$", t)
    if m:
        meters = float(m.group(1)) * 1609.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*ft$", t)
    if m:
        meters = float(m.group(1)) * 0.3048
    return reps, secs, meters


def compute_predicted_burn(card: WorkoutCard | dict) -> BurnEstimate:
    """Per-movement predicted burn for the WOD + warm-up + cool-down
    portions of a Workout Card.

    The math:
        1. For each movement, parse reps_or_time → (reps, seconds).
        2. If seconds > 0, use kcal/min × minutes directly.
        3. If reps > 0, convert reps → minutes via SECS_PER_REP table,
           then kcal/min × minutes.
        4. Sum LOW and HIGH separately. The card surfaces the band so
           the user knows the math has uncertainty baked in.

    Spec §5 item 16 ("calorie math transparency"): when the user
    questions the predicted burn, Fraser surfaces the per-movement
    contributions. `BurnEstimate.by_movement` is what the reasoner
    quotes when asked to explain.
    """
    if isinstance(card, dict):
        card = WorkoutCard.from_dict(card)

    breakdowns: list[BurnBreakdown] = []
    total_low = 0.0
    total_high = 0.0

    def _add_block(mov_list: Iterable[Movement]) -> None:
        nonlocal total_low, total_high
        for m in mov_list:
            mov_name = normalize_movement(m.name)
            reps, secs, meters = _parse_dimensions(m.reps_or_time)

            # Day-6 kcal model — first preference. Sum per-dimension
            # contributions whenever the movement has a profile.
            profile = MOVEMENT_KCAL_MODEL.get(mov_name)
            kcal_from_model = 0.0
            if profile is not None:
                if reps > 0 and profile.per_rep_kcal > 0:
                    kcal_from_model += reps * profile.per_rep_kcal
                if secs > 0 and profile.per_second_kcal > 0:
                    kcal_from_model += secs * profile.per_second_kcal
                if meters > 0 and profile.per_meter_kcal > 0:
                    kcal_from_model += meters * profile.per_meter_kcal

            if kcal_from_model > 0:
                # ±15% band around the model's point estimate. The
                # model is a point estimate; surface a band so the
                # user sees the uncertainty without us having to
                # carry separate low/high per movement.
                lo_kcal = kcal_from_model * 0.85
                hi_kcal = kcal_from_model * 1.15
                # minutes_estimated kept for backward-compat with the
                # breakdown shape — derive from secs if available.
                minutes_eq = (secs / 60.0) if secs > 0 else 0.0
                breakdowns.append(BurnBreakdown(
                    movement=m.name, minutes_estimated=round(minutes_eq, 2),
                    kcal_low=round(lo_kcal, 1), kcal_high=round(hi_kcal, 1)))
                total_low += lo_kcal
                total_high += hi_kcal
                continue

            # Fallback: per-minute coefficient path (Day-2 behavior).
            if secs <= 0 and reps > 0:
                per_rep = SECS_PER_REP_BY_MOVEMENT.get(
                    mov_name, DEFAULT_SECS_PER_REP)
                secs = reps * per_rep
            minutes = secs / 60.0
            if minutes <= 0:
                continue
            lo, hi = _coeff_for(m.name)
            lo_kcal = lo * minutes
            hi_kcal = hi * minutes
            breakdowns.append(BurnBreakdown(
                movement=m.name, minutes_estimated=round(minutes, 2),
                kcal_low=round(lo_kcal, 1), kcal_high=round(hi_kcal, 1)))
            total_low += lo_kcal
            total_high += hi_kcal

    _add_block(card.warm_up.movements)
    # Strength lifts have implicit timing — ~3 min per working set.
    for lift in card.strength.lifts:
        secs = (lift.working_sets * 3 * 60.0)
        minutes = secs / 60.0
        lo, hi = _coeff_for(lift.name)
        lo_kcal = lo * minutes
        hi_kcal = hi * minutes
        breakdowns.append(BurnBreakdown(
            movement=lift.name, minutes_estimated=round(minutes, 2),
            kcal_low=round(lo_kcal, 1), kcal_high=round(hi_kcal, 1)))
        total_low += lo_kcal
        total_high += hi_kcal
    _add_block(card.wod.movements)
    _add_block(card.cool_down.movements)

    return BurnEstimate(
        total_low=int(round(total_low)),
        total_high=int(round(total_high)),
        by_movement=breakdowns,
    )


# ─────────────────────────── lookup_movement_cues ─────────────────────
def lookup_movement_cues(movement: str) -> list[str]:
    """Return the coaching cues for a movement. Spec §5 items 2, 3, 7:
    every session prepends a Hunch reset, pressing/cleans get neck-
    guard cues, heavy lifts carry the cardio-caution rule.

    Unknown movements get the cardio-caution rule by default — it's the most
    broadly applicable cue and never harmful to surface.

    Two normalizers tried, in order:
        1. normalize_movement (generic — handles "back-squat" → "back_squat")
        2. normalize_lift_name (handles "Bench Press" → "bench" via
           the lift-aliases table)

    This dual lookup lets the cue table key by the canonical short
    lift names ('bench', 'strict_press') without forcing the caller
    to pre-normalize.
    """
    cues = CUE_TABLE.get(normalize_movement(movement))
    if cues is None:
        cues = CUE_TABLE.get(normalize_lift_name(movement))
    if cues:
        return list(cues)
    return [CARDIO_CUE]


# ─────────────────────────── parse_user_workout ───────────────────────
def parse_user_workout(raw_text: str,
                      *, one_rms_kg: dict[str, float] | None = None,
                      ) -> WorkoutCard:
    """Freeform user input → structured Workout Card schema.

    Strategy (Day-2 baseline; Day-3 LLM fallback for unparseable input):

        1. If the text mentions a known benchmark name, instantiate the
           registered structure. Apply 'at N%' scaling if present.
        2. Else if a rep scheme '21-15-9' / 'X RFT' / 'AMRAP X' / 'EMOM X'
           is present, extract format + cap + structure.
        3. Else return a skeleton card with the raw text in NOTES.

    `one_rms_kg` is the dict from `state.get_1rms()`; passed through
    so benchmark weights scale to the user's actual numbers when the
    benchmark spec carries a `lift_ref`.

    Returns a WorkoutCard with `input_mode=USER_SUPPLIED_WORKOUT` (the
    handler's classifier puts us here; we honor that contract).
    """
    one_rms_kg = one_rms_kg or {}
    raw_text = raw_text or ""
    text = raw_text.strip()
    low = text.lower()

    card = WorkoutCard(
        date_iso=datetime.now().strftime("%Y-%m-%d"),
        input_mode=InputMode.USER_SUPPLIED_WORKOUT,
        notes=WorkoutNotes(
            why_this_design=f"Parsed from user-supplied input: {text!r}"
        ),
    )

    # Strategy 1 — benchmark match.
    scale_pct: float | None = None
    pct_match = re.search(r"\bat\s+(\d{1,3})\s*%", low)
    if pct_match:
        scale_pct = float(pct_match.group(1)) / 100.0

    matched_benchmark: _BenchmarkSpec | None = None
    for name, spec in BENCHMARKS.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            matched_benchmark = spec
            break

    if matched_benchmark:
        card.wod = _benchmark_to_wod_block(
            matched_benchmark, scale_pct=scale_pct, one_rms_kg=one_rms_kg)
        card.notes.deltas_from_request = []
        if scale_pct is not None:
            card.notes.deltas_from_request.append(
                f"scaled to {int(scale_pct * 100)}% of benchmark weights/reps")
        card.notes.why_this_design = (
            f"{matched_benchmark.name}: {matched_benchmark.rounds_or_structure}"
        )
        return card

    # Strategy 2 — pasted rep scheme.
    rep_scheme = _REP_SCHEME_RE.search(text)
    rft_match  = _RFT_RE.search(text)
    amrap_match = _AMRAP_RE.search(text)
    emom_match = _EMOM_RE.search(text)

    movements_in_text = _extract_movement_names(text)

    if rep_scheme:
        a, b, c = (int(g) for g in rep_scheme.groups())
        rs = f"{a}-{b}-{c}"
        movs = [Movement(name=normalize_movement(m), reps_or_time=rs)
                for m in movements_in_text]
        card.wod = WODBlock(
            format=WodFormat.FOR_TIME, cap_min=20,
            rounds_or_structure=rs, movements=movs,
        )
        return card

    if rft_match:
        rounds = int(rft_match.group(1))
        movs = [Movement(name=normalize_movement(m), reps_or_time="")
                for m in movements_in_text]
        card.wod = WODBlock(
            format=WodFormat.FOR_TIME, cap_min=20,
            rounds_or_structure=f"{rounds} RFT", movements=movs,
        )
        return card

    if amrap_match:
        cap = int(amrap_match.group(1))
        movs = [Movement(name=normalize_movement(m), reps_or_time="")
                for m in movements_in_text]
        card.wod = WODBlock(
            format=WodFormat.AMRAP, cap_min=cap,
            rounds_or_structure=f"AMRAP {cap}", movements=movs,
        )
        return card

    if emom_match:
        cap = int(emom_match.group(1))
        movs = [Movement(name=normalize_movement(m), reps_or_time="")
                for m in movements_in_text]
        card.wod = WODBlock(
            format=WodFormat.EMOM, cap_min=cap,
            rounds_or_structure=f"EMOM {cap}", movements=movs,
        )
        return card

    # Strategy 3 — fallback skeleton.
    card.notes.why_this_design = (
        f"Unparseable user-supplied input. Day-3 LLM fallback will "
        f"handle this once wired. Raw: {text!r}"
    )
    return card


def _benchmark_to_wod_block(spec: _BenchmarkSpec,
                           *, scale_pct: float | None,
                           one_rms_kg: dict[str, float]) -> WODBlock:
    """Instantiate a benchmark spec into a WODBlock, applying optional
    rep- and weight-scaling."""
    movs: list[Movement] = []
    for (name, reps_or_time, base_load, lift_ref) in spec.movements:
        load_kg: float | None = base_load
        # If the movement references a lift, use the user's 1RM with
        # the typical benchmark % rather than the raw base_load.
        if lift_ref and lift_ref in one_rms_kg:
            # Benchmarks default to ~30-50% of 1RM for thrusters etc.
            # If scale_pct is set (e.g., "Murph at 70%"), use that;
            # else use the benchmark's base_load as the floor.
            if scale_pct is not None:
                load_kg = compute_target_weight(
                    lift_ref, scale_pct * 100.0, one_rms_kg[lift_ref])
        elif scale_pct is not None and base_load is not None:
            # Pure scaling against the benchmark's published load.
            load_kg = round(base_load * scale_pct, 1)

        # Scale reps if the token is purely numeric.
        rt = reps_or_time
        if scale_pct is not None and rt.isdigit():
            rt = str(max(1, int(int(rt) * scale_pct)))

        movs.append(Movement(
            name=normalize_movement(name),
            reps_or_time=rt, load_kg=load_kg))

    return WODBlock(
        format=spec.format, cap_min=spec.cap_min,
        rounds_or_structure=spec.rounds_or_structure,
        movements=movs,
    )


# Movement-name extraction heuristics for pasted text. A real LLM
# classifier would crush this, but the regex baseline catches the
# common cases — slash-separated lists, line-separated lists, and
# the obvious movement vocabulary.
_KNOWN_MOVEMENTS = sorted(KCAL_PER_MIN_BY_MOVEMENT.keys() | {
    "push_up", "air_squat", "handstand_push_up", "muscle_up",
    "toes_to_bar", "rope_climb",
}, key=len, reverse=True)


def _extract_movement_names(text: str) -> list[str]:
    """Pull out movement names from freeform text. Used by parse_user_workout
    when the format is detected but the movement list needs assembly."""
    if not text:
        return []
    norm = re.sub(r"[\s/\-]+", " ", text.lower()).replace("'s", "")
    norm = re.sub(r"[^\w\s]", " ", norm)
    found: list[str] = []
    seen: set[str] = set()
    for mov in _KNOWN_MOVEMENTS:
        # Allow space OR underscore in the source text. Allow optional
        # trailing 's' so 'thrusters' / 'pull-ups' / 'burpees' match
        # their singular canonical names in _KNOWN_MOVEMENTS.
        token = mov.replace("_", " ")
        if re.search(rf"\b{re.escape(token)}s?\b", norm) and mov not in seen:
            found.append(mov)
            seen.add(mov)
    return found


__all__ = [
    # Constants
    "PLATE_INCREMENT_KG",
    "KCAL_PER_MIN_BY_MOVEMENT", "MIN_KCAL_PER_MIN",
    "SECS_PER_REP_BY_MOVEMENT", "DEFAULT_SECS_PER_REP",
    "HUNCH_CUE", "NECK_GUARD_CUE", "CARDIO_CUE", "ANKLE_CHECK_CUE",
    "CUE_TABLE",
    "BENCHMARKS",
    # Dataclasses
    "BurnBreakdown", "BurnEstimate",
    # Tools
    "compute_target_weight",
    "compute_predicted_burn",
    "lookup_movement_cues",
    "parse_user_workout",
]
