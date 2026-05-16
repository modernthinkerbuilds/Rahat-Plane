"""Fraser protocols — type contract + round-trip pinning.

What this file pins
-------------------
1. Each of the 11 entity body dataclasses round-trips through
   to_payload() / from_payload() with no data loss.
2. The Workout Card round-trips through to_dict() / from_dict()
   including all nested blocks (warm-up, strength, WOD, cool-down,
   notes, context snapshot).
3. Enum values stay as their .value strings in JSON-serializable
   payloads (the substrate stores TEXT — we never want a payload
   that holds a raw Python Enum reference).
4. Input-mode classification (handler.classify_input_mode) routes
   the canonical benchmark / format / default test cases correctly.
5. Lift normalization collapses common variants (`Deadlifts`,
   `DL`, `back-squat`, etc.) to canonical names.

Every test is offline. No GEMINI_API_KEY, no Telegram, no DB.
"""
from __future__ import annotations

from agents.fraser.protocols import (
    AGENT, ALL_ENTITY_TYPES, ALL_CHARTER_KINDS,
    InputMode, WodFormat, Polarity, Severity,
    OneRMSource, CompletionStatus,
    WorkoutCard, WorkoutBody, OneRepMaxBody, InjuryBody,
    PreferenceBody, RouteBody, PRVNPositionBody,
    ChestProgressionBody, SubstitutionRuleBody,
    MovementInstanceBody, WarmUpBody, CoolDownBody,
    Movement, StrengthLift,
    WarmUpBlock, StrengthBlock, WODBlock, CoolDownBlock,
    ContextSnapshot, WorkoutNotes,
    normalize_lift_name, normalize_movement,
    is_pressing, is_pulling, is_overhead, loads_posterior_chain,
)
from agents.fraser.handler import (
    classify_input_mode, extract_requested_format,
)


# ─── 1. Identity invariants ─────────────────────────────────────────
def test_agent_namespace_is_fraser():
    """The storage-convention test reads AGENT to confirm Fraser writes
    aren't masquerading under another agent's namespace."""
    assert AGENT == "fraser"


def test_twelve_entity_types_enumerated():
    """Spec §3 (v2 — Day-5 adapter pivot) lists 12 entity types.
    `fraser_source_workout` joined the set when Fraser became an
    adaptation engine. ALL_ENTITY_TYPES is the canonical tuple —
    drift here breaks downstream tools that enumerate per-type
    entities (eval suite, /memory debug)."""
    assert len(ALL_ENTITY_TYPES) == 12
    # All start with the agent prefix — defensive against typo drift.
    assert all(t.startswith("fraser_") for t in ALL_ENTITY_TYPES)
    # The new entity must be present.
    assert "fraser_source_workout" in ALL_ENTITY_TYPES


def test_eleven_charter_kinds_enumerated():
    """Spec §2.2 enumerates 11 write tools, each with its own charter
    rule kind. ALL_CHARTER_KINDS pins the count."""
    assert len(ALL_CHARTER_KINDS) == 11
    assert all(k.startswith("fraser.") for k in ALL_CHARTER_KINDS)


# ─── 2. Lift + movement normalization ───────────────────────────────
def test_normalize_lift_collapses_aliases():
    assert normalize_lift_name("Deadlifts") == "deadlift"
    assert normalize_lift_name("DL") == "deadlift"
    assert normalize_lift_name("back-squat") == "back_squat"
    assert normalize_lift_name("BS") == "back_squat"
    assert normalize_lift_name("Bench Press") == "bench"
    assert normalize_lift_name("OHP") == "strict_press"
    assert normalize_lift_name("Push Press") == "push_press"


def test_normalize_movement_handles_plurals_and_spacing():
    assert normalize_movement("Burpees") == "burpee"
    assert normalize_movement("box jump") == "box_jump"
    assert normalize_movement("DEADLIFT") == "deadlift"
    # 'ss' tail (e.g., "press") must NOT be stripped.
    assert normalize_movement("press") == "press"


def test_movement_category_predicates():
    assert is_pressing("bench") is True
    assert is_pressing("Deadlift") is False
    assert is_pulling("deadlift") is True
    assert is_overhead("snatch") is True
    assert loads_posterior_chain("Deadlift") is True
    assert loads_posterior_chain("bench") is False


# ─── 3. Workout Card round-trip ─────────────────────────────────────
def test_workout_card_default_roundtrip():
    """A default-constructed card must round-trip cleanly. The eval
    suite's stub-output assertions depend on this."""
    card = WorkoutCard()
    d = card.to_dict()
    # Enum values must be strings, not Python references.
    assert d["input_mode"] == InputMode.DEFAULT.value
    assert d["wod"]["format"] == WodFormat.FOR_TIME.value
    rt = WorkoutCard.from_dict(d)
    assert rt.input_mode == InputMode.DEFAULT
    assert rt.wod.format == WodFormat.FOR_TIME


def test_workout_card_full_roundtrip():
    """Build a populated card and round-trip. Every nested dataclass
    must survive the JSON-shaped intermediate."""
    card = WorkoutCard(
        date_iso="2026-05-14",
        time_of_day="morning",
        target_kcal=620,
        target_minutes=60,
        context=ContextSnapshot(
            hrv=48, sleep_hours=7.5, kobe_tier="hammer",
            recovery_color="green", active_injuries=["left_glute"],
            equipment=["barbell", "dumbbells"], time_of_day="morning",
        ),
        warm_up=WarmUpBlock(
            duration_min=8,
            movements=[Movement(name="face_pull", reps_or_time="15")],
            postural_cues=["Hunch reset"],
        ),
        strength=StrengthBlock(
            duration_min=20,
            lifts=[StrengthLift(
                name="back_squat", working_sets=5, working_reps=5,
                working_weight_kg=92.5, percent_1rm=70.0,
                ramp_up_kg=[20, 40, 60, 80],
            )],
        ),
        wod=WODBlock(
            format=WodFormat.AMRAP, cap_min=18,
            movements=[
                Movement(name="thruster", reps_or_time="12", load_kg=42.5),
                Movement(name="pull_up", reps_or_time="9"),
            ],
            rounds_or_structure="AMRAP 18",
            substitutions_applied=["no rope → penguin jumps"],
            predicted_burn_kcal_low=480, predicted_burn_kcal_high=560,
        ),
        cool_down=CoolDownBlock(
            duration_min=5,
            breathing_protocol="legs-up-the-wall 5min",
        ),
        notes=WorkoutNotes(
            why_this_design="HRV green, hammer tier, no posterior fatigue.",
            deltas_from_request=[],
            prvn_position="W4D2",
            chest_progression_position="W6, target 8 reps",
        ),
        input_mode=InputMode.USER_REQUESTED_FORMAT,
    )
    d = card.to_dict()
    rt = WorkoutCard.from_dict(d)
    assert rt.date_iso == "2026-05-14"
    assert rt.context.hrv == 48
    assert rt.context.kobe_tier == "hammer"
    assert rt.context.active_injuries == ["left_glute"]
    assert len(rt.strength.lifts) == 1
    assert rt.strength.lifts[0].working_weight_kg == 92.5
    assert rt.wod.format == WodFormat.AMRAP
    assert rt.wod.predicted_burn_kcal_high == 560
    assert rt.input_mode == InputMode.USER_REQUESTED_FORMAT
    assert rt.notes.prvn_position == "W4D2"


# ─── 4. Entity body round-trips ─────────────────────────────────────
def test_injury_body_roundtrip():
    body = InjuryBody(
        body_part="left_glute", severity=Severity.MODERATE,
        onset_iso="2026-05-14",
        mute_movements=["Back Squats", "box step-overs"],
        eta_iso="2026-05-21", rationale="catch on warm-up set",
    )
    rt = InjuryBody.from_payload(body.to_payload())
    assert rt.body_part == "left_glute"
    assert rt.severity == Severity.MODERATE
    # Mute movements must come back normalized — hyphens collapse
    # to underscores AND trailing plurals are stripped.
    assert rt.mute_movements == ["back_squat", "box_step_over"]
    assert rt.eta_iso == "2026-05-21"


def test_one_rep_max_body_roundtrip():
    body = OneRepMaxBody(
        lift="Deadlifts", weight_kg=155.0, tested_on_iso="2026-05-10",
        source=OneRMSource.TESTED, notes="with belt",
    )
    rt = OneRepMaxBody.from_payload(body.to_payload())
    assert rt.lift == "deadlift"   # normalized through to_payload
    assert rt.weight_kg == 155.0
    assert rt.source == OneRMSource.TESTED


def test_preference_body_roundtrip_movement():
    body = PreferenceBody(
        target="Devil's Press", target_kind="movement",
        polarity=Polarity.DISLIKE, reason="hate the cycle",
        declared_on_iso="2026-05-14",
    )
    rt = PreferenceBody.from_payload(body.to_payload())
    assert rt.target == "devil's_press"
    assert rt.polarity == Polarity.DISLIKE


def test_preference_body_roundtrip_format():
    """Format-kind preferences (e.g., 'EMOM') must not be passed through
    normalize_movement — only movement-kind targets get normalized."""
    body = PreferenceBody(
        target="EMOM", target_kind="format",
        polarity=Polarity.DISLIKE, reason="cooked",
    )
    payload = body.to_payload()
    # Format target is preserved verbatim.
    assert payload["target"] == "EMOM"


def test_route_body_roundtrip_with_correction():
    body = RouteBody(
        name="local loop", distance_km=7.8, terrain="road",
        gear_notes="single technical tee",
        corrected_from_distance_km=10.0,
        declared_on_iso="2026-05-14",
    )
    rt = RouteBody.from_payload(body.to_payload())
    assert rt.distance_km == 7.8
    assert rt.corrected_from_distance_km == 10.0


def test_prvn_position_body_roundtrip():
    body = PRVNPositionBody(
        week=4, day=2, phase="intensify",
        last_completed_iso="2026-05-13",
    )
    rt = PRVNPositionBody.from_payload(body.to_payload())
    assert rt.week == 4
    assert rt.phase == "intensify"


def test_chest_progression_body_roundtrip():
    body = ChestProgressionBody(
        week=6, day=3, target_reps=8,
        plateau_status="stalled", cycle_start_iso="2026-04-02",
    )
    rt = ChestProgressionBody.from_payload(body.to_payload())
    assert rt.target_reps == 8
    assert rt.plateau_status == "stalled"


def test_substitution_rule_body_roundtrip():
    body = SubstitutionRuleBody(
        movement="Wall Ball", condition="equipment_missing",
        replacements=["DB Thruster", "Burpee Box Jump"],
        reason_template="no wall ball → {replacement}",
    )
    rt = SubstitutionRuleBody.from_payload(body.to_payload())
    assert rt.movement == "wall_ball"
    assert rt.condition == "equipment_missing"
    assert rt.replacements == ["db_thruster", "burpee_box_jump"]


def test_substitution_rule_rejects_unknown_condition():
    """Write-time validation: an unknown condition string fails LOUDLY
    rather than landing as an unfindable orphan in the substrate. This
    is the same instinct as `dislikes._normalize_movement` failing on
    empty input."""
    import pytest as _pytest
    body = SubstitutionRuleBody(
        movement="thruster", condition="no_wall_ball",  # legacy/invalid
        replacements=["air_squat"],
    )
    with _pytest.raises(ValueError, match="unknown substitution condition"):
        body.to_payload()


def test_substitution_conditions_vocab_is_alphabetical():
    """The protocols comment commits to alphabetical order — that
    keeps PR diffs minimal when new conditions land."""
    from agents.fraser.protocols import SUBSTITUTION_CONDITIONS
    assert list(SUBSTITUTION_CONDITIONS) == sorted(SUBSTITUTION_CONDITIONS)


def test_substitution_conditions_includes_canonical_seven():
    """Pin the vocabulary so a silent rename surfaces immediately."""
    from agents.fraser.protocols import SUBSTITUTION_CONDITIONS
    for c in ("equipment_missing", "mobility_limit", "user_dislike",
              "rx_unavailable", "recovery_gate", "format_incompatible",
              "time_constrained"):
        assert c in SUBSTITUTION_CONDITIONS


def test_warmup_body_roundtrip():
    body = WarmUpBody(
        name="hunch_reset", duration_min=8,
        movements=[Movement(name="face_pull", reps_or_time="15")],
        postural_targets=["thoracic", "neck"],
    )
    rt = WarmUpBody.from_payload(body.to_payload())
    assert rt.name == "hunch_reset"
    assert len(rt.movements) == 1
    assert rt.movements[0].name == "face_pull"


def test_cooldown_body_roundtrip():
    body = CoolDownBody(
        name="evening_restorative", duration_min=10,
        movements=[Movement(name="child_pose", reps_or_time="60s")],
        breathing_protocol="4-7-8 x6",
    )
    rt = CoolDownBody.from_payload(body.to_payload())
    assert rt.name == "evening_restorative"


def test_workout_body_roundtrip_carries_card():
    card = WorkoutCard(date_iso="2026-05-14", target_kcal=600)
    body = WorkoutBody(
        date_iso="2026-05-14",
        completion_status=CompletionStatus.PLANNED,
        target_kcal=600, target_minutes=60, card=card,
        system_prompt_version="v1",
    )
    rt = WorkoutBody.from_payload(body.to_payload())
    assert rt.completion_status == CompletionStatus.PLANNED
    assert rt.card.date_iso == "2026-05-14"
    assert rt.system_prompt_version == "v1"


def test_workout_body_system_prompt_version_optional():
    """Old rows without a system_prompt_version field deserialize
    cleanly with version=None — the field is forward-compatible."""
    body = WorkoutBody.from_payload({
        "date_iso": "2026-05-14",
        "completion_status": "planned",
        "target_kcal": 600,
        "target_minutes": 60,
        "card": {},
        # Note: no `system_prompt_version` key.
    })
    assert body.system_prompt_version is None


def test_fraser_system_prompt_version_constant_present():
    """The constant must exist and be a non-empty version string.
    Importers downstream (state.commit_workout) depend on this."""
    from agents.fraser.protocols import FRASER_SYSTEM_PROMPT_VERSION
    assert isinstance(FRASER_SYSTEM_PROMPT_VERSION, str)
    assert FRASER_SYSTEM_PROMPT_VERSION
    assert FRASER_SYSTEM_PROMPT_VERSION.startswith("v")


def test_movement_instance_body_roundtrip():
    body = MovementInstanceBody(
        workout_entity_id=42,
        movement="Back Squat",
        load_kg=92.5, reps=5,
        executed_volume_kcal=180,
        logged_at_iso="2026-05-14T18:32:11",
    )
    rt = MovementInstanceBody.from_payload(body.to_payload())
    # Movement name normalized.
    assert rt.movement == "back_squat"
    assert rt.load_kg == 92.5
    assert rt.workout_entity_id == 42


# ─── 5. Input-mode classifier ───────────────────────────────────────
def test_classify_default_for_empty_or_generic():
    assert classify_input_mode("") is InputMode.DEFAULT
    assert classify_input_mode("what's today") is InputMode.DEFAULT
    assert classify_input_mode("give me the plan") is InputMode.DEFAULT


def test_classify_user_supplied_on_benchmark_name():
    """Spec §2.4 §11 cases: 'Murph at 70%' / 'do Cindy' / 'Fran'."""
    assert classify_input_mode("Murph at 70%") is InputMode.USER_SUPPLIED_WORKOUT
    assert classify_input_mode("let's do Cindy today") is InputMode.USER_SUPPLIED_WORKOUT
    assert classify_input_mode("FRAN") is InputMode.USER_SUPPLIED_WORKOUT


def test_classify_user_supplied_on_pasted_rep_scheme():
    """A '21-15-9 / pull-ups / thrusters / etc.' paste is user-supplied."""
    msg = "21-15-9 / Thrusters / Pull-ups / For Time"
    assert classify_input_mode(msg) is InputMode.USER_SUPPLIED_WORKOUT


def test_classify_user_requested_on_format_word():
    """Spec §2.4: 'give me an EMOM' / 'AMRAP 18'."""
    assert classify_input_mode("give me an EMOM today") is InputMode.USER_REQUESTED_FORMAT
    assert classify_input_mode("AMRAP 18 please") is InputMode.USER_REQUESTED_FORMAT
    assert classify_input_mode("Tabata legs") is InputMode.USER_REQUESTED_FORMAT
    # Smash Format is a Fraser-specific format from the transcript.
    assert classify_input_mode("Let's do a Smash Format") is InputMode.USER_REQUESTED_FORMAT


def test_classify_benchmark_outranks_format():
    """'Murph at EMOM pace' must classify as USER_SUPPLIED — the
    benchmark is the strongest signal."""
    msg = "Murph at EMOM pace 70%"
    assert classify_input_mode(msg) is InputMode.USER_SUPPLIED_WORKOUT


def test_extract_requested_format_pulls_token():
    assert extract_requested_format("EMOM 18") is WodFormat.EMOM
    assert extract_requested_format("AMRAP 12 minutes") is WodFormat.AMRAP
    assert extract_requested_format("hello") is None
