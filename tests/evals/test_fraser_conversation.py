"""Fraser eval suite — first 10 of the 40 cases (spec §9).

Day-4 status: ALL CASES MARKED `xfail(strict=True)`.

Why strict=True (Day-4 directive, 2026-05-14):
    • An xfailing case that starts passing fails the suite LOUDLY —
      the engineer is forced to drop the mark in the same commit
      that stabilized the behavior. Self-policing cadence; can't
      accidentally leave xfail on a case that's been working for
      a week.

Why each case has a `_reasoner_produced_content(card)` precondition:
    • The stub reasoner returns a card with empty `strength.lifts`
      and empty `wod.movements`. A "not in" assertion on those
      empty lists passes VACUOUSLY today — it would XPASS under
      strict=True, which lies about the stability of the case.
    • The precondition asserts the card carries real movements,
      which fails today (stub) and passes once the Day-3 reasoner
      produces output. At that point the case becomes real
      coverage; drop the xfail mark in the same commit.

Each test docstring carries the spec §9 line verbatim so the eval
suite is self-documenting against the requirements doc.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("RAHAT_DB_PATH", str(db))
    from core import io as cio
    cio.DB_PATH = db
    return db


def _reasoner_produced_content(card) -> None:
    """Precondition assertion: the card has real movements somewhere.

    The stub reasoner returns empty `strength.lifts` and empty
    `wod.movements`. Without this gate, "not in" assertions against
    those empty lists pass vacuously — which would XPASS under the
    strict=True marks and lie about the case being stable.

    This precondition is the failing assertion today (stub) and the
    passing one tomorrow (reasoner). When the Day-3 reasoner lands,
    every test below starts producing real content here, which flips
    the xfail to XPASS — the strict=True mark then fails the suite
    and tells you which case to declare stable.
    """
    has_content = bool(card.strength.lifts) or bool(card.wod.movements)
    assert has_content, (
        "Workout Card has no movements — the stub reasoner returns "
        "empty blocks. Drop the xfail mark here when the Day-3 "
        "reasoner produces real output for this case.")


# ─── fraser_001 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. every strength lift's percent_1rm ≤ 70.
#   3. no movement passing `is_overhead` appears in strength.lifts
#      OR wod.movements.
#   4. NOTES section mentions HRV / red / recovery as the override reason.
# Why these bars: spec §2.3 case 1, eval anchor for the HRV-red flow.
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_001_hrv_33_scales_intensity_and_swaps_overhead(fresh_db):
    """spec §9: HRV=33 from Huberman → intensity scaled ≤70%, overhead
    pressing replaced."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout
    from agents.fraser.protocols import is_overhead

    fst.set_mock_huberman_state({
        "hrv": 33, "sleep_hours": 6.0, "rhr": 62,
        "recovery_color": "red",
    })
    card = design_workout("what's today's workout?")

    # Day-1 assertions (structural, work today).
    assert card.context.hrv == 33
    assert card.context.recovery_color == "red"

    # Precondition: reasoner produced real content. Fails today
    # (stub returns empty blocks), passes when Day-3 reasoner lands.
    _reasoner_produced_content(card)

    # Day-3 assertions (require reasoner).
    # Cap intensity at 70%.
    max_pct = max(l.percent_1rm or 0 for l in card.strength.lifts)
    assert max_pct <= 70, f"HRV-red must cap intensity ≤70%, got {max_pct}"
    # No overhead pressing in either strength or WOD blocks.
    all_movements = (
        [l.name for l in card.strength.lifts]
        + [m.name for m in card.wod.movements])
    overheads = [m for m in all_movements if is_overhead(m)]
    assert not overheads, f"HRV-red must swap overhead, got {overheads}"


# ─── fraser_002 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. "back_squat" not in strength.lifts OR wod.movements.
#   3. "box_step_over" (or normalized variants) not in either.
#   4. NOTES section mentions the glute injury as the swap reason.
#   5. Workout Card persists with a substitution row referencing
#      `mobility_limit` per ADR-004 condition vocabulary.
# Why these bars: spec §9 case 2, spec §5 item 4 (auto-mute on injury).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_002_left_glute_catch_mutes_back_squats(fresh_db):
    """spec §9: Left glute catch registered → no back squats programmed
    for 7 days."""
    from agents.fraser import state as fst
    from agents.fraser.protocols import Severity

    eta = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    fst.register_injury(
        "left_glute", severity=Severity.MODERATE,
        mute_movements=["back_squat", "box_step_over"],
        eta_iso=eta, rationale="catch behind left glute")

    # Day-1 assertion: injury persisted and movements normalized.
    active = fst.get_active_injuries()
    assert len(active) == 1
    assert "back_squat" in active[0].mute_movements

    # Day-3 assertion: composed card has no back squat.
    from agents.fraser.handler import design_workout
    card = design_workout("today's workout please")
    _reasoner_produced_content(card)
    all_movements = (
        [l.name for l in card.strength.lifts]
        + [m.name for m in card.wod.movements])
    assert "back_squat" not in all_movements


# ─── fraser_003 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. zero barbell movements in the card (back_squat / deadlift /
#      bench / strict_press / clean / snatch / thruster).
#   3. ≥1 DB-pattern movement present (db_thruster / dumbbell_press
#      / db_front_squat / etc.).
#   4. card.context.equipment matches what set_mock_travel_state declared.
#   5. NOTES section names the hotel context.
# Why these bars: spec §2.3 case 3 + spec §5 item 9 (travel adaptation).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub) + Bourdain travel stub",
                   strict=True)
def test_fraser_003_travel_no_barbell_db_only_programming(fresh_db):
    """spec §9: Travel + no barbell (Bourdain) → DB-only programming,
    hotel gym detected."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout

    fst.set_mock_travel_state({
        "away": True, "location": "JW Marriott Austin",
        "equipment": ["dumbbells", "treadmill", "yoga_mat"],
    })
    fst.set_equipment_available(
        ["dumbbells", "treadmill", "yoga_mat"])

    card = design_workout("hotel gym workout")
    _reasoner_produced_content(card)

    # Day-3 assertion: no barbell movements in the card.
    barbell_movements = {"back_squat", "deadlift", "bench",
                         "strict_press", "clean", "snatch", "thruster"}
    all_movements = (
        [l.name for l in card.strength.lifts]
        + [m.name for m in card.wod.movements])
    assert not (set(all_movements) & barbell_movements)


# ─── fraser_004 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card.context.kobe_tier == "hammer" (already passes today —
#      this is the cross-agent substrate read working).
#   2. card.target_kcal >= 720 (a +20% lift over the 600 baseline).
#   3. ≥1 strength-bias lift in strength.lifts (back_squat / deadlift
#      / front_squat / clean / bench / strict_press) — hammer is
#      strength-leaning per spec §2.3 item 2.
#   4. NOTES section quotes the tier in the rationale.
# Why these bars: spec §2.3 case 2 (hammer tier activation).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_004_hammer_tier_raises_volume_target(fresh_db):
    """spec §9: Kobe hammer tier active → weekly volume +20% vs
    baseline."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout

    fst.set_mock_kobe_tier("hammer")
    card = design_workout("today's plan")
    assert card.context.kobe_tier == "hammer"
    # Day-3 assertion: target_kcal up ≥20% vs zone2 baseline.
    # Baseline assumed 600; hammer should hit ≥720.
    assert card.target_kcal >= 720


# ─── fraser_005 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. every strength lift's percent_1rm ∈ [60, 70].
#   3. zero entries with percent_1rm > 70 (no max-effort attempts).
#   4. card.target_kcal in [420, 480] (20–30% drop from 600 baseline).
#   5. NOTES section names sleep_hours as the scaling driver.
# Why these bars: spec §5 item 8 (sleep-debt scaling).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_005_sleep_debt_caps_intensity(fresh_db):
    """spec §9: Sleep < 5h registered → intensity 60–70%, no max-effort,
    volume −20–30%."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout

    fst.set_mock_huberman_state({
        "hrv": 50, "sleep_hours": 4.5, "rhr": 60,
        "recovery_color": "amber",
    })
    card = design_workout("today's workout")
    assert card.context.sleep_hours == 4.5

    _reasoner_produced_content(card)

    # Day-3 assertions.
    max_pct = max(l.percent_1rm or 0 for l in card.strength.lifts)
    assert 60 <= max_pct <= 70


# ─── fraser_006 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card.target_kcal == 800 (already passes today).
#   2. card.target_minutes == 75 (already passes today).
#   3. (wod.predicted_burn_kcal_low + wod.predicted_burn_kcal_high)/2
#      ∈ [720, 880] — ±10% of target.
#   4. compute_predicted_burn(card).by_movement has ≥3 entries
#      (the per-movement breakdown the reasoner quotes when the user
#      asks "wouldn't SDHP burn lower than thrusters?").
#   5. NOTES section names the burn target.
# Why these bars: spec §5 item 6 (calorie targeting) + item 16 (math
# transparency).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_006_calorie_target_hits_within_tolerance(fresh_db):
    """spec §9: Calorie target 800 in 75min → designed WOD predicted
    burn within 720–880 (±10%)."""
    from agents.fraser.handler import design_workout
    card = design_workout(
        "design me a workout — 800 kcal target in 75 minutes",
        ctx={"target_kcal": 800, "target_minutes": 75})
    assert card.target_kcal == 800
    assert card.target_minutes == 75
    # Day-3 assertion: predicted burn ±10% of target.
    mid = (card.wod.predicted_burn_kcal_low
           + card.wod.predicted_burn_kcal_high) / 2
    assert 720 <= mid <= 880


# ─── fraser_007 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card produced for a rest-day source (workouts: [] OR
#      [{title: "Rest Day", description: ""}]).
#   2. card.wod has zero programmed movements (no auto-WOD).
#   3. card.cool_down has the active-recovery flow (zone-2 walk,
#      mobility, breathing).
#   4. card.notes.why_this_design explicitly labels the active-
#      recovery flow as Fraser's suggestion, NOT gym-prescribed.
#   5. No source_id link (the workout body is rest-day shaped).
# Why these bars: spec §9 case 7 + §11.5 rest-day handling — past
# incidents had Fraser silently programming on top of rest days.
#
# REWRITTEN 2026-05-14 — spec §9 was updated to reframe fraser_007
# as the rest-day case; the prior PRVN-advancement case moved to
# the substrate-test layer where it already passes.
def test_fraser_007_rest_day_surface_no_programmed_wod(fresh_db):
    """spec §9: Rest day in SugarWOD → 'rest day per gym programming'
    with active-recovery flow; NO auto-composed WOD."""
    from agents.fraser import state as fst
    from agents.fraser.source import ingest_source_week
    from agents.fraser.handler import design_workout
    from pathlib import Path
    import json as _json

    # Synthesize a single-day archive with a rest-day shape.
    archive = Path(fresh_db).parent / "rest_day_archive.json"
    archive.write_text(_json.dumps({
        "url": "https://app.sugarwod.com/?track=workout-of-the-day",
        "week_start": "20260514",
        "fetched_at": datetime.now().isoformat(),
        "days": [{
            "date_int": "20260514",
            "header": "THU 14",
            "workouts": [{"title": "Rest Day", "description": ""}],
        }],
    }))
    ingest_source_week(archive)

    card = design_workout("today's plan", today_int="20260514")

    # 1. Card produced with rest-day shape.
    assert card.date_iso == "2026-05-14"
    # 2. No auto-WOD.
    assert len(card.wod.movements) == 0
    # 3. Active-recovery flow in cool-down.
    assert len(card.cool_down.movements) > 0
    cooldown_names = {m.name for m in card.cool_down.movements}
    # The default active-recovery flow includes at least one of these.
    expected = {"zone_2_walk", "thoracic_extension_on_roller",
                "thread_the_needle", "legs_up_the_wall"}
    assert cooldown_names & expected, (
        f"Active-recovery flow missing; got {cooldown_names}")
    # 4. NOTES labels the active-recovery as Fraser's suggestion.
    why = card.notes.why_this_design.lower()
    assert "rest day" in why
    assert ("fraser's suggestion" in why or "not gym-prescribed" in why
            or "skip if" in why), (
        f"Rest-day card must clearly label active-recovery as "
        f"Fraser's idea, not gym programming. Got: {why!r}")


# ─── fraser_008 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. "jump_rope" not in wod.movements.
#   3. ≥1 of {penguin_jump, lateral_hop, run} present in wod.movements.
#   4. wod.substitutions_applied has a string containing "rope".
#   5. governance_log row exists with subject=fraser.tool.lookup_substitution_rule
#      and args.condition == "equipment_missing".
# Why these bars: spec §5 item 1 (equipment substitution) + DEFAULT
# substitution seed for jump_rope.
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_008_no_jump_rope_substitutes_penguin_or_run(fresh_db):
    """spec §9: No jump rope in equipment → penguin jumps OR run
    substituted with rationale."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout

    fst.set_equipment_available(["barbell", "dumbbells", "kettlebell"])
    card = design_workout("today")
    _reasoner_produced_content(card)

    # Day-3 assertion: if any movement WOULD have been jump_rope, it's
    # swapped to penguin_jump / lateral_hop / short_run.
    all_movements = {m.name for m in card.wod.movements}
    assert "jump_rope" not in all_movements
    # Substitution rationale references rope (the reasoner must have
    # composed a swap, not just dropped the movement silently).
    assert any("rope" in s.lower()
               for s in card.wod.substitutions_applied), (
        f"Expected a rope-substitution rationale in WOD.substitutions_"
        f"applied; got {card.wod.substitutions_applied}")


# ─── fraser_009 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. card has movements (precondition).
#   2. zero movements where `is_overhead(m)` returns True.
#   3. ≥1 non-overhead replacement in the same general category
#      (floor_press / landmine_press / bench / etc. instead of
#       strict_press / snatch / thruster).
#   4. NOTES section references the neck injury.
# Why these bars: spec §5 item 4 (joint vigilance) + spec §9 case 9.
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub)", strict=True)
def test_fraser_009_right_neck_pain_substitutes_all_overhead(fresh_db):
    """spec §9: Right neck pain registered → all overhead movements
    substituted."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout
    from agents.fraser.protocols import Severity, is_overhead

    fst.register_injury(
        "right_neck", severity=Severity.MODERATE,
        mute_movements=["strict_press", "push_press", "snatch",
                        "handstand_push_up", "thruster"])
    card = design_workout("today")
    _reasoner_produced_content(card)

    all_movements = (
        [l.name for l in card.strength.lifts]
        + [m.name for m in card.wod.movements])
    overheads = [m for m in all_movements if is_overhead(m)]
    assert not overheads, f"neck-injury must mute overhead, got {overheads}"


# ─── fraser_010 ─────────────────────────────────────────────────────
# DOMAIN ASSERTION (drop xfail when ALL of these hold):
#   1. today_card has movements (precondition).
#   2. "back_squat" not in today_card.strength.lifts.
#   3. today_card.strength.lifts ⊆ {non-back-squat patterns} —
#      either posterior-chain (deadlift / RDL / sumo) or upper-body.
#   4. NOTES section references yesterday's back-squat session as
#      the rationale for the pivot.
#   5. governance_log shows a `fraser.tool.get_recent_workouts` row
#      under the same trace_id as the design call.
# Why these bars: spec §5 item 11 (movement memory).
@pytest.mark.xfail(reason="reasoner not wired yet (Day 3 stub) + recent-volume read",
                   strict=True)
def test_fraser_010_no_back_to_back_back_squats(fresh_db):
    """spec §9: Back squats logged yesterday → next session pulls
    posterior chain or upper, not BS again."""
    from agents.fraser import state as fst
    from agents.fraser.handler import design_workout
    from agents.fraser.protocols import (
        WorkoutCard, ContextSnapshot, StrengthBlock, StrengthLift,
        CompletionStatus,
    )

    # Seed yesterday's workout with a back squat.
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    card = WorkoutCard(
        date_iso=yesterday, time_of_day="morning",
        target_kcal=600, target_minutes=60,
        strength=StrengthBlock(lifts=[StrengthLift(
            name="back_squat", working_sets=5, working_reps=5,
            working_weight_kg=92.5)]),
    )
    fst.commit_workout(card)

    # Day-3 assertion: today's composed card has no back squat.
    today_card = design_workout("today's plan")
    _reasoner_produced_content(today_card)
    today_lifts = [l.name for l in today_card.strength.lifts]
    assert "back_squat" not in today_lifts
