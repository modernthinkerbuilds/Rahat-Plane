"""fraser.handler — input-mode router + adaptation pipeline + LLM overlay.

Day-5 pivot (2026-05-14): the reasoner is no longer a from-scratch
generator. It's an **adaptation engine**. The flow:

    1. Read today's `fraser_source_workout` (SugarWOD ingest).
    2. Apply cross-agent context (HRV / tier / injuries / 1RMs /
       equipment / preferences).
    3. Run the deterministic adaptation pipeline:
        - For each movement in the primary WOD:
          * Check injury mute → substitute via lookup_substitution_rule
            keyed on `mobility_limit`.
          * Check user dislike → substitute via `user_dislike`.
          * Check equipment → substitute via `equipment_missing`.
          * Scale loads against the user's 1RMs.
          * Attach postural cues from `lookup_movement_cues`.
        - Predict burn via `compute_predicted_burn`.
        - Compose structural NOTES (rationale per substitution,
          context summary, prvn position if known).
    4. Call `core.llm.generate` to enrich the NOTES voice
       (best-effort — falls back to structural NOTES on LLM
       error or budget exhaustion).
    5. Stamp source_id on the resulting card body when persisted.

Three failure modes Fraser surfaces EXPLICITLY (no silent fallback —
the spec §11.5 doctrine "past incidents silently broke for weeks"):

    • Source workout absent → "rest day per your gym programming"
      card with optional active-recovery flow. Clearly labeled as
      Fraser's suggestion, not gym programming.
    • Source workout stale (`STALE_SOURCE_WORKOUT` sentinel) →
      "Last SugarWOD sync was N days ago. Click the bookmarklet."
      Card is empty of programming.
    • Source workout's primary WOD blacklisted (Kobe BLACKLIST /
      partner WODs / pure handstand work) → NOTES section flags
      the blacklist hit and proposes the next non-blacklisted
      section as the day's focus.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Repo root on path so this module loads under importlib `fraser`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.fraser.protocols import (  # noqa: E402, F401
    InputMode, WodFormat, WorkoutCard, WorkoutNotes, ContextSnapshot,
    WarmUpBlock, StrengthBlock, WODBlock, CoolDownBlock,
    Movement, StrengthLift,
    FraserSourceWorkoutBody, ParsedSection, ParsedMovement,
    STALE_SOURCE_WORKOUT, FRASER_SYSTEM_PROMPT_VERSION,
    normalize_movement, normalize_lift_name,
    is_overhead, loads_posterior_chain, is_pulling,
    BW_SCALING_MODEL, KCAL_TARGET_BAND_LOW, KCAL_TARGET_BAND_HIGH,
)
from agents.fraser.state import *  # noqa: E402, F401, F403
from agents.fraser.tools import (  # noqa: E402
    compute_target_weight, compute_predicted_burn,
    lookup_movement_cues, parse_user_workout,
)
from core import llm as _llm  # noqa: E402
from core import decisions as _decisions  # noqa: E402


# ─────────────────────── Input-mode classifier ───────────────────────
_FORMAT_PATTERNS = [
    (re.compile(r"\bemom\b", re.I),          WodFormat.EMOM),
    (re.compile(r"\bamrap(?:\s*\d+)?\b", re.I), WodFormat.AMRAP),
    (re.compile(r"\btabata\b", re.I),        WodFormat.TABATA),
    (re.compile(r"\bsmash\s+format\b", re.I), WodFormat.SMASH_FORMAT),
    (re.compile(r"\bfor\s+time\b", re.I),    WodFormat.FOR_TIME),
    (re.compile(r"\binterval", re.I),        WodFormat.INTERVALS),
]

_BENCHMARK_NAMES = frozenset({
    "murph", "cindy", "helen", "fran", "grace", "kelly", "diane",
    "isabel", "jackie", "karen", "annie", "mary", "elizabeth",
    "amanda", "linda", "nancy", "angie", "barbara", "chelsea",
    "filthy 50", "filthy_fifty",
})

_PASTED_WORKOUT_PATTERNS = [
    re.compile(r"\d+\s*-\s*\d+\s*-\s*\d+", re.I),
    re.compile(r"\b\d+\s*(?:rft|rounds for time)\b", re.I),
    re.compile(r"\bfor\s+time\b.*\b\d+\b.*\b\d+\b",  re.I),
]


def classify_input_mode(msg: str) -> InputMode:
    """Day-5 unchanged. Rule-based regex classifier; Day-3 LLM-fallback
    upgrade path is queued in FRASER_OPEN_QUESTIONS.md item 2."""
    if not msg:
        return InputMode.DEFAULT
    low = msg.lower()
    for name in _BENCHMARK_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", low):
            return InputMode.USER_SUPPLIED_WORKOUT
    for pat in _PASTED_WORKOUT_PATTERNS:
        if pat.search(msg):
            return InputMode.USER_SUPPLIED_WORKOUT
    for pat, _fmt in _FORMAT_PATTERNS:
        if pat.search(msg):
            return InputMode.USER_REQUESTED_FORMAT
    if msg.count("\n") >= 4 and sum(
            1 for line in msg.splitlines() if re.search(r"\d", line)) >= 4:
        return InputMode.USER_SUPPLIED_WORKOUT
    return InputMode.DEFAULT


def extract_requested_format(msg: str) -> WodFormat | None:
    if not msg:
        return None
    for pat, fmt in _FORMAT_PATTERNS:
        if pat.search(msg):
            return fmt
    return None


# ─────────────────────── System prompt (cached) ──────────────────────
_CACHED_SYSTEM_PROMPT: str | None = None


def _build_system_prompt() -> str:
    """Read the behavioral transcript + concat structural preamble.
    Cached on first call (process-boot scope per Day-5 directive)."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is not None:
        return _CACHED_SYSTEM_PROMPT

    transcript_path = (
        Path(__file__).resolve().parent.parent.parent
        / "specs" / "FRASER_BEHAVIORAL_TRANSCRIPT.md"
    )
    transcript = ""
    if transcript_path.exists():
        try:
            text = transcript_path.read_text()
            m = re.search(
                r"BEGIN TRANSCRIPT(.*?)END TRANSCRIPT",
                text, re.DOTALL)
            transcript = (m.group(1).strip() if m else text)
        except (OSError, ValueError):
            transcript = ""
    if not transcript or "<!-- paste" in transcript:
        transcript = (
            "[Behavioral transcript not yet loaded — structural "
            "fallback. Real coaching voice degrades until the "
            "transcript is populated.]"
        )

    # Structural preamble (Day-5: leads with the adaptation contract).
    from agents.fraser.protocols import (
        FRASER_CHARTER_RULE_SPECS, TOOL_CATALOG,
        SUBSTITUTION_CONDITIONS,
    )
    import json as _json
    catalog_json = _json.dumps(
        [{"name": m.name, "description": m.description,
          "args": m.args_schema, "returns": m.returns_schema}
         for m in TOOL_CATALOG],
        indent=2)
    rules_json = _json.dumps(
        [{"kind": r.kind, "description": r.description,
          "blocks_during_quiet_hours": r.blocks_during_quiet_hours,
          "blocks_during_hrv_red": r.blocks_during_hrv_red,
          "requires_huberman_green_for_increase":
              r.requires_huberman_green_for_increase}
         for r in FRASER_CHARTER_RULE_SPECS],
        indent=2)
    preamble = (
        f"# Fraser system prompt — version {FRASER_SYSTEM_PROMPT_VERSION}\n\n"
        f"You are Fraser, the CrossFit programming agent in the Rahat "
        f"mesh. Your job is to ADAPT (not invent) the user's gym-"
        f"prescribed workout to today's HRV, tier, injuries, 1RMs, "
        f"equipment, and preferences. The gym's SugarWOD calendar is "
        f"the source of truth for what to do; your value is the "
        f"last-mile personalization.\n\n"
        f"## Adaptation pipeline\n"
        f"1. Call `get_todays_source_workout()` FIRST. Handle three "
        f"return shapes:\n"
        f"   - Workout body → adapt it (continue to step 2).\n"
        f"   - `null` → surface 'rest day per gym programming'; "
        f"propose optional active recovery.\n"
        f"   - `STALE` sentinel → surface 'last sync was N days ago, "
        f"click the bookmarklet'. Do NOT use stale data.\n"
        f"2. Read context: `get_huberman_state`, `get_kobe_tier`, "
        f"`get_active_injuries`, `get_1rms`, `get_equipment_available`, "
        f"`get_preferences`.\n"
        f"3. For each movement in the source's primary WOD: check "
        f"injury mute / preference dislike / equipment missing → "
        f"substitute via `lookup_movement_cues` + the substitution "
        f"rule registry. Conditions are drawn from this vocabulary: "
        f"{SUBSTITUTION_CONDITIONS}.\n"
        f"4. Scale loads via `compute_target_weight` against the "
        f"user's 1RMs.\n"
        f"5. Predict burn via `compute_predicted_burn`.\n"
        f"6. Output a Workout Card with movements, cues, predicted "
        f"burn, and NOTES section explaining every substitution.\n\n"
        f"## Charter rules (vetoes propagate from `core.charter`)\n"
        f"```json\n{rules_json}\n```\n\n"
        f"## Tool catalog\n"
        f"```json\n{catalog_json}\n```\n\n"
        f"## Coaching voice (from the behavioral transcript)\n"
    )
    _CACHED_SYSTEM_PROMPT = preamble + transcript
    return _CACHED_SYSTEM_PROMPT


# ─────────────────────── Adaptation pipeline ─────────────────────────
# Default-mode tool-call cap (Day-5 directive: 8-hop cap + budget cap,
# defense in depth).
TOOL_CALL_HOP_CAP = 8


def _rest_day_card(*, label: str, date_iso: str,
                   ctx: ContextSnapshot) -> WorkoutCard:
    """Compose a rest-day card with an optional active-recovery flow.
    Clearly labeled in NOTES — gym programming, not Fraser's idea."""
    cool = CoolDownBlock(
        duration_min=20,
        movements=[
            Movement(name="zone_2_walk", reps_or_time="20 min"),
            Movement(name="thoracic_extension_on_roller",
                     reps_or_time="6 reps × 3 sets"),
            Movement(name="thread_the_needle",
                     reps_or_time="6/side × 3 sets"),
            Movement(name="legs_up_the_wall", reps_or_time="5 min"),
        ],
        breathing_protocol="box breathing 4×4×4×4",
    )
    notes = WorkoutNotes(
        why_this_design=(
            f"Rest day per your gym programming ({label}). "
            f"Active-recovery flow below is Fraser's suggestion, "
            f"not gym-prescribed — skip if you'd rather take a "
            f"full off day."
        ),
        deltas_from_request=[],
    )
    return WorkoutCard(
        date_iso=date_iso, time_of_day="",
        target_kcal=120, target_minutes=20,
        context=ctx,
        warm_up=WarmUpBlock(),
        strength=StrengthBlock(),
        wod=WODBlock(format=WodFormat.STRENGTH_ONLY),
        cool_down=cool,
        notes=notes,
        input_mode=InputMode.DEFAULT,
    )


def _stale_source_card(*, date_iso: str, ctx: ContextSnapshot) -> WorkoutCard:
    """Compose a 'click the bookmarklet' card. Empty programming —
    the explicit non-fallback per the §11.5 doctrine."""
    notes = WorkoutNotes(
        why_this_design=(
            "Your SugarWOD sync is stale (>7 days). I won't program "
            "off old data — click the bookmarklet on the calendar "
            "page, then ask me again. No silent fallbacks."
        ),
        deltas_from_request=[],
    )
    return WorkoutCard(
        date_iso=date_iso, time_of_day="",
        target_kcal=0, target_minutes=0,
        context=ctx,
        warm_up=WarmUpBlock(),
        strength=StrengthBlock(),
        wod=WODBlock(format=WodFormat.FOR_TIME),
        cool_down=CoolDownBlock(),
        notes=notes,
        input_mode=InputMode.DEFAULT,
    )


# ─────────────────────── Day-6: BW-scaling helper ─────────────────────
def _apply_bw_scaling(movement: Movement, *, tier: str) -> Movement:
    """For movements in BW_SCALING_MODEL (KB swing, wall sit, push-up,
    plank, BW farmers carry), stamp the tier-driven Rx + rationale.

    The rationale lands on `Movement.substitution_reason` (re-using
    that field as the per-movement annotation slot) so the card
    render surfaces it inline without a schema change. If a
    substitution_reason is already set (e.g., injury swap), keep it
    and append the BW note.
    """
    name = normalize_movement(movement.name)
    scaling = BW_SCALING_MODEL.get(name)
    if scaling is None:
        return movement
    value = scaling.by_tier.get(tier)
    if value is None:
        # Tier not in the table — fall back to zone2 baseline.
        value = scaling.by_tier.get("zone2")
    if value is None:
        return movement
    rationale = scaling.rationale_template.format(
        movement=movement.name, value=value, units=scaling.units,
        tier=tier or "default")
    # Stamp the load / time onto reps_or_time so the card render
    # shows "wall_sit — 60s" or "kettlebell_swing — 15 @ 24 kg".
    if name == "wall_sit" or name == "plank" or name == "dead_hang":
        # Time-bounded: tier value IS the duration.
        movement = Movement(
            name=movement.name,
            reps_or_time=f"{value}{scaling.units}",
            load_kg=movement.load_kg,
            percent_1rm=movement.percent_1rm,
            substitution_reason=_compose_reason(
                movement.substitution_reason, rationale),
        )
    elif name in ("kettlebell_swing", "farmers_carry"):
        # Load-bearing: tier value IS the load (kg).
        movement = Movement(
            name=movement.name,
            reps_or_time=movement.reps_or_time,
            load_kg=float(value) if isinstance(value, (int, float)) else movement.load_kg,
            percent_1rm=None,
            substitution_reason=_compose_reason(
                movement.substitution_reason, rationale),
        )
    elif name == "push_up":
        # Rep-bounded: tier value is the rep count.
        movement = Movement(
            name=movement.name,
            reps_or_time=str(value) if not movement.reps_or_time else movement.reps_or_time,
            load_kg=movement.load_kg,
            percent_1rm=movement.percent_1rm,
            substitution_reason=_compose_reason(
                movement.substitution_reason, rationale),
        )
    return movement


def _compose_reason(existing: str | None, new: str) -> str:
    """Stack reason lines so injury / equipment / BW-scaling notes
    coexist on a single Movement."""
    if not existing:
        return new
    return f"{existing}; {new}"


# ─────────────────────── Day-6: Default mobility flow ─────────────────
# When the source workout lacks a PRVN-Reset block OR the parser
# extracts zero movements, fall back to a movement-pattern-keyed
# mobility flow so the card always carries a cool-down. Per the
# Day-6 directive: "squat day → hip flexor stretch; pull day →
# t-spine work".
_DEFAULT_MOBILITY_BY_PATTERN: dict[str, list[Movement]] = {
    "squat": [
        Movement(name="couch_stretch", reps_or_time="60s/side"),
        Movement(name="hip_flexor_stretch", reps_or_time="60s/side"),
        Movement(name="ankle_dorsiflexion", reps_or_time="45s/side"),
    ],
    "pull": [
        Movement(name="thread_the_needle", reps_or_time="6/side"),
        Movement(name="thoracic_extension_on_roller", reps_or_time="8"),
        Movement(name="lat_stretch", reps_or_time="45s/side"),
    ],
    "press": [
        Movement(name="puppy_pose", reps_or_time="60s"),
        Movement(name="rack_lat_stretch", reps_or_time="45s/side"),
        Movement(name="wall_slide", reps_or_time="8"),
    ],
    "run": [
        Movement(name="elevated_calf_stretch", reps_or_time="60s/side"),
        Movement(name="supine_hamstring_stretch", reps_or_time="45s/side"),
        Movement(name="sciatic_nerve_floss", reps_or_time="6/side"),
    ],
    "default": [
        Movement(name="thread_the_needle", reps_or_time="6/side"),
        Movement(name="thoracic_extension_on_roller", reps_or_time="8"),
        Movement(name="child_pose", reps_or_time="60s"),
    ],
}


def _classify_movement_pattern(wod_sec: ParsedSection | None,
                              strength_sec: ParsedSection | None) -> str:
    """Pick the dominant movement pattern of the day. Used to key the
    default mobility flow. Squat trumps pull trumps press trumps run
    when multiple patterns appear — strength-dominant work biases
    toward squat/pull, conditioning work biases toward run."""
    candidates: list[str] = []
    for sec in (strength_sec, wod_sec):
        if sec is None:
            continue
        title_low = sec.title.lower()
        if "squat" in title_low or "deadlift" in title_low:
            candidates.append("squat")
        if "pull" in title_low or "row" in title_low or "snatch" in title_low:
            candidates.append("pull")
        if "press" in title_low or "bench" in title_low or "push" in title_low:
            candidates.append("press")
        for mov in (sec.movements or []):
            n = mov.name
            if any(p in n for p in ("squat", "deadlift", "lunge")):
                candidates.append("squat")
            elif any(p in n for p in ("snatch", "clean", "pull_up", "row")):
                candidates.append("pull")
            elif any(p in n for p in ("press", "bench", "push_up")):
                candidates.append("press")
            elif "run" in n:
                candidates.append("run")
    for pattern in ("squat", "pull", "press", "run"):
        if pattern in candidates:
            return pattern
    return "default"


def _default_mobility_flow(wod_sec: ParsedSection | None,
                          strength_sec: ParsedSection | None
                          ) -> list[Movement]:
    """Return the default mobility movement list for this day's pattern."""
    pattern = _classify_movement_pattern(wod_sec, strength_sec)
    return list(_DEFAULT_MOBILITY_BY_PATTERN.get(pattern,
                _DEFAULT_MOBILITY_BY_PATTERN["default"]))


# ─────────────────────── Day-6: Target-vs-predicted scaling ───────────
def _scale_card_to_target(card: WorkoutCard, *,
                         target_kcal: float | None) -> tuple[WorkoutCard, str]:
    """If `target_kcal` is set and the card's predicted burn falls
    outside the ±20% band, scale up or down. Returns (card, adjustment
    label) where adjustment is "scaled-up" / "scaled-down" /
    "within-band" / "no-target".

    Scaling is deliberately gentle today — add/drop one round, ±15%
    on loads, ±20% on cap. The LLM overlay can refine; the
    deterministic path just lands a card inside the band.
    """
    if target_kcal is None or target_kcal <= 0:
        return card, "no-target"
    mid_predicted = (card.wod.predicted_burn_kcal_low
                     + card.wod.predicted_burn_kcal_high) / 2.0
    if mid_predicted <= 0:
        return card, "no-prediction"
    low_band = target_kcal * KCAL_TARGET_BAND_LOW
    high_band = target_kcal * KCAL_TARGET_BAND_HIGH
    if low_band <= mid_predicted <= high_band:
        return card, "within-band"

    if mid_predicted < low_band:
        # Scale UP — bump rounds, lengthen cap, increase load on
        # strength lifts. We add multiplier rounds via the structure
        # text and bump the cap; the burn estimator runs again.
        factor = min(low_band / mid_predicted, 2.0)
        # Inflate rounds count if the structure carries one.
        m = re.match(r"(\d+)\s+Rounds", card.wod.rounds_or_structure)
        if m:
            old_rounds = int(m.group(1))
            new_rounds = max(old_rounds + 1, int(old_rounds * factor))
            card.wod.rounds_or_structure = re.sub(
                r"^\d+", str(new_rounds), card.wod.rounds_or_structure)
            card.wod.cap_min = int(card.wod.cap_min * (new_rounds / old_rounds))
            card.wod.predicted_burn_kcal_low = int(card.wod.predicted_burn_kcal_low * (new_rounds / old_rounds))
            card.wod.predicted_burn_kcal_high = int(card.wod.predicted_burn_kcal_high * (new_rounds / old_rounds))
        else:
            # No rounds — bump cap proportionally.
            card.wod.cap_min = int(card.wod.cap_min * factor)
            card.wod.predicted_burn_kcal_low = int(card.wod.predicted_burn_kcal_low * factor)
            card.wod.predicted_burn_kcal_high = int(card.wod.predicted_burn_kcal_high * factor)
        return card, "scaled-up"

    # mid_predicted > high_band — scale DOWN.
    factor = max(high_band / mid_predicted, 0.5)
    m = re.match(r"(\d+)\s+Rounds", card.wod.rounds_or_structure)
    if m and int(m.group(1)) > 1:
        old_rounds = int(m.group(1))
        new_rounds = max(1, int(old_rounds * factor))
        card.wod.rounds_or_structure = re.sub(
            r"^\d+", str(new_rounds), card.wod.rounds_or_structure)
        card.wod.cap_min = int(card.wod.cap_min * (new_rounds / old_rounds))
        card.wod.predicted_burn_kcal_low = int(card.wod.predicted_burn_kcal_low * (new_rounds / old_rounds))
        card.wod.predicted_burn_kcal_high = int(card.wod.predicted_burn_kcal_high * (new_rounds / old_rounds))
    else:
        card.wod.cap_min = max(5, int(card.wod.cap_min * factor))
        card.wod.predicted_burn_kcal_low = int(card.wod.predicted_burn_kcal_low * factor)
        card.wod.predicted_burn_kcal_high = int(card.wod.predicted_burn_kcal_high * factor)
    return card, "scaled-down"


def _adapt_movement(parsed: ParsedMovement,
                   *, injuries_mute: set[str],
                   disliked: set[str],
                   equipment_missing_conditions: dict[str, str],
                   substitutions_log: list[str],
                   db_path: str | None = None) -> Movement | None:
    """Run one parsed source-workout movement through the substitution
    pipeline. Returns the adapted Movement or None to drop entirely.

    Substitution lookup order:
        1. Injury mute → 'mobility_limit'
        2. User dislike → 'user_dislike'
        3. Equipment gap → 'equipment_missing'
    Whichever condition matches first, the rule registry is consulted.
    """
    name = parsed.name
    reason: str | None = None
    condition: str | None = None

    if name in injuries_mute:
        condition = "mobility_limit"
        reason = "injury-driven mute"
    elif name in disliked:
        condition = "user_dislike"
        reason = "user-declared dislike"
    elif name in equipment_missing_conditions:
        condition = "equipment_missing"
        reason = equipment_missing_conditions[name]

    if condition is None:
        return Movement(
            name=name, reps_or_time=parsed.reps_or_time,
        )

    rule = lookup_substitution_rule(  # noqa: F405
        name, condition, db_path=db_path)
    if rule and rule.replacements:
        replacement = rule.replacements[0]
        substitutions_log.append(
            f"{name} → {replacement} ({reason})")
        return Movement(
            name=replacement,
            reps_or_time=parsed.reps_or_time,
            substitution_reason=f"{reason}: {name} → {replacement}",
        )
    # No rule found — drop the movement and log the gap. Better to
    # under-program than to inflict a contraindicated movement.
    substitutions_log.append(
        f"{name} dropped — no substitution rule for "
        f"({reason or condition})")
    return None


def _adapt_strength_section(sec: ParsedSection,
                           *, one_rms: dict,
                           db_path: str | None = None) -> StrengthBlock:
    """Build a StrengthBlock from a 'strength' section. Pulls the
    canonical lift name out of the title (Bench Press, Back Squat,
    Snatch Complex). Scales loads via `compute_target_weight`."""
    title_low = sec.title.lower()
    lift_name = ""
    for lift in ("back_squat", "deadlift", "bench", "strict_press",
                 "push_press", "clean", "snatch", "front_squat"):
        if lift in title_low or lift.replace("_", " ") in title_low:
            lift_name = lift
            break
    if not lift_name:
        lift_name = "back_squat"  # default if extraction fails

    one_rm_kg = float(one_rms.get(lift_name, {}).get("weight_kg", 0.0))
    # Default working % when source doesn't specify — 70% is the
    # safe baseline that respects HRV-amber.
    pct = 70.0
    pct_match = re.search(r"(\d+)\s*%", sec.raw_description)
    if pct_match:
        try:
            pct = float(pct_match.group(1))
        except ValueError:
            pass
    working_kg = compute_target_weight(lift_name, pct, one_rm_kg) if one_rm_kg else 0.0

    return StrengthBlock(
        duration_min=sec.cap_min or 15,
        lifts=[StrengthLift(
            name=lift_name,
            working_sets=5, working_reps=5,
            working_weight_kg=working_kg,
            percent_1rm=pct,
            ramp_up_kg=([20.0, 40.0, 60.0, 80.0] if working_kg > 80 else []),
        )],
    )


def _adapt_wod_section(sec: ParsedSection,
                      *, injuries_mute: set[str],
                      disliked: set[str],
                      equipment_missing_conditions: dict[str, str],
                      substitutions_log: list[str],
                      db_path: str | None = None) -> WODBlock:
    """Build a WODBlock from the primary WOD section."""
    adapted: list[Movement] = []
    for mov in sec.movements:
        out = _adapt_movement(
            mov,
            injuries_mute=injuries_mute,
            disliked=disliked,
            equipment_missing_conditions=equipment_missing_conditions,
            substitutions_log=substitutions_log,
            db_path=db_path)
        if out is not None:
            adapted.append(out)

    fmt_enum = WodFormat.FOR_TIME
    if sec.format:
        fmt_low = sec.format.lower()
        if "amrap" in fmt_low:
            fmt_enum = WodFormat.AMRAP
        elif "emom" in fmt_low:
            fmt_enum = WodFormat.EMOM
        elif "tabata" in fmt_low:
            fmt_enum = WodFormat.TABATA

    return WODBlock(
        format=fmt_enum,
        cap_min=sec.cap_min or 20,
        movements=adapted,
        rounds_or_structure=sec.rounds_or_structure,
        substitutions_applied=list(substitutions_log),
        predicted_burn_kcal_low=0,
        predicted_burn_kcal_high=0,
    )


def _adapt_source_workout(source: FraserSourceWorkoutBody,
                         *, ctx: ContextSnapshot,
                         one_rms: dict,
                         injuries_mute: set[str],
                         disliked: set[str],
                         equipment_missing_conditions: dict[str, str],
                         date_iso: str,
                         db_path: str | None = None) -> WorkoutCard:
    """Run the full deterministic adaptation pipeline on a parsed
    source workout. Returns a populated Workout Card."""
    parsed = source.parsed
    if parsed is None or not parsed.sections:
        return _rest_day_card(
            label="empty source", date_iso=date_iso, ctx=ctx)

    substitutions_log: list[str] = []

    # Strength block: from the first 'strength' section, if any.
    strength_sec = next(
        (s for s in parsed.sections
         if s.section_kind == "strength" and not s.is_skip_section),
        None)
    strength = (_adapt_strength_section(
        strength_sec, one_rms=one_rms, db_path=db_path)
                if strength_sec else StrengthBlock())

    # WOD block: from primary_wod_index, or first non-skip 'wod' section.
    wod_sec: ParsedSection | None = None
    if 0 <= parsed.primary_wod_index < len(parsed.sections):
        wod_sec = parsed.sections[parsed.primary_wod_index]
    if wod_sec is None or wod_sec.is_skip_section:
        wod_sec = next(
            (s for s in parsed.sections
             if s.section_kind == "wod" and not s.is_skip_section),
            None)
    wod = (_adapt_wod_section(
                wod_sec,
                injuries_mute=injuries_mute, disliked=disliked,
                equipment_missing_conditions=equipment_missing_conditions,
                substitutions_log=substitutions_log,
                db_path=db_path)
           if wod_sec else WODBlock())

    # Warm-up + cool-down: standard Hunch-reset + PRVN Reset if source has it.
    warm_up = WarmUpBlock(
        duration_min=8,
        movements=[
            Movement(name="face_pull", reps_or_time="15"),
            Movement(name="cat_cow", reps_or_time="10"),
            Movement(name="chin_tuck", reps_or_time="10"),
        ],
        postural_cues=["Hunch reset"],
    )
    # Day-6: cool-down sourced from PRVN Reset if present AND parser
    # extracted ≥1 movement. Otherwise fall back to the pattern-keyed
    # default mobility flow so the card ALWAYS surfaces a cool-down.
    reset_sec = next(
        (s for s in parsed.sections if s.section_kind == "reset"), None)
    if reset_sec and reset_sec.movements:
        cool_movements = [Movement(name=m.name, reps_or_time=m.reps_or_time)
                          for m in reset_sec.movements]
    else:
        cool_movements = _default_mobility_flow(wod_sec, strength_sec)
    cool = CoolDownBlock(
        duration_min=8,
        movements=cool_movements,
        breathing_protocol="legs-up-the-wall 5 min",
    )

    # Day-6: BW-scaling pass on WOD movements. KB swings / wall sits /
    # push-ups / planks / BW farmers carry get tier-appropriate Rx +
    # a rationale note that surfaces in the card.
    tier_str = ctx.kobe_tier or "zone2"
    wod.movements = [_apply_bw_scaling(m, tier=tier_str) for m in wod.movements]

    # Predict burn now that movements are wired. The WOD's
    # rounds_or_structure carries an `N Rounds` multiplier that we
    # apply to the per-round burn — Lava Plume's 6 rounds otherwise
    # under-estimates because compute_predicted_burn sees one round
    # of movements.
    card = WorkoutCard(
        date_iso=date_iso, time_of_day=ctx.time_of_day or "",
        target_kcal=600, target_minutes=60,
        context=ctx, warm_up=warm_up, strength=strength,
        wod=wod, cool_down=cool,
        notes=WorkoutNotes(),
        input_mode=InputMode.DEFAULT,
    )
    burn = compute_predicted_burn(card)
    rounds_multiplier = 1
    m = re.match(r"(\d+)\s+Rounds", card.wod.rounds_or_structure or "")
    if m:
        rounds_multiplier = max(int(m.group(1)), 1)
    card.wod.predicted_burn_kcal_low = burn.total_low * rounds_multiplier
    card.wod.predicted_burn_kcal_high = burn.total_high * rounds_multiplier

    # Blacklist surfacing — if the WOD section was blacklisted, flag it.
    blacklist_note = ""
    if wod_sec and wod_sec.is_blacklisted:
        blacklist_note = f" Blacklist hit: {wod_sec.blacklist_reason}."

    # Structural NOTES. LLM enriches the voice; this is the fallback.
    why = (
        f"Adapted from gym programming '{wod_sec.title if wod_sec else 'unknown'}'. "
        f"HRV={ctx.hrv}, recovery={ctx.recovery_color}, tier={ctx.kobe_tier}, "
        f"active injuries={len(ctx.active_injuries)}."
        f"{blacklist_note}"
    )
    card.notes = WorkoutNotes(
        why_this_design=why,
        deltas_from_request=list(substitutions_log),
        prvn_position=None,
        chest_progression_position=None,
    )
    return card


def design_workout(msg: str = "",
                  *, ctx: dict | None = None,
                  today_int: str | None = None,
                  db_path: str | None = None) -> WorkoutCard:
    """Main entry. Day-5: replaces the Day-1 stub with the adaptation
    pipeline. LLM is overlay-only — the deterministic pipeline
    produces a complete card on its own.

    `today_int` defaults to today's YYYYMMDD string. Override for
    tests / time-travel.
    """
    today_str = today_int or datetime.now().strftime("%Y%m%d")
    date_iso = (f"{today_str[:4]}-{today_str[4:6]}-{today_str[6:]}"
                if len(today_str) == 8 else today_str)
    mode = classify_input_mode(msg)

    # Build the context snapshot up-front (substrate reads happen ONCE
    # per design, not per tool call — reads are cheap but tracing is
    # cleaner with a single read pass).
    huberman = get_huberman_state(db_path=db_path)         # noqa: F405
    tier = get_kobe_tier(db_path=db_path)                   # noqa: F405
    active_injuries = get_active_injuries(db_path=db_path)  # noqa: F405
    equipment = get_equipment_available(db_path=db_path)    # noqa: F405
    one_rms = get_1rms(db_path=db_path)                     # noqa: F405
    preferences = get_preferences(db_path=db_path)          # noqa: F405

    ctx_snap = ContextSnapshot(
        hrv=huberman.get("hrv"),
        sleep_hours=huberman.get("sleep_hours"),
        kobe_tier=tier,
        recovery_color=huberman.get("recovery_color"),
        active_injuries=[i.body_part for i in active_injuries],
        equipment=list(equipment),
        time_of_day=(ctx or {}).get("time_of_day"),
    )

    if mode == InputMode.DEFAULT:
        source = get_todays_source_workout(  # noqa: F405
            today=today_str, db_path=db_path)
        if source is STALE_SOURCE_WORKOUT:
            return _stale_source_card(date_iso=date_iso, ctx=ctx_snap)
        if source is None:
            return _rest_day_card(
                label="no source workout for today",
                date_iso=date_iso, ctx=ctx_snap)

        # Build mute / dislike / equipment sets from substrate.
        injuries_mute: set[str] = set()
        for inj in active_injuries:
            for m in inj.mute_movements:
                injuries_mute.add(m)
        disliked = get_disliked_movements(db_path=db_path)  # noqa: F405

        # Equipment-missing conditions: any known movement that
        # requires equipment the user doesn't have, mapped to a
        # reason string for the substitution log.
        equipment_missing_conditions: dict[str, str] = {}
        if "jump_rope" not in equipment and "rope" not in equipment:
            equipment_missing_conditions["jump_rope"] = "no jump rope"
            equipment_missing_conditions["double_under"] = "no jump rope"
        if "wall_ball" not in equipment and "med_ball" not in equipment:
            equipment_missing_conditions["wall_ball"] = "no wall ball"
        if "pull_up_bar" not in equipment and "barbell" not in equipment:
            equipment_missing_conditions["pull_up"] = "no pull-up bar"
        if "box" not in equipment:
            equipment_missing_conditions["box_jump"] = "no box"

        card = _adapt_source_workout(
            source, ctx=ctx_snap, one_rms=one_rms,
            injuries_mute=injuries_mute, disliked=disliked,
            equipment_missing_conditions=equipment_missing_conditions,
            date_iso=date_iso, db_path=db_path)

        # Day-6: target-vs-predicted scaling. Read Kobe's target_kcal,
        # adjust rounds/cap if the band is exceeded, surface the math
        # in NOTES.
        kobe_target = get_kobe_kcal_target(today=today_str, db_path=db_path)  # noqa: F405
        card, adjustment = _scale_card_to_target(card, target_kcal=kobe_target)
        predicted_mid = (card.wod.predicted_burn_kcal_low
                         + card.wod.predicted_burn_kcal_high) // 2
        if kobe_target:
            target_line = (
                f"\n\n**Kobe target**: {int(kobe_target)} kcal · "
                f"**Predicted**: {card.wod.predicted_burn_kcal_low}–"
                f"{card.wod.predicted_burn_kcal_high} kcal "
                f"(mid {predicted_mid}) · "
                f"**Adjustment**: {adjustment}"
            )
        else:
            target_line = (
                f"\n\n**Kobe target**: not set · "
                f"**Predicted**: {card.wod.predicted_burn_kcal_low}–"
                f"{card.wod.predicted_burn_kcal_high} kcal · "
                f"**Adjustment**: no-target"
            )
        card.notes.why_this_design = (
            (card.notes.why_this_design or "") + target_line)
        # If the WOD has BW-scaled movements, append a brief note so
        # the rationale is visible at the NOTES level too.
        bw_scaling_notes = [
            m.substitution_reason for m in card.wod.movements
            if m.substitution_reason and "tier" in (m.substitution_reason or "").lower()
        ]
        if bw_scaling_notes:
            card.notes.deltas_from_request.extend(
                f"BW-scaling: {n}" for n in bw_scaling_notes)

        # LLM overlay — enrich NOTES voice. Best-effort: any failure
        # falls back to the structural NOTES already in place.
        card = _llm_enrich_notes(card, source=source, ctx=ctx_snap,
                                 db_path=db_path)
        return card

    if mode == InputMode.USER_SUPPLIED_WORKOUT:
        # Parse user input, scale weights, output card.
        one_rms_kg = {k: v.get("weight_kg") for k, v in one_rms.items()}
        card = parse_user_workout(msg, one_rms_kg=one_rms_kg)
        card.context = ctx_snap
        card.date_iso = date_iso
        card.target_kcal = card.target_kcal or 600
        card.target_minutes = card.target_minutes or 60
        burn = compute_predicted_burn(card)
        card.wod.predicted_burn_kcal_low = burn.total_low
        card.wod.predicted_burn_kcal_high = burn.total_high
        return card

    # USER_REQUESTED_FORMAT — stubbed for Day 5+ generation flow.
    return WorkoutCard(
        date_iso=date_iso, time_of_day=ctx_snap.time_of_day or "",
        target_kcal=500, target_minutes=45,
        context=ctx_snap,
        wod=WODBlock(
            format=extract_requested_format(msg) or WodFormat.FOR_TIME,
            cap_min=18,
            movements=[],
            rounds_or_structure="",
            substitutions_applied=[],
        ),
        notes=WorkoutNotes(
            why_this_design=(
                f"User requested format: '{msg}'. Generation-mode is "
                f"deferred to Day 5+ wiring; today this returns an "
                f"empty card with the requested format honored."
            )
        ),
        input_mode=InputMode.USER_REQUESTED_FORMAT,
    )


def _llm_enrich_notes(card: WorkoutCard,
                     *, source: FraserSourceWorkoutBody,
                     ctx: ContextSnapshot,
                     db_path: str | None = None) -> WorkoutCard:
    """Call `core.llm.generate` to enrich the NOTES section voice.

    Best-effort. Three failure modes, all fall back to the structural
    NOTES already on the card:
        1. `BudgetExceeded` — daily cap hit; structural NOTES stay,
           card adds 'budget cap hit — voice-enrichment skipped'.
        2. `GeminiUsage.error` set — wire-call failure; same fallback.
        3. Stub response ('[LLM-FALLBACK]' from conftest under
           RAHAT_TEST_MODE without a fixture) — empty enrichment;
           structural NOTES stay.
    """
    trace_id = _decisions.new_trace()
    structural_why = card.notes.why_this_design
    prompt = _build_enrichment_prompt(card, source, ctx)
    try:
        usage = _llm.generate(
            actor="fraser", kind="fraser.notes_enrichment",
            prompt=prompt, trace_id=trace_id, db_path=db_path)
    except _llm.BudgetExceeded:
        card.notes.why_this_design = (
            f"{structural_why}\n\n[budget cap hit — coaching-voice "
            f"enrichment skipped; structural NOTES only.]"
        )
        return card
    except (ImportError, Exception) as e:  # noqa: BLE001
        # genai import error (no API key / package missing), network
        # failures, etc. NOTES enrichment is overlay-only — never
        # bring the card down. Structural NOTES are already populated.
        card.notes.why_this_design = (
            f"{structural_why}\n\n[LLM enrichment unavailable: "
            f"{type(e).__name__} — structural NOTES only.]"
        )
        return card

    if usage.error:
        card.notes.why_this_design = (
            f"{structural_why}\n\n[LLM error '{usage.error}' — "
            f"structural NOTES only.]"
        )
        return card

    enrichment = (usage.text or "").strip()
    if not enrichment or "[LLM-FALLBACK]" in enrichment:
        return card  # structural NOTES were already populated

    # Real LLM response — prepend the coaching voice ahead of the
    # structural rationale so both are surfaced.
    card.notes.why_this_design = f"{enrichment}\n\n— Adaptation rationale —\n{structural_why}"
    return card


def _build_enrichment_prompt(card: WorkoutCard,
                            source: FraserSourceWorkoutBody,
                            ctx: ContextSnapshot) -> str:
    """Tight prompt for NOTES enrichment. The system prompt (cached)
    carries the full transcript; this is the per-call delta."""
    import json as _json
    payload = {
        "source_title": (
            source.parsed.sections[source.parsed.primary_wod_index].title
            if source.parsed and source.parsed.primary_wod_index >= 0
            and source.parsed.primary_wod_index < len(source.parsed.sections)
            else "unknown"),
        "structural_why": card.notes.why_this_design,
        "substitutions": list(card.notes.deltas_from_request),
        "hrv": ctx.hrv,
        "recovery_color": ctx.recovery_color,
        "tier": ctx.kobe_tier,
        "injuries": list(ctx.active_injuries),
    }
    return (
        "[Workout Card NOTES enrichment] "
        "Write a 2-3 sentence coaching note explaining why this "
        "adapted workout matches the user's state today. Voice: "
        "direct, technical, prescriptive. No fluff. Quote the "
        "structural rationale's key facts in plainer English.\n\n"
        f"Context:\n```json\n{_json.dumps(payload, indent=2)}\n```\n\n"
        "Respond with the coaching note ONLY — no preamble, no "
        "JSON, no markdown headings.")


def route(msg: str) -> Any:
    """Miya entry-point."""
    from core.agent import Reply
    card = design_workout(msg)
    # Day-5: confidence climbs to 0.5 because the adaptation pipeline
    # produces real cards (not a stub). The final gate before flipping
    # FraserAgent on in miya_main.py is owner-reviewed cards from
    # real Gemini, not the confidence number.
    text = (
        f"[Fraser] mode={card.input_mode.value} · "
        f"hrv={card.context.hrv} · tier={card.context.kobe_tier} · "
        f"injuries={len(card.context.active_injuries)} · "
        f"wod={card.wod.format.value} cap={card.wod.cap_min}min · "
        f"movements={len(card.wod.movements)}"
    )
    return Reply(text=text, confidence=0.5)


def start() -> None:
    """Legacy hook. Fraser does NOT own its own bot loop."""
    print("[fraser.handler] start() is a no-op — Fraser runs under Miya.")


__all__ = [
    "classify_input_mode", "extract_requested_format",
    "design_workout", "route", "start",
    "TOOL_CALL_HOP_CAP",
    "_build_system_prompt",
]
