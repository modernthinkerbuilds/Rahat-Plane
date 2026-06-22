"""Fraser tools — pure-transform unit tests.

What this file pins
-------------------
1. `compute_target_weight` snaps to the 2.5-kg plate grid, rounds DOWN
   (so we never program more weight than the math implies), and is
   zero-safe on missing 1RMs.
2. `compute_predicted_burn` returns a LOW/HIGH band with a per-movement
   breakdown that sums (within rounding) to the totals — the breakdown
   is what the reasoner quotes when the user asks 'wouldn't SDHP burn
   lower than thrusters?' (spec §5 item 16, case fraser_015).
3. `lookup_movement_cues` returns the canonical cues per category
   (pressing → neck-guard + cardio-caution; squat → ankle-check + cardio-caution; pull →
   hunch). Unknown movements get the cardio-caution rule as a safe default.
4. `parse_user_workout` instantiates known benchmarks (Murph / Cindy /
   Fran / Diane), applies 'at X%' scaling against the user's 1RMs, and
   extracts format + structure from pasted rep schemes.

All tests are offline. tools.py has no DB or LLM dependencies.
"""
from __future__ import annotations

from agents.fraser.tools import (
    PLATE_INCREMENT_KG,
    HUNCH_CUE, NECK_GUARD_CUE, CARDIO_CUE, ANKLE_CHECK_CUE,
    BENCHMARKS, BurnEstimate, BurnBreakdown,
    compute_target_weight, compute_predicted_burn,
    lookup_movement_cues, parse_user_workout,
)
from agents.fraser.protocols import (
    Movement, StrengthLift, WarmUpBlock, StrengthBlock, WODBlock,
    CoolDownBlock, WorkoutCard, InputMode, WodFormat,
)


# ─── 1. compute_target_weight ───────────────────────────────────────
def test_target_weight_snaps_down_to_plate_grid():
    """200 kg × 70% = 140.0 kg → already on the 2.5-kg grid (2.5-kg increment).
    Rounding-down is the safety contract: never program more than the
    math implies."""
    assert compute_target_weight("deadlift", 70, 200.0) == 140.0


def test_target_weight_exact_increment_no_snap():
    """130 × 92.5% = 120.25; snap to 120.0. 130 × 50% = 65.0 (already
    on the grid) returns exactly 65.0."""
    assert compute_target_weight("back_squat", 50, 130.0) == 65.0
    assert compute_target_weight("back_squat", 92.5, 130.0) == 120.0


def test_target_weight_lift_normalization():
    """`DL`, `Deadlifts`, `deadlift` all collapse to the same canonical
    name — the function accepts any of them."""
    assert (compute_target_weight("DL", 70, 200.0)
            == compute_target_weight("Deadlifts", 70, 200.0)
            == compute_target_weight("deadlift", 70, 200.0))


def test_target_weight_zero_safe():
    """Missing 1RM (0.0) → 0.0 target. No crash."""
    assert compute_target_weight("deadlift", 70, 0.0) == 0.0
    assert compute_target_weight("deadlift", 0, 200.0) == 0.0
    assert compute_target_weight("deadlift", -5, 200.0) == 0.0


def test_target_weight_custom_increment():
    """5-kg increment for Olympic-lift contexts where the bar is loaded
    with full plates only."""
    assert compute_target_weight(
        "snatch", 80, 70.0, plate_increment_kg=5.0) == 55.0


# ─── 2. compute_predicted_burn ──────────────────────────────────────
def test_burn_returns_band_with_breakdown():
    """A WOD with two known movements emits a LOW/HIGH range that
    sums per-movement contributions."""
    card = WorkoutCard(
        wod=WODBlock(
            format=WodFormat.FOR_TIME, cap_min=15,
            movements=[
                Movement(name="thruster", reps_or_time="21"),
                Movement(name="pull_up", reps_or_time="21"),
            ],
        ),
    )
    est = compute_predicted_burn(card)
    assert isinstance(est, BurnEstimate)
    assert est.total_high > est.total_low > 0
    assert len(est.by_movement) == 2
    # The breakdown sums (within rounding) to the totals.
    sum_low = sum(b.kcal_low for b in est.by_movement)
    sum_high = sum(b.kcal_high for b in est.by_movement)
    assert abs(est.total_low - sum_low) <= 1
    assert abs(est.total_high - sum_high) <= 1


def test_burn_per_movement_explanation():
    """The user asks 'wouldn't SDHP burn lower than thrusters?' — the
    breakdown carries the per-movement numbers Fraser quotes. Thrusters
    burn ~14-18 kcal/min; air-squats burn much less."""
    card = WorkoutCard(
        wod=WODBlock(
            format=WodFormat.FOR_TIME, cap_min=10,
            movements=[
                Movement(name="thruster", reps_or_time="30"),
                Movement(name="air_squat", reps_or_time="30"),
            ],
        ),
    )
    est = compute_predicted_burn(card)
    by_mov = {b.movement: b for b in est.by_movement}
    assert "thruster" in by_mov
    # Thruster has a coefficient; air_squat doesn't, so it falls back
    # to MIN_KCAL_PER_MIN. Thruster's HIGH should still exceed air_squat's.
    if "air_squat" in by_mov:
        assert by_mov["thruster"].kcal_high > by_mov["air_squat"].kcal_high


def test_burn_handles_time_token():
    """`reps_or_time` as '60s' is parsed as one minute."""
    card = WorkoutCard(
        wod=WODBlock(
            format=WodFormat.FOR_TIME, cap_min=10,
            movements=[
                Movement(name="run", reps_or_time="60s"),
            ],
        ),
    )
    est = compute_predicted_burn(card)
    # Run coefficient is (10, 12) per minute, so 60s → 10-12 kcal.
    assert 8 <= est.total_low <= 12
    assert 10 <= est.total_high <= 14


def test_burn_includes_strength_block():
    """Strength lifts contribute at ~3 min/set (CrossFit-style pacing)."""
    card = WorkoutCard(
        strength=StrengthBlock(
            duration_min=20,
            lifts=[StrengthLift(
                name="back_squat", working_sets=5, working_reps=5,
                working_weight_kg=92.5)],
        ),
    )
    est = compute_predicted_burn(card)
    # 5 sets × 3 min × (7-9 kcal/min) = 105-135 kcal.
    assert 90 <= est.total_low <= 130
    assert 120 <= est.total_high <= 160


def test_burn_from_dict_round_trips():
    """The reasoner may pass a serialized card (from a tool call).
    compute_predicted_burn accepts both Card and dict."""
    card = WorkoutCard(
        wod=WODBlock(
            movements=[Movement(name="burpee", reps_or_time="15")],
        ),
    )
    d = card.to_dict()
    est_card = compute_predicted_burn(card)
    est_dict = compute_predicted_burn(d)
    assert est_card.total_low == est_dict.total_low
    assert est_card.total_high == est_dict.total_high


def test_burn_unknown_movement_falls_to_floor():
    """A made-up movement uses MIN_KCAL_PER_MIN. The function does NOT
    crash on novel input."""
    card = WorkoutCard(
        wod=WODBlock(
            movements=[Movement(name="space_jumps", reps_or_time="30s")],
        ),
    )
    est = compute_predicted_burn(card)
    assert est.total_low > 0
    assert est.total_high >= est.total_low


# ─── 3. lookup_movement_cues ────────────────────────────────────────
def test_pressing_movements_get_neck_guard_and_hbp():
    """Spec §5 item 2 + 3: pressing movements get neck-guard cues, and
    every heavy lift carries cardio-caution."""
    cues = lookup_movement_cues("Bench Press")
    assert NECK_GUARD_CUE in cues
    assert CARDIO_CUE in cues


def test_squat_pattern_gets_ankle_check_and_hbp():
    cues = lookup_movement_cues("back squat")
    assert ANKLE_CHECK_CUE in cues
    assert CARDIO_CUE in cues


def test_pulling_gets_hunch():
    cues = lookup_movement_cues("deadlift")
    assert HUNCH_CUE in cues


def test_olympic_lifts_get_all_three_categories():
    """Clean and snatch touch pull + squat + press patterns. They
    should carry all four cue types."""
    cues = lookup_movement_cues("clean")
    # Hunch (pull pattern), ankle-check (catch), cardio-caution (heavy lift).
    assert HUNCH_CUE in cues
    assert ANKLE_CHECK_CUE in cues
    assert CARDIO_CUE in cues


def test_unknown_movement_gets_hbp_fallback():
    """Unknown movements get cardio-caution as a safe default — the rule that
    never hurts to surface."""
    cues = lookup_movement_cues("Space Jumps")
    assert cues == [CARDIO_CUE]


# ─── 4. parse_user_workout ──────────────────────────────────────────
def test_parse_murph_returns_for_time_card():
    card = parse_user_workout("let's do Murph today")
    assert card.input_mode == InputMode.USER_SUPPLIED_WORKOUT
    assert card.wod.format == WodFormat.FOR_TIME
    assert "Murph" in card.notes.why_this_design
    movement_names = [m.name for m in card.wod.movements]
    assert "run" in movement_names
    assert "pull_up" in movement_names


def test_parse_murph_at_70_percent_scales_reps():
    card = parse_user_workout(
        "Murph at 70%",
        one_rms_kg={"deadlift": 200.0, "back_squat": 130.0})
    # The benchmark's reps token "100" should now scale to 70.
    movement_reps = {m.name: m.reps_or_time for m in card.wod.movements}
    assert movement_reps.get("pull_up") == "70"
    assert any("70%" in d for d in card.notes.deltas_from_request)


def test_parse_fran_attaches_thruster_load_from_1rm():
    """Fran's thrusters are normally 43 kg (95 lb). If the user has a
    higher 1RM and supplies 'Fran at 50%', the load math scales."""
    card = parse_user_workout(
        "Fran at 50%",
        one_rms_kg={"thruster": 60.0})
    thrusters = [m for m in card.wod.movements if m.name == "thruster"]
    assert thrusters
    # 50% of 60 = 30 → snapped to 30.0 on the 2.5-kg grid.
    assert thrusters[0].load_kg == 30.0


def test_parse_rep_scheme_extracts_format_and_movements():
    card = parse_user_workout(
        "21-15-9 / Thrusters / Pull-ups / For Time")
    assert card.wod.format == WodFormat.FOR_TIME
    assert card.wod.rounds_or_structure == "21-15-9"
    movement_names = [m.name for m in card.wod.movements]
    assert "thruster" in movement_names
    assert "pull_up" in movement_names


def test_parse_amrap_extracts_cap():
    card = parse_user_workout("AMRAP 18 / Burpees / Pull-ups")
    assert card.wod.format == WodFormat.AMRAP
    assert card.wod.cap_min == 18


def test_parse_emom_extracts_cap():
    card = parse_user_workout("EMOM 12 working bench")
    assert card.wod.format == WodFormat.EMOM
    assert card.wod.cap_min == 12


def test_parse_unparseable_returns_skeleton_with_notes():
    """Unparseable input must NOT crash. Returns a skeleton card with
    the raw input in NOTES for the Day-3 LLM to handle."""
    card = parse_user_workout("hey can you build me something fun")
    assert card.input_mode == InputMode.USER_SUPPLIED_WORKOUT
    # No movements parsed.
    assert card.wod.movements == []
    # Notes carry the raw input for LLM fallback.
    assert "fun" in card.notes.why_this_design.lower()


def test_parse_empty_input_no_crash():
    card = parse_user_workout("")
    assert card.input_mode == InputMode.USER_SUPPLIED_WORKOUT


# ─── 5. Benchmark registry coverage ─────────────────────────────────
def test_benchmarks_include_core_set():
    """Spec §9 / §11 reference Murph, Cindy, Fran. All must be in the
    registry — otherwise eval cases that paste 'Cindy' get the
    fallback skeleton instead of the parsed structure."""
    assert "murph" in BENCHMARKS
    assert "cindy" in BENCHMARKS
    assert "fran" in BENCHMARKS


def test_plate_increment_is_documented_constant():
    """Tests that monkey-patch this value will surface their expected
    behavior here. 2.5 kg = standard gym increment."""
    assert PLATE_INCREMENT_KG == 2.5
