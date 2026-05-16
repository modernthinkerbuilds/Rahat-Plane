#!/usr/bin/env python3
"""Produce DAY5_DEMO_CARD.md — end-to-end Fraser adapter run against
the real SugarWOD archive in `staging/workspace/gym-programming/archive/`.

Approach (no real Gemini needed):
    1. Run `ingest_latest_source_week()` against the archive.
    2. Seed PLACEHOLDER 1RMs into the substrate (clearly labeled in
       the card so the owner swaps in real numbers from
       /tmp/my_1rms.json).
    3. Set HRV-green Huberman mock + zone2 tier + standard equipment.
    4. Call `handler.design_workout` for THU 14 = 20260514 (today).
    5. Render the resulting Workout Card as markdown per spec §2.5.
    6. Write to DAY5_DEMO_CARD.md at repo root.

Run:
    RAHAT_TEST_MODE=1 python -m scripts.produce_day5_demo_card

The NOTES section will be structural-only because GEMINI_API_KEY is
not set in the build sandbox. Once the owner runs
`python -m scripts.record_fraser_cassettes` on their Mac, re-running
this script will produce a coaching-voice-enriched card.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force test mode so the substrate writes are sandboxed.
os.environ["RAHAT_TEST_MODE"] = "1"
os.environ.setdefault("RAHAT_VOICE", "neutral")


# ─────────────────────────── Demo seed values ─────────────────────────
# Owner's real 1RMs (Venkat, 2026-05-15). Update in place when tested.
# These are read directly by produce_day5_demo_card to scale strength
# loads in the rendered card; they're NOT written to the substrate
# (that's a separate Fraser ingest_1rm call that lands later).
PLACEHOLDER_1RMS = [
    ("back_squat",   120.0),
    ("deadlift",     155.0),
    ("bench",         65.0),
    ("strict_press",  50.0),
    ("push_press",    65.0),
    ("clean",         70.0),
    ("snatch",        45.0),
    ("front_squat",  100.0),
]

# Owner's typical equipment loadout (home + gym).
EQUIPMENT = [
    "barbell", "dumbbells", "kettlebell", "jump_rope", "pull_up_bar",
    "box", "rowing_machine", "wall_ball", "med_ball", "echo_bike",
]


# ─────────────────────────── Card → Markdown ──────────────────────────
def render_card_md(card) -> str:
    """Render a WorkoutCard per spec §2.5 in plain markdown."""
    lines: list[str] = []
    ctx = card.context

    lines.append(f"## WORKOUT — {card.date_iso}"
                 f"{' · ' + card.time_of_day if card.time_of_day else ''}"
                 f" · Target {card.target_kcal} kcal in {card.target_minutes} min")
    lines.append("")
    inj = ", ".join(ctx.active_injuries) if ctx.active_injuries else "none"
    equip = ", ".join(ctx.equipment) if ctx.equipment else "none"
    lines.append(
        f"**Context**: HRV {ctx.hrv} · Sleep {ctx.sleep_hours}h · "
        f"Recovery {ctx.recovery_color} · Tier {ctx.kobe_tier} · "
        f"Injuries: {inj} · Equipment: {equip}")
    lines.append(f"**Input mode**: `{card.input_mode.value}`")
    lines.append("")

    # WARM-UP
    lines.append(f"### ▌WARM-UP ({card.warm_up.duration_min} min)")
    for m in card.warm_up.movements:
        loc = f" — {m.reps_or_time}" if m.reps_or_time else ""
        lines.append(f"- {m.name}{loc}")
    if card.warm_up.postural_cues:
        lines.append(f"- **Postural cues**: {', '.join(card.warm_up.postural_cues)}")
    lines.append("")

    # STRENGTH
    if card.strength.lifts:
        lines.append(f"### ▌STRENGTH ({card.strength.duration_min} min)")
        for lift in card.strength.lifts:
            pct = f" ({lift.percent_1rm}% of 1RM)" if lift.percent_1rm else ""
            lines.append(
                f"- **{lift.name}** — {lift.working_sets}×{lift.working_reps}"
                f" @ {lift.working_weight_kg} kg{pct}")
            if lift.ramp_up_kg:
                ramp = " → ".join(str(k) for k in lift.ramp_up_kg)
                lines.append(f"  - Ramp-up: {ramp} → {lift.working_weight_kg} kg")
            if lift.hbp_cue:
                lines.append(f"  - **HBP cue**: {lift.hbp_cue}")
        lines.append("")

    # WOD
    if card.wod.movements or card.wod.rounds_or_structure:
        lines.append(
            f"### ▌WOD — {card.wod.format.value} · "
            f"Cap {card.wod.cap_min} min")
        if card.wod.rounds_or_structure:
            lines.append(f"- **Structure**: {card.wod.rounds_or_structure}")
        for m in card.wod.movements:
            load = f" @ {m.load_kg}kg" if m.load_kg else ""
            sub = f" *[{m.substitution_reason}]*" if m.substitution_reason else ""
            lines.append(f"- {m.name} — {m.reps_or_time}{load}{sub}")
        if card.wod.substitutions_applied:
            lines.append("")
            lines.append("**Substitutions applied**:")
            for s in card.wod.substitutions_applied:
                lines.append(f"- {s}")
        lines.append("")
        lines.append(
            f"**Predicted burn**: "
            f"{card.wod.predicted_burn_kcal_low}–"
            f"{card.wod.predicted_burn_kcal_high} kcal")
        lines.append("")

    # COOL-DOWN
    if card.cool_down.movements or card.cool_down.breathing_protocol:
        lines.append(f"### ▌COOL-DOWN ({card.cool_down.duration_min} min)")
        for m in card.cool_down.movements:
            loc = f" — {m.reps_or_time}" if m.reps_or_time else ""
            lines.append(f"- {m.name}{loc}")
        if card.cool_down.breathing_protocol:
            lines.append(f"- **Breathing**: {card.cool_down.breathing_protocol}")
        lines.append("")

    # NOTES
    lines.append("### ▌NOTES")
    if card.notes.why_this_design:
        lines.append(card.notes.why_this_design)
    if card.notes.deltas_from_request:
        lines.append("")
        lines.append("**Deltas from source**:")
        for d in card.notes.deltas_from_request:
            lines.append(f"- {d}")
    if card.notes.prvn_position:
        lines.append(f"- **PRVN position**: {card.notes.prvn_position}")
    if card.notes.chest_progression_position:
        lines.append(f"- **Chest progression**: {card.notes.chest_progression_position}")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────── Demo run ─────────────────────────────────
def main() -> int:
    import tempfile
    db_dir = Path(tempfile.mkdtemp(prefix="day5_demo_"))
    db = db_dir / "demo.db"
    from core import io as cio
    cio.DB_PATH = db
    os.environ["RAHAT_DB_PATH"] = str(db)

    print(f"Demo DB at: {db}")

    # Step 1: ingest the latest archive.
    from agents.fraser import source, state, handler
    n, archive_path = source.ingest_latest_source_week()
    print(f"Ingested {n} days from {archive_path.name}")

    # Step 2: seed placeholder 1RMs.
    from agents.fraser.protocols import OneRMSource
    from datetime import datetime as _dt
    today_iso = _dt.now().strftime("%Y-%m-%d")
    for lift, kg in PLACEHOLDER_1RMS:
        state.update_1rm(lift, kg, tested_on_iso=today_iso,
                         source=OneRMSource.USER_PROVIDED)

    # Step 3: paint Huberman + tier + equipment mocks.
    state.set_mock_huberman_state({
        "hrv": 55, "sleep_hours": 7.5, "rhr": 58,
        "recovery_color": "green",
    })
    state.set_mock_kobe_tier("zone2")
    state.set_equipment_available(EQUIPMENT)

    # Step 4: design today's workout. Today = 2026-05-14 = THU 14.
    print("Designing workout for 2026-05-14 (THU 14 in archive)...")
    card = handler.design_workout(
        "what's today's workout?", today_int="20260514")
    print(f"Card produced: input_mode={card.input_mode.value}, "
          f"strength_lifts={len(card.strength.lifts)}, "
          f"wod_movements={len(card.wod.movements)}, "
          f"predicted_burn={card.wod.predicted_burn_kcal_low}-"
          f"{card.wod.predicted_burn_kcal_high} kcal")

    # Step 5: render markdown.
    body = render_card_md(card)

    preface = (
        f"# Fraser — DAY 5 DEMO CARD\n\n"
        f"**Generated**: 2026-05-14\n"
        f"**Source archive**: `{archive_path.name}`\n"
        f"**System-prompt version**: `v2` "
        f"(adapter pivot, see protocols.FRASER_SYSTEM_PROMPT_VERSION)\n"
        f"**LLM enrichment**: NOT applied (no GEMINI_API_KEY in build "
        f"sandbox). NOTES below is the structural fallback — once you "
        f"run `python -m scripts.record_fraser_cassettes` on your Mac, "
        f"re-running `python -m scripts.produce_day5_demo_card` "
        f"produces a coaching-voice-enriched card.\n\n"
        f"## ⚠️ Placeholder 1RMs in use\n\n"
        f"These match the spec §11 example numbers. Swap with your "
        f"real numbers from `/tmp/my_1rms.json` (your earlier message "
        f"referenced this file but the JSON body wasn't included), "
        f"then re-run the script:\n\n"
        f"| Lift | kg |\n"
        f"|---|---:|\n"
    )
    for lift, kg in PLACEHOLDER_1RMS:
        preface += f"| {lift} | {kg} |\n"

    preface += (
        f"\n## ⚠️ Context mocks in use\n\n"
        f"- **Huberman state**: HRV 55, Sleep 7.5h, Recovery green "
        f"(no real Huberman wiring yet — Day-4 contract)\n"
        f"- **Kobe tier**: zone2 (mock; real wiring is Kobe writing "
        f"`kobe_tier` entity, deferred per the brief)\n"
        f"- **Equipment**: {', '.join(EQUIPMENT)}\n"
        f"- **Injuries**: none registered\n"
        f"- **Preferences**: none declared\n\n"
        f"## What gym programming said today\n\n"
    )
    sw = state.get_todays_source_workout(today="20260514")
    if sw and sw.parsed:
        for i, sec in enumerate(sw.parsed.sections):
            marker = "★ " if i == sw.parsed.primary_wod_index else "  "
            blk = (f" *[blacklisted: {sec.blacklist_reason}]*"
                   if sec.is_blacklisted else "")
            skip = " *[skip-section]*" if sec.is_skip_section else ""
            preface += (
                f"{marker}**{sec.title}** "
                f"(`{sec.section_kind}`, format=`{sec.format or 'n/a'}`, "
                f"cap={sec.cap_min}min){blk}{skip}\n\n"
            )

    preface += "\n---\n\n"
    output = preface + body + "\n"

    out_path = ROOT / "DAY5_DEMO_CARD.md"
    out_path.write_text(output)
    print(f"\nDemo card written to: {out_path}")
    print(f"Length: {len(output)} bytes / {len(output.splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
