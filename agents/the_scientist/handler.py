"""handler — Sports Scientist's intent dispatch + nudges + loop.

Phase 4d (R1) Step 2b: extracted from agents/the_scientist/main.py.

This module owns:
    • All handle_* functions (per-intent responders)
    • route / _legacy_route / llm_coach — dispatch
    • Hindi/Hyderabadi regex blocks
    • maybe_* nudge generators
    • start — the launchd polling loop
    • send / _split_for_telegram — Telegram wire helpers

Imports from:
    • state — for DB-backed data + computation (every helper from Steps 1a/1b/2a)
    • protocols — for constants (mirrored from main.py's full import surface)
    • core.* — for the runtime substrate

main.py keeps:
    • Module-level imports + config (.env, API_KEY, etc.)
    • parse_gym_plan / eligible_cf_days wrappers
    • `from agents.the_scientist.handler import *` re-export
    • if __name__ == "__main__": start() entry point

The legacy `sci.<name>` import contract (used by ScientistAgent's
importlib loader and the eval suite) is preserved end-to-end via the
two star re-exports in main.py (state + handler).
"""
from __future__ import annotations  # 3.9-safe: defer PEP 604 / PEP 585 evaluation
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import requests
from dotenv import load_dotenv
from google import genai
from core import io as cio  # noqa: E402


from agents.the_scientist.protocols import (  # noqa: E402
    BMR_KCAL, KCAL_PER_LB_FAT, KCAL_PER_KG_FAT,
    WEEKLY_ACTIVE_TARGET_KCAL, DAILY_INTAKE_KCAL,
    INTENT_INTERMEDIATE_KG, INTENT_INTERMEDIATE_LBS,
    INTENT_TARGET_KG, INTENT_TARGET_LBS,
    INTENT_INTERMEDIATE_DATE, INTENT_TARGET_DATE,
    TARGET_LBS,
    EASY_LOSS_LB_PER_WEEK, MAX_LOSS_LB_PER_WEEK,
    LOCKED_LOSS_LB_PER_WEEK,
    TYPICAL_BURN, TIERS, DEFAULT_TIER,
    HRV_RED, HRV_YELLOW, HRV_GREEN, HRV_ELITE,
    BLACKLIST, STRENGTH_BLACKLIST,
    Z2_RUN_KCAL_DEFAULT, NONWORKOUT_BURN_FLOOR,
    NUDGE_MORNING_HOUR, NUDGE_HOURLY_START, NUDGE_HOURLY_END,
    NUDGE_RECOVERY_HOUR, HAMMER_KCAL,
    MISSED_WORKOUT_THRESHOLD_KCAL,
    DAY_TYPE_BY_TIER, DAY_TYPE_LABEL,
    WEEKDAY_INDEX, WEEKDAY_NAME, Z2_PREFERRED_WEEKDAY,
    week_bounds, hrv_band, fmt_kcal, fmt_lbs,
    _empty_prefs, _eta_at_locked_rate, _locked_intake,
    _WEEKDAY_LOOKUP, _WEEKDAY_TOKEN_RE,
    parse_weekdays, _BLACKLIST_NORMALIZE, normalize_blacklist_term,
    DAY_HEADER, GymDay,
    parse_gym_plan as _proto_parse_gym_plan,
    eligible_cf_days as _proto_eligible_cf_days,
)

from agents.the_scientist.state import *  # noqa: F401, F403


# ── LLM client + gym-plan wrappers + paths ──────────────────────────
# All four pieces are duplicated from main.py so handler.py doesn't
# need to import from main.py (which would create a circular import:
# main does `from handler import *`, handler would need `from main import …`).
# Each piece is idempotent — same env var, same Path.home() — so the
# duplication carries no behavioral risk.
#
# parse_gym_plan + eligible_cf_days are thin wrappers around the
# protocols.py versions; main.py's protocols import aliases them so
# the bare name binds to the wrapper, not the underlying function.

# Load .env explicitly here so handler.py is self-sufficient. main.py
# also calls load_dotenv() before importing this module, so this is a
# belt-and-suspenders measure — load_dotenv is idempotent.
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

# Telegram credentials — duplicated from main.py for the same reason
# client / API_KEY are: handler.py can't import from main.py without a
# circular import (main star-imports handler). Both are read from the
# same env vars, so duplication is behavior-neutral. start() and the
# Telegram-wire helpers (send, _split_for_telegram, the long-poll loop)
# all live in this module and need these names at module load time —
# main.py's copies aren't visible here.
TOKEN = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HOME = Path.home()
PLAN_PATH = HOME / "developer/agency/rahat/staging/workspace/gym-programming/weekly_plan.txt"


def _active_model() -> str:
    """Pick the freshest Flash model, preferring 2.5 over older series.

    Why tier preference rather than lexicographic sort
    --------------------------------------------------
    The previous implementation did `sorted(flash)[-1]`, which is
    lexicographically last across ALL flash variants. Google routinely
    surfaces legacy `gemini-1.5-*` alongside current `gemini-2.5-*` in
    `client.models.list()`, and a `gemini-1.5-flash-002-XL` (hypothetical
    but plausible) sorts after `gemini-2.5-flash`. Whenever that
    happened, every reasoner call 404'd because Google deprecated the
    1.5-flash tier and the bot silently slid onto a dead model.

    Explicit tier preference (2.5 → 2.0 → 1.5) is the durable fix:
    within a tier we still take the lexicographic max so -001 / -002
    revisions auto-upgrade, but we never cross a tier boundary.

    The fallback string is `gemini-2.5-flash` (no version suffix) —
    Google's documented "latest stable" alias for the 2.5 series. The
    previous fallback of `gemini-1.5-flash` was the *literal cause* of
    test-stub-environment 404s — see the API-key-rotation incident this
    PR is also patching (llm_coach error sanitization).
    """
    if not client:
        return "gemini-2.5-flash"
    preferred_tiers = ("gemini-2.5", "gemini-2.0", "gemini-1.5")
    try:
        all_flash = [m.name for m in client.models.list()
                     if "flash" in m.name.lower()]
        for tier in preferred_tiers:
            tier_matches = sorted([m for m in all_flash if tier in m])
            if tier_matches:
                return tier_matches[-1]
    except Exception:
        pass
    return "gemini-2.5-flash"


MODEL_ID = _active_model()


def parse_gym_plan(text=None):
    """Wrapper preserving the legacy zero-arg call sites — reads from
    this module's PLAN_PATH, which the eval suite reassigns."""
    return _proto_parse_gym_plan(text, plan_path=PLAN_PATH)


def eligible_cf_days(days=None):
    if days is None:
        days = parse_gym_plan()
    return _proto_eligible_cf_days(days)


__all__ = ['API_KEY', 'FIX_BURN_RE', 'HOME', 'MODEL_ID', 'PLAN_PATH', 'SLASH_COMMANDS', '_active_model', '_extract_wod_summary', '_internal_safety_downgrade', '_is_workout_on_day_query', '_legacy_route', '_n', '_parse_date', '_prorated_day_target', '_prorated_week_target', '_slash_help', '_split_for_telegram', '_try_slash_command', '_which_monday', 'client', 'daily_target', 'eligible_cf_days', 'handle_breathing', 'handle_clear_prefs', 'handle_current_weight', 'handle_daily_burn', 'handle_decision_run_or_wod', 'handle_dislike_movement', 'handle_drop_dislike', 'handle_filter', 'handle_fix_burn', 'handle_hrv', 'handle_last_week', 'handle_list_dislikes', 'handle_manual_burn', 'handle_next_week_target', 'handle_next_workout', 'handle_pace', 'handle_pick_days', 'handle_post_recovery', 'handle_pre_fuel', 'handle_recalibrate', 'handle_replan', 'handle_scheduling_help', 'handle_set_tier', 'handle_show_plan', 'handle_split_target', 'handle_swap', 'handle_today_target', 'handle_tolerate', 'handle_unavailable', 'handle_weekly_remaining', 'handle_weighin_when', 'handle_weight', 'handle_weight_timeline', 'handle_workout_on', 'handle_workout_today', 'latest_hrv', 'llm_coach', 'maybe_morning_briefing', 'maybe_recovery_nudge', 'maybe_walk_nudge', 'maybe_weekly_reset', 'parse_gym_plan', 'route', 'send', 'start']


def handle_recalibrate() -> str:
    """User-facing: 'how do I catch up?', 'am I behind?', 'what should I
    do this week?'. Returns the same recalibration proposal that the
    morning briefing uses, but on demand and full-fat."""
    r = compute_week_recalibration()
    lines = [
        f"*Week recalibration — {WEEKDAY_NAME[r['today_idx']]} morning*",
        f"Burned: *{fmt_kcal(r['burned_so_far'])}* / "
        f"{fmt_kcal(r['weekly_target'])} target.",
        f"Remaining to goal: *{fmt_kcal(r['remaining_to_goal'])}* | "
        f"Planned ahead: {fmt_kcal(r['remaining_planned'])}.",
    ]
    # Surface missed workouts before the gap summary — they're often
    # the root cause of the gap, and the user wants to see them
    # explicitly per the 2026-05 spec ("if burn < 700 assume no
    # workout happened and recalibrate").
    if r.get("missed"):
        names = ", ".join(
            f"{m['weekday_name']} ({m['day_type_label']}, "
            f"{fmt_kcal(m['actual_burn'])})"
            for m in r["missed"])
        lines.append("")
        lines.append(
            f"*⚠️ Missed: {names}.* "
            f"Treating these as rest days — make-up picks below.")
    lines.append("")
    lines.append(r["summary"])
    if r["proposal"]:
        lines.append("")
        lines.append("*Suggested redistribution:*")
        for p in r["proposal"]:
            tag = "✓ clean" if p.get("gym_clean") else "⚠️ scale needed"
            lines.append(
                f"  • {p['weekday_name']}: {p['from']} → {p['to']} "
                f"(+{fmt_kcal(p['delta_kcal'])}, {tag})")
        # If any are scale-needed, suggest the tolerate command
        if any(not p.get("gym_clean") for p in r["proposal"]):
            lines.append("")
            lines.append(
                "_Some days have blacklisted movements (handstand, "
                "OH squat, snatch in strength, etc.). Either scale, "
                "or `tolerate <movement>` to widen picks._")
    return "\n".join(lines)


# ───────────────────────── Handlers ──────────────────────────
def handle_daily_burn(when: datetime) -> str:
    """Bare burn answer — explicitly NO goal-burn footer per user spec."""
    kcal = burn_for_date(when)
    label = when.strftime("%a %b %-d")
    if when.date() == datetime.now().date():
        return f"Today ({label}): *{fmt_kcal(kcal)}*."
    if when.date() == (datetime.now() - timedelta(days=1)).date():
        return f"Yesterday ({label}): *{fmt_kcal(kcal)}*."
    return f"{label}: *{fmt_kcal(kcal)}*."


def handle_weekly_remaining() -> str:
    """Prorated week view — actual burn vs the slice of week-target we
    SHOULD have burned by now.

    The pre-prorate version compared current burn to the *full* weekly
    target, which made every Monday morning look like a disaster (~0%
    of 6000) and every Saturday night look like a success (~85% of
    6000) — neither was actionable. Prorating against elapsed seconds
    since Monday 00:00 turns the same numbers into a useful pacing
    signal that's stable across the week.

    The literal substring "Remaining" must stay in the output —
    test_remaining_burn_lookup in the eval suite asserts that as the
    canary that this handler (not the LLM) answered the question.
    """
    now = datetime.now()
    burned, _ = burn_this_week()
    full_target = weekly_target()
    expected = _prorated_week_target(full_target, now=now)
    remaining = max(full_target - burned, 0.0)
    days_left = 7 - now.weekday()  # incl today, week ends Sun
    per_day = remaining / days_left if days_left else 0.0

    if expected > 0:
        pct = (burned / expected * 100)
        delta = burned - expected
        if delta >= 0:
            pace_line = (f"Actual: *{fmt_kcal(burned)}* / Expected so far: "
                         f"{fmt_kcal(expected)} ({pct:.0f}%, "
                         f"{fmt_kcal(delta)} ahead)")
        else:
            pace_line = (f"Actual: *{fmt_kcal(burned)}* / Expected so far: "
                         f"{fmt_kcal(expected)} ({pct:.0f}%, "
                         f"{fmt_kcal(-delta)} behind)")
    else:
        # Monday 00:00:00 edge — expected==0 means no pacing math yet.
        pace_line = (f"Actual: *{fmt_kcal(burned)}* / Expected so far: "
                     f"{fmt_kcal(0.0)} (week just started)")

    return (
        "Week so far\n"
        f"{pace_line}\n"
        f"Week target: {fmt_kcal(full_target)}\n"
        f"Remaining: *{fmt_kcal(remaining)}* over {days_left} day(s) "
        f"≈ {fmt_kcal(per_day)}/day."
    )


def handle_last_week() -> str:
    kcal, mon, sun = burn_last_week()
    target = weekly_target()
    pct = (kcal / target * 100) if target else 0
    return (
        f"Last week ({mon.strftime('%b %-d')}–{sun.strftime('%b %-d')}): "
        f"*{fmt_kcal(kcal)}* — {pct:.0f}% of {fmt_kcal(target)} target."
    )


def handle_split_target(workouts: int, rests: int,
                        target_override: float | None = None) -> str:
    """Distribute remaining kcal across N workout + M rest days.

    Caps workout days at the active tier's per-session ceiling; assumes rest
    days hit ~500 kcal of NEAT. Returns the per-workout target needed.
    """
    burned, _ = burn_this_week()
    target = target_override if target_override is not None else weekly_target()
    remaining = max(target - burned, 0.0)
    rest_credit = rests * TYPICAL_BURN["rest_passive"]
    workout_total = max(remaining - rest_credit, 0.0)

    if workouts <= 0:
        return (f"Need {fmt_kcal(remaining)} more this week with no workout days. "
                f"Rest days alone won't get there — consider adding a session.")
    per_workout = workout_total / workouts

    tier = state_get("recovery_tier", DEFAULT_TIER)
    cap = TIERS.get(tier, TIERS[DEFAULT_TIER])["cap"]
    realistic = per_workout <= cap

    out = [
        f"*Plan to hit {fmt_kcal(target)} this week*",
        f"Burned so far: {fmt_kcal(burned)}. Remaining: {fmt_kcal(remaining)}.",
        f"Rest days ({rests}) ≈ {fmt_kcal(rest_credit)} of NEAT.",
        f"Per workout day ({workouts}): *{fmt_kcal(per_workout)}*.",
    ]
    if not realistic:
        # Suggest a more reachable target instead of a pep talk.
        reachable = workouts * cap + rest_credit + burned
        out.append(
            f"\n⚠️ {fmt_kcal(per_workout)}/session is above your tier cap "
            f"({fmt_kcal(cap)}). Realistic week-end total: ~{fmt_kcal(reachable)}. "
            "Add a Z2 walk after each WOD to bridge the gap, or accept the lower total."
        )
    return "\n".join(out)


def handle_weighin_when() -> str:
    """Recommend when to step on the scale based on the last hammer session."""
    last = last_hammer_day()
    now = datetime.now()
    if last is None:
        return ("No hammer day in the last 14 days — your inflammation should be "
                "fully cleared. Weigh in tomorrow morning, fasted, post-bathroom.")
    hours_since = (now - last).total_seconds() / 3600
    if hours_since < 36:
        when = last + timedelta(hours=48)
        return (
            f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
            f"Inflammation peaks at 24–36h. Wait until *{when.strftime('%a %b %-d')}* "
            "morning to weigh in. Tonight: low sodium, 3L water, 7/15 breathing, "
            "dinner by 7pm."
        )
    if hours_since < 60:
        return (
            f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
            "Borderline — you're past inflammation peak but not fully flushed. "
            "Weigh tomorrow morning if you must; the 'whoosh' usually shows up "
            "12–24h later."
        )
    return (
        f"Last hammer: {last.strftime('%a %b %-d')} ({hours_since:.0f}h ago). "
        "You're in the truth window. Weigh tomorrow morning, fasted, post-bathroom."
    )


# hrv_band imported from protocols.py — pure-math, also re-exported for Coach/Bajrangi.


def handle_hrv(value: float) -> str:
    log_hrv(value)
    band, advice = hrv_band(value)
    return (
        f"HRV logged: *{value:.0f} ms* — {band}.\n"
        f"{advice}"
    )


def handle_breathing(kind: str = "7-15") -> str:
    if "box" in kind:
        return (
            "*Box breathing* (4 cycles, ~5 min):\n"
            "• Inhale 4s through the nose\n"
            "• Hold 4s (relaxed, no strain)\n"
            "• Exhale 4s through pursed lips\n"
            "• Hold 4s\n"
            "Repeat. Lower jaw, soft shoulders. Good for pre-meeting reset."
        )
    return (
        "*7/15 breathing* (10 min, lying down, legs elevated if possible):\n"
        "• Inhale 7s through the nose — belly rises, chest still\n"
        "• Exhale 15s through pursed lips, slow and steady\n"
        "• No hold — passive. Long exhale = vagal brake = HRV up.\n"
        "Cycle for 10 min. Skip if pressure builds in head/ears; "
        "drop to 5s in / 10s out instead."
    )


def handle_pre_fuel(minutes_to_workout: int = 75) -> str:
    """Pre-workout fueling ranked by how friendly they are to HRV."""
    if minutes_to_workout < 30:
        return (
            "*Too close to workout for solid food.* "
            "Sip 200ml water + electrolytes. If lightheaded, half a date or "
            "1 tsp honey for fast glucose. Save the meal for after."
        )
    return (
        f"*Pre-workout fuel ({minutes_to_workout} min out)* — easy on the gut, "
        "fast to clear, won't tank HRV:\n"
        "1. 2–3 dates (nature's gel)\n"
        "2. Half a jowar roti + thin honey\n"
        "3. Slate Ultra (30g protein, low sugar) — your 'protein bridge'\n"
        "4. Small handful of grapes + pinch of salt\n"
        "Avoid: nuts, heavy fat, big fiber load, full protein bar (digests slow).\n"
        "Hydration: 500ml water with electrolytes between now and start."
    )


def handle_post_recovery() -> str:
    return (
        "*Post-WOD recovery (15 min total)*\n"
        "• 5 min slow walk until HR < 100 bpm\n"
        "• Pigeon pose 2 min/side — releases glutes\n"
        "• Couch stretch 2 min/side — hip flexors + quads\n"
        "• Calf stretch 1 min/side — Achilles, ankle mobility\n"
        "• Foam roller thoracic 2 min — fixes the hunch\n"
        "• 5 min 7/15 breathing — vagal brake, HRV bounce\n"
        "Then: 500ml water + electrolytes, protein within 60 min."
    )


def handle_decision_run_or_wod() -> str:
    """Run vs WOD when both are on the table — favor the run for fat loss."""
    burned, _ = burn_this_week()
    target = weekly_target()
    remaining = max(target - burned, 0.0)
    return (
        f"*Run vs WOD — for fat loss, run wins.*\n"
        f"• Z2 10K: ~{fmt_kcal(TYPICAL_BURN['z2_10k'])} burn, low cortisol, "
        "low inflammation, supports HRV.\n"
        f"• CrossFit WOD: ~{fmt_kcal(TYPICAL_BURN['crossfit'])} burn, "
        "high cortisol, 24–36h water retention, 'looks heavy' on Mon scale.\n"
        f"Week status: {fmt_kcal(burned)} / {fmt_kcal(target)} "
        f"(remaining {fmt_kcal(remaining)}).\n"
        "Pick the run unless you specifically need the strength stimulus today."
    )


def handle_set_tier(tier: str) -> str:
    tier = tier.lower().strip().replace("-", "_")
    if tier not in TIERS:
        opts = ", ".join(TIERS.keys())
        return f"Unknown tier '{tier}'. Pick: {opts}."
    state_set("recovery_tier", tier)
    t = TIERS[tier]
    return (
        f"✅ Tier set to *{tier}*.\n"
        f"Weekly: {fmt_kcal(t['weekly'])} | Daily: {fmt_kcal(t['daily'])} | "
        f"Per-session cap: {fmt_kcal(t['cap'])}\n"
        f"_{t['note']}_"
    )


def handle_manual_burn(kcal: float, kind: str = "manual") -> str:
    log_workout(kind, kcal)
    today = burn_for_date(datetime.now())
    return f"✅ Logged *{fmt_kcal(kcal)}* ({kind}). Today total: {fmt_kcal(today)}."


def handle_fix_burn(day_token: str, kcal: float) -> str:
    """Overwrite the recorded active-burn for ONE calendar day this week.

    Use case: Apple Watch missed a workout, or the user double-counted
    a manual log, and the recorded burn for some day is wrong. This
    handler REWRITES the day rather than appending — it DELETEs every
    raw_vitals active-calories row and every workout_log row for that
    date, then INSERTs one corrected raw_vitals row at 23:59:59 so
    `burn_for_date(target) == kcal` exactly after the fix.

    Refuses:
      • Future dates (this-week-only; can't fix what hasn't happened)
      • kcal outside [0, 10000] (typo guard — nobody burned 50,000 kcal
        in a day; a value that large is a finger-slip, not data)

    Returns a confirmation line that names both the previous and new
    totals so the user can spot a misparse before the fix sticks.
    """
    # Parse the day token. Accept "Mon", "Monday", "MON", "tues", any
    # case. _WEEKDAY_LOOKUP is the canonical lowercase map from
    # protocols ({"mon": 0, "monday": 0, "tue": 1, "tues": 1, ...}).
    if not day_token:
        return "❌ Need a day token, e.g. `fix mon 581`."
    token = day_token.strip().lower()
    idx = _WEEKDAY_LOOKUP.get(token)
    if idx is None:
        return (f"❌ Couldn't parse day `{day_token}`. "
                "Try `fix mon 581` or `fix monday 581`.")

    # Out-of-range kcal: typo guard. 10,000 is well above the highest
    # daily burn ever seen (post-PRVN Murph day was ~3,200).
    if kcal < 0 or kcal > 10000:
        return (f"❌ {fmt_kcal(kcal)} is outside the sane range "
                f"[0, 10,000 kcal]. Looks like a typo — double-check "
                "the number and re-send.")

    # Target date = this week's Monday + idx. Reject future days.
    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    target = week_start + timedelta(days=idx)
    if target.date() > now.date():
        return (f"❌ {target.strftime('%a %b %-d')} is in the future. "
                "Can only fix days that have already happened.")

    # Capture the old total before we wipe — for the confirmation line.
    prev = burn_for_date(target)

    # DELETE + INSERT inside one txn so we can't end up half-applied.
    # Inline SQL on _db() rather than adding a helper to state.py —
    # this is a one-off destructive operation, doesn't belong in the
    # general state API surface.
    day_str = target.strftime("%Y-%m-%d")
    ts = f"{day_str} 23:59:59"
    con = _db()
    try:
        con.execute(
            "DELETE FROM raw_vitals "
            "WHERE metric_type='active_calories' "
            "AND substr(timestamp,1,10)=?",
            (day_str,))
        con.execute(
            "DELETE FROM workout_log "
            "WHERE substr(ts,1,10)=?",
            (day_str,))
        con.execute(
            "INSERT INTO raw_vitals (metric_type, value, timestamp) "
            "VALUES ('active_calories', ?, ?)",
            (float(kcal), ts))
        con.commit()
    finally:
        con.close()

    new_total = burn_for_date(target)
    label = target.strftime("%a (%b %-d)")
    return (f"✅ {label}: "
            f"{fmt_kcal(prev)} → {fmt_kcal(new_total)}")


# _eta_at_locked_rate, _locked_intake imported from protocols.py.


def handle_weight_timeline(target_lbs: float | None = None,
                           by_date: datetime | None = None) -> str:
    """Realistic timeline at the user's locked sustainable rate.

    Default (no args): shows ETAs for BOTH the 84 kg intermediate and
    80 kg final targets, plus the locked intake/deficit/active-burn numbers.
    Single target: shows that one. With a date: refuses faster-than-max
    rates and proposes a realistic alternative date.
    """
    current = latest_weight()
    deficit_locked = LOCKED_LOSS_LB_PER_WEEK * KCAL_PER_LB_FAT / 7   # 375
    intake_locked = _locked_intake()                                 # 2,600
    daily_active = WEEKLY_ACTIVE_TARGET_KCAL / 7                     # 857
    tdee = BMR_KCAL + daily_active                                   # 2,957

    def line(name: str, target: float) -> str:
        if current <= target:
            return f"  • {name} ({fmt_lbs(target)}): 🎯 *met*."
        eta = _eta_at_locked_rate(current - target)
        weeks = (current - target) / LOCKED_LOSS_LB_PER_WEEK
        return (f"  • {name} ({fmt_lbs(target)}): {weeks:.0f} weeks → "
                f"*{eta.strftime('%b %-d, %Y')}*")

    # Default: dual-target dashboard.
    if target_lbs is None and by_date is None:
        return "\n".join([
            "*Weight timeline*",
            f"Now: {fmt_lbs(current)}.",
            "",
            "*Targets at locked 0.75 lb/wk pace:*",
            line("84 kg intermediate", INTENT_INTERMEDIATE_LBS),
            line("80 kg final",        INTENT_TARGET_LBS),
            "",
            f"*Daily intake*: *{intake_locked:,} kcal* "
            f"(TDEE {tdee:,.0f} − {deficit_locked:.0f} kcal deficit).",
            f"*Weekly active burn*: *{WEEKLY_ACTIVE_TARGET_KCAL:,} kcal* "
            f"(scheduled {weekly_target():,.0f} from plan + ~"
            f"{WEEKLY_ACTIVE_TARGET_KCAL - int(weekly_target()):,} from daily walks/NEAT).",
            "_Targets auto-recalibrate every time you log a new weight._",
        ])

    # Single target with optional date.
    target = target_lbs if target_lbs is not None else TARGET_LBS
    if current <= target:
        return (f"You're at {fmt_lbs(current)} ≤ target {fmt_lbs(target)}. "
                "Goal already met.")
    lbs_to_lose = current - target
    eta_locked = _eta_at_locked_rate(lbs_to_lose)
    weeks_locked = lbs_to_lose / LOCKED_LOSS_LB_PER_WEEK

    out = [
        "*Weight timeline*",
        f"Now: {fmt_lbs(current)} → goal: {fmt_lbs(target)} "
        f"(lose {fmt_lbs(lbs_to_lose)}).",
        f"At locked {LOCKED_LOSS_LB_PER_WEEK} lb/wk: {weeks_locked:.0f} weeks → "
        f"*{eta_locked.strftime('%b %-d, %Y')}*",
        "",
        f"*Daily intake*: {intake_locked:,} kcal "
        f"(TDEE {tdee:,.0f} − {deficit_locked:.0f} deficit).",
        f"*Weekly active burn*: {WEEKLY_ACTIVE_TARGET_KCAL:,} kcal target.",
    ]

    if by_date is not None:
        weeks = max((by_date - datetime.now()).days / 7, 0.1)
        required_rate = lbs_to_lose / weeks
        out.append("")
        if required_rate <= MAX_LOSS_LB_PER_WEEK:
            deficit_needed = required_rate * KCAL_PER_LB_FAT / 7
            intake_needed = round((tdee - deficit_needed) / 50) * 50
            out.append(
                f"By {by_date.strftime('%b %-d, %Y')} ({weeks:.1f} weeks): "
                f"need {required_rate:.2f} lb/wk → "
                f"intake *{intake_needed:,} kcal/day* "
                f"(deficit {deficit_needed:.0f}/day)."
            )
        else:
            slip_days = max((eta_locked - by_date).days, 0)
            out.append(
                f"⚠️ {by_date.strftime('%b %-d, %Y')} would require "
                f"*{required_rate:.2f} lb/wk* — above your sustainable max "
                f"of {MAX_LOSS_LB_PER_WEEK} lb/wk."
            )
            out.append(
                f"Realistic ETA at {LOCKED_LOSS_LB_PER_WEEK} lb/wk: "
                f"*{eta_locked.strftime('%b %-d, %Y')}* "
                f"(~{slip_days} days past your stated date)."
            )
            out.append(
                f"At that pace: intake {intake_locked:,} kcal/day, "
                f"{WEEKLY_ACTIVE_TARGET_KCAL:,} weekly active."
            )

    return "\n".join(out)


def handle_current_weight() -> str:
    """Bare 'what's my current weight' lookup. Uses same watch-preferred
    logic as latest_weight(); shows source attribution."""
    con = _db()
    try:
        watch = con.execute("""
            SELECT value, substr(timestamp,1,10) AS day, timestamp
            FROM raw_vitals
            WHERE metric_type='weight'
            ORDER BY day DESC, timestamp DESC, rowid DESC
            LIMIT 1
        """).fetchone()
        manual = con.execute("""
            SELECT weight_lbs, substr(ts,1,10) AS day, ts
            FROM weighin_log
            ORDER BY day DESC, ts DESC, rowid DESC
            LIMIT 1
        """).fetchone()
    finally:
        con.close()

    if not watch and not manual:
        return "No weight logged yet. Try `wt: 198` to anchor."
    if watch and (not manual or watch[1] >= manual[1]):
        return (f"Current weight: *{fmt_lbs(watch[0])}* "
                f"(Apple Watch, {watch[2]}).")
    return (f"Current weight: *{fmt_lbs(manual[0])}* "
            f"(manual entry at {manual[2]}). "
            "_Watch hasn't synced a fresher reading yet._")


def handle_weight(val: float) -> str:
    sync_weight(val)   # logs + auto-recalibrates intent dates
    lines = [f"✅ Weight logged: {fmt_lbs(val)}."]
    con = _db()
    try:
        for kind, label_kg, target_lbs in [
            ("weight_intermediate_kg", "84 kg", INTENT_INTERMEDIATE_LBS),
            ("weight_kg",              "80 kg", INTENT_TARGET_LBS),
        ]:
            row = con.execute(
                "SELECT target_date, status FROM intents WHERE kind=? "
                "ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
            if not row:
                continue
            target_date, status = row
            if status == "met":
                lines.append(f"🎯 {label_kg} ({target_lbs:.1f} lbs) — *met*.")
            else:
                lines.append(f"🎯 {label_kg} ETA: {target_date}")
    finally:
        con.close()
    lines.append(f"_Recalibrated at {LOCKED_LOSS_LB_PER_WEEK} lb/wk locked rate._")
    return "\n".join(lines)


def handle_filter() -> str:
    days = parse_gym_plan()
    if not days:
        return "No gym plan found at workspace/gym-programming/weekly_plan.txt."
    lines = ["*Gym week — eligibility for your CrossFit slots:*"]
    for d in days:
        if d.blockers:
            lines.append(f"❌ {d.label} — skip ({', '.join(sorted(set(d.blockers)))})")
        else:
            lines.append(f"✅ {d.label} — eligible")
    return "\n".join(lines)


def handle_show_plan(next_week: bool = False) -> str:
    """Render the locked weekly cadence: 3 CF + 1 Z2 + 3 active-rest.
    Set `next_week=True` to render the upcoming Mon–Sun (uses gym schedule
    eligible days for this week — see note below if you want a different
    week's gym data)."""
    monday, _ = week_bounds()
    if next_week:
        monday = monday + timedelta(days=7)
    plan = current_plan(monday)
    plan_sum = sum(d["target_kcal"] for d in plan)
    weekly_total = weekly_target()
    neat = max(int(weekly_total - plan_sum), 0)
    today_idx = datetime.now().weekday()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    sun = monday + timedelta(days=6)

    header = "Next week" if next_week else "This week"
    week_key = monday.strftime("%Y-%m-%d")
    # Day-9 bug 1 fix (2026-05-17): derive is_fallback from CURRENT
    # parse_gym_plan() output, not from the stale
    # user_state.plan_fallback_{week_key} flag. Background:
    # replan_week() writes that flag at the moment it picks CF days,
    # snapshotting a TRANSIENT condition (gym not yet synced, or had
    # too many blocked days). The flag never gets re-evaluated, so
    # when the user runs the SugarWOD bookmarklet AFTER replan, the
    # flag stays "1" and handle_show_plan lies: "No gym plan synced"
    # — even though parse_gym_plan() now returns full bodies.
    #
    # The fix re-derives is_fallback from current gym data every
    # render. If the synced file has >=3 blacklist-clean CF weekdays,
    # we are NOT in fallback regardless of what the stale flag says.
    # The stale flag is kept as the third-fallback case (no synced
    # data AND no clean days computed → trust the historical signal).
    #
    # Pinned by tests/test_kobe_show_plan_fix.py.
    gym_days_for_warn = parse_gym_plan()
    has_synced_data = any((d.body or "").strip() for d in gym_days_for_warn)
    prefs_for_warn = get_prefs(monday)
    tolerated_for_warn = {
        normalize_blacklist_term(t)
        for t in prefs_for_warn["tolerated_blacklist"]
    }
    clean_wds: set[int] = set()
    for d in gym_days_for_warn:
        # parse_gym_plan returns weekday in upper case ('MON', 'TUE'…)
        # but WEEKDAY_INDEX has Title Case keys ('Mon', 'Tue'…). Normalize
        # via .capitalize() — 'MON' → 'Mon'. This is the 2026-05-17
        # production bug: every day silently failed the lookup, clean_wds
        # stayed empty, "Only 0 days blacklist-clean" warning fired and
        # the response fell back to the default Tue/Thu/Sat/Sun cadence
        # instead of showing the real synced SugarWOD plan.
        wd_idx = WEEKDAY_INDEX.get(d.weekday[:3].capitalize())
        if wd_idx is None:
            continue
        blocked = False
        for b in d.blockers:
            core = b.split(" (")[0]
            if normalize_blacklist_term(core) not in tolerated_for_warn:
                blocked = True
                break
        if not blocked:
            clean_wds.add(wd_idx)
    stale_fallback_flag = state_get(f"plan_fallback_{week_key}", "0") == "1"
    if has_synced_data and len(clean_wds) >= 3:
        # Gym IS synced and supports 3+ clean CF days — definitively
        # not fallback. Override any stale flag.
        is_fallback = False
    elif has_synced_data:
        # Gym synced but too few clean days. Surface the partial-clean
        # warning (not the misleading "no plan synced" one). is_fallback
        # is True so we enter the warning block, but the warning text
        # below branches on has_synced_data to pick the right message.
        is_fallback = True
    else:
        # No synced data at all. Trust the stale flag for the historical
        # signal — replan_week sets it to "1" only when it had to
        # backfill from defaults.
        is_fallback = stale_fallback_flag
    lines = [
        f"*{header} — {monday.strftime('%b %-d')} – {sun.strftime('%b %-d')}*",
        f"Tier `{tier}`, target *{fmt_kcal(weekly_total)}* "
        f"({fmt_kcal(plan_sum)} from plan + ~{fmt_kcal(neat)} NEAT).",
        "",
    ]
    if is_fallback:
        # has_synced_data picks the message: True → partial-clean
        # warning (the user CAN sync; the issue is blockers).
        # False → "no plan synced" (the user needs to run the
        # bookmarklet). Pre-Day-9 this branch was decided by
        # clean_picks counted against the committed (possibly stale)
        # plan — which gave the wrong message when the committed plan
        # was stale-fallback but the gym was now synced.
        if not has_synced_data:
            warning = ("_⚠️ No gym plan synced — using default "
                       "Mon/Wed/Fri cadence._")
            sub = ("_Sync via the SugarWOD bookmarklet for "
                   "blacklist-aware picks._")
        else:
            n_clean = len(clean_wds)
            warning = (
                f"_⚠️ Only {n_clean} day"
                f"{'s' if n_clean != 1 else ''} in this week's gym "
                f"plan are blacklist-clean — backfilled the rest from "
                f"default cadence._")
            sub = ("_Tolerate a movement to widen picks: `tolerate "
                   "muscle-up` or `tolerate handstand`._")
        lines.insert(1, warning)
        lines.insert(2, sub)
        lines.insert(3, "")
    # Detect missed workouts (past CF/Z2 days where burn < threshold).
    # We mark them in the plan view so the user sees reality, not what
    # was scheduled. Today is never marked (still in progress).
    if not next_week:
        missed = detect_missed_workouts(plan, today_idx, monday)
        missed_wds = {m["weekday"] for m in missed}
        # Surface missed-workout banner near the top of the plan
        if missed:
            names = ", ".join(m["weekday_name"] for m in missed)
            kcal_short = sum(m["shortfall"] for m in missed)
            banner = (f"_⚠️ {len(missed)} missed workout"
                      f"{'s' if len(missed) > 1 else ''}: {names} "
                      f"(~{fmt_kcal(kcal_short)} short of plan)._")
            # Insert banner above the day grid (after warnings if any)
            insert_at = 4 if is_fallback else 2
            lines.insert(insert_at, banner)
            lines.insert(insert_at + 1, "")
    else:
        missed_wds = set()

    for row in plan:
        wd = row["weekday"]
        is_today = (not next_week) and wd == today_idx
        marker = "▶" if is_today else " "
        name = WEEKDAY_NAME[wd]
        kind = DAY_TYPE_LABEL[row["day_type"]]
        gym = f" ({row['gym_label']})" if row["gym_label"] else ""
        if not next_week:
            actual = burn_for_date(monday + timedelta(days=wd))
            actual_s = f" — burned {fmt_kcal(actual)}" if actual > 0 else ""
        else:
            actual_s = ""
        # Past CF/Z2 day with burn below threshold: render as missed
        # so the user sees "this didn't happen" instead of pretending
        # the workout was done.
        if wd in missed_wds:
            kind = f"~~{kind}~~ ⚠️ missed"
        lines.append(
            f"{marker} {name}: {kind}{gym} → ideal "
            f"{fmt_kcal(row['target_kcal'])}{actual_s}")
    if not next_week:
        burned, _ = burn_this_week()
        lines.append(f"\nWeek so far: *{fmt_kcal(burned)}* / {fmt_kcal(weekly_total)}.")
    return "\n".join(lines)


def handle_next_week_target() -> str:
    """Active-burn target for the upcoming week, derived from the locked plan.

    Distinct from the daily *intake* target (~2400 kcal) — this is the active
    burn the Scientist owns. Computed from 3 CF + 1 Z2 + 3 active-rest.
    """
    monday, _ = week_bounds()
    next_mon = monday + timedelta(days=7)
    next_sun = next_mon + timedelta(days=6)
    plan = current_plan(next_mon)
    target = sum(d["target_kcal"] for d in plan)
    cf_n = sum(1 for d in plan if d["day_type"] == "cf")
    z2_n = sum(1 for d in plan if d["day_type"] == "z2")
    rest_n = sum(1 for d in plan if d["day_type"] == "rest")
    weight = latest_weight()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    cf_kcal = day_type_target("cf", tier)
    z2_kcal = day_type_target("z2", tier)
    rest_kcal = day_type_target("rest", tier)
    return (
        f"*Next week target — {next_mon.strftime('%b %-d')} – {next_sun.strftime('%b %-d')}*\n"
        f"Active-burn target: *{fmt_kcal(target)}*\n"
        f"  • {cf_n} × CrossFit @ {fmt_kcal(cf_kcal)} = {fmt_kcal(cf_n*cf_kcal)}\n"
        f"  • {z2_n} × Zone-2 10K @ {fmt_kcal(z2_kcal)} = {fmt_kcal(z2_n*z2_kcal)}\n"
        f"  • {rest_n} × active rest @ {fmt_kcal(rest_kcal)} = "
        f"{fmt_kcal(rest_n*rest_kcal)}\n"
        f"Tier `{tier}`. Current weight {weight:.1f} lbs → 80 kg North Star.\n"
        f"Use `show plan next week` to see day-by-day."
    )


def handle_replan() -> str:
    """Force-rebuild this week's plan from the current gym schedule."""
    monday, _ = week_bounds()
    replan_week(monday, force=True)
    return "🔄 Plan rebuilt for this week.\n\n" + handle_show_plan()


def _which_monday(next_week: bool) -> tuple[datetime, str]:
    monday, _ = week_bounds()
    if next_week:
        monday = monday + timedelta(days=7)
    return monday, ("next week" if next_week else "this week")


def handle_unavailable(weekday_text: str, next_week: bool = False) -> str:
    """Mark one or more weekdays as unavailable; replan picks the next-best
    day automatically. Generic — works for any weekday, any week.

    If the message contains both an explicit named weekday AND 'today'/
    'tomorrow', use only the named day(s) — the relative reference is
    almost always part of a different clause ('I can't make Thursday,
    can I work out today?' — only Thursday should be marked off)."""
    monday, label = _which_monday(next_week)
    named = parse_weekdays(weekday_text, include_relative=False)
    indices = named if named else parse_weekdays(weekday_text)
    if not indices:
        return ("Couldn't find a weekday in that. Try: "
                "'I can't make Wednesday' or 'skip Thursday next week'.")
    prefs = get_prefs(monday)
    merged = sorted(set(prefs["unavailable_days"]) | set(indices))
    set_prefs(monday, unavailable_days=merged)
    replan_week(monday, force=True)
    names = ", ".join(WEEKDAY_NAME[i] for i in indices)
    return (f"✅ Marked {names} unavailable {label}. Replanned.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_pick_days(weekday_text: str, next_week: bool = False) -> str:
    """Explicit day picks. If the message mentions 'run'/'z2'/'zone 2', that
    set goes to Z2; otherwise everything goes to CF. Mixed phrasing splits
    into both buckets when both keywords appear."""
    monday, label = _which_monday(next_week)
    text_lc = weekday_text.lower()
    indices = parse_weekdays(weekday_text)
    if not indices:
        return "Couldn't find any weekdays in that."

    # Split into CF vs Z2 buckets based on phrasing context.
    z2_kw = re.search(r"\b(run|z2|zone\s*2|10k|zone-2|easy run)\b", text_lc)
    cf_kw = re.search(r"\b(crossfit|cf|wod|workout|lift|class)\b", text_lc)

    cf_picks: list[int] = []
    z2_pick: int | None = None
    if z2_kw and cf_kw:
        # Mixed phrasing: split at the z2 keyword. Days before → CF, days
        # at/after → Z2. Handles "Mon Tue Fri for CF, Sun for run".
        split_at = z2_kw.start()
        cf_part = parse_weekdays(weekday_text[:split_at])
        z2_part = parse_weekdays(weekday_text[split_at:])
        cf_picks = cf_part
        z2_pick = z2_part[0] if z2_part else None
    elif z2_kw:
        # All days are Z2 candidates — first one wins.
        z2_pick = indices[0]
    elif cf_kw:
        # User explicitly said "for crossfit/CF" → all listed days are CF.
        # No auto-split into Z2 even if they listed 4+ days; respect what
        # they said.
        cf_picks = indices
    else:
        # Ambiguous (no keyword). 4+ days → last is Z2 so the cadence still
        # comes out at 3 CF + 1 Z2; 3 or fewer → all CF.
        if len(indices) >= 4:
            cf_picks = indices[:-1]
            z2_pick = indices[-1]
        else:
            cf_picks = indices[:3]

    set_prefs(monday, forced_cf_days=cf_picks, forced_z2_day=z2_pick)
    replan_week(monday, force=True)

    parts = []
    if cf_picks:
        parts.append("CF: " + ", ".join(WEEKDAY_NAME[i] for i in cf_picks))
    if z2_pick is not None:
        parts.append(f"Z2: {WEEKDAY_NAME[z2_pick]}")
    return (f"✅ Locked picks for {label} → {' | '.join(parts)}.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_tolerate(term_text: str, next_week: bool = False) -> str:
    """Add blacklist terms to this week's tolerance list (e.g., 'I'll scale
    muscle-ups this week')."""
    monday, label = _which_monday(next_week)
    matches = re.findall(
        r"\b(muscle[\s-]?ups?|muscleups?|partner|handstand(?:s)?|hspu|"
        r"overhead\s+squats?|ohs|snatch(?:es)?)\b", term_text, re.I)
    if not matches:
        return ("Couldn't find a blacklist term to tolerate. "
                "Options: muscle-up, partner, handstand, OHS, snatch.")
    norm = sorted({normalize_blacklist_term(m) for m in matches})
    prefs = get_prefs(monday)
    merged = sorted(set(prefs["tolerated_blacklist"]) | set(norm))
    set_prefs(monday, tolerated_blacklist=merged)
    replan_week(monday, force=True)
    return (f"✅ Tolerating {', '.join(norm)} for {label}. Replanned.\n\n"
            + handle_show_plan(next_week=next_week))


def handle_clear_prefs(next_week: bool = False) -> str:
    monday, label = _which_monday(next_week)
    clear_prefs(monday)
    replan_week(monday, force=True)
    return (f"✅ Cleared all overrides for {label}. Plan reverts to "
            "auto-picker.\n\n" + handle_show_plan(next_week=next_week))


# ─── Dislike capture (user-stated negative movement prefs) ──────────
# The complement to handle_tolerate. Where tolerate ADDS a blacklisted
# movement back ("I'll scale handstand this week"), dislike REMOVES a
# movement from suggestions ("don't put deadlifts in my week"). Stored
# in core/memory substrate as type='dislike', scope-aware
# (today/week/always). See agents/the_scientist/dislikes.py for the
# storage model and ADR-003 for why the substrate (not week_preferences)
# is the right home.
def handle_dislike_movement(movement: str, scope: str = "week",
                            rationale: str | None = None) -> str:
    """Persist 'I don't want <movement> [today|this week|always]'.
    Triggers a replan so the new dislike takes effect immediately."""
    from agents.the_scientist import dislikes as _dl
    try:
        _dl.add(movement, scope, rationale=rationale)
    except ValueError as e:
        return f"❌ Couldn't record dislike: {e}"
    monday, _ = _which_monday(False)
    replan_week(monday, force=True)
    scope_label = {
        "today":  "today only",
        "week":   "this week",
        "always": "permanently",
    }.get(scope, scope)
    rat = f" ({rationale})" if rationale else ""
    return (f"✅ Got it — skipping *{movement.lower()}* {scope_label}{rat}. "
            f"Plan re-picked.\n\n" + handle_show_plan(next_week=False))


def handle_drop_dislike(movement: str) -> str:
    """Reverse a prior dislike — 'actually I can do deadlifts again'."""
    from agents.the_scientist import dislikes as _dl
    n = _dl.drop(movement)
    if n == 0:
        return (f"No active dislike for *{movement.lower()}* — nothing "
                f"to drop. Use `dislikes` to see what's currently set.")
    monday, _ = _which_monday(False)
    replan_week(monday, force=True)
    return (f"✅ Cleared *{n}* dislike entr{'y' if n == 1 else 'ies'} "
            f"for *{movement.lower()}*. Plan re-picked.\n\n"
            + handle_show_plan(next_week=False))


def handle_list_dislikes() -> str:
    """Show every currently-active dislike. Useful for `dislikes` /
    `what am I skipping` / `/dislikes` queries."""
    from agents.the_scientist import dislikes as _dl
    rows = _dl.active_movements()
    if not rows:
        return ("No active dislikes. Say 'no deadlifts today' / 'skip "
                "burpees this week' / 'never suggest rowing' to add one.")
    by_scope: dict[str, list[str]] = {"today": [], "week": [], "always": []}
    for r in rows:
        scope = r["scope"]
        line = r["movement"]
        if r.get("rationale"):
            line += f" ({r['rationale']})"
        by_scope.setdefault(scope, []).append(line)
    parts = ["*Active dislikes:*"]
    for scope, label in (("today", "Today"),
                         ("week", "This week"),
                         ("always", "Permanent")):
        if by_scope.get(scope):
            parts.append(f"  • {label}: {', '.join(sorted(by_scope[scope]))}")
    return "\n".join(parts)


def handle_swap(text: str, next_week: bool = False) -> str:
    """Swap one workout day for another. Handles many phrasings:
      • 'I'd prefer Monday over Sunday'
      • 'swap Sunday for Monday'  /  'switch Sunday to Monday'
      • 'use Monday instead of Sunday'
      • 'move Sunday's session to Monday'
      • 'Monday rather than Sunday'  /  'Monday as opposed to Sunday'
    Adds the new day to forced_cf_days, marks the old day unavailable,
    so the locked 3 CF + 1 Z2 cadence is preserved."""
    monday, label = _which_monday(next_week)
    days = parse_weekdays(text)
    if len(days) < 2:
        return ("Need two weekdays to swap. Try: "
                "'swap Sunday for Monday' or 'I'd prefer Mon over Sun'.")

    text_lc = text.lower()
    # Direction depends on phrasing.
    # In "swap/switch/move A to B": A is OLD (removed), B is NEW (added).
    # In "prefer A over B" / "A instead of B": A is NEW, B is OLD.
    if re.search(r"\b(swap|switch|move|change)\b", text_lc):
        old_idx, new_idx = days[0], days[1]
    else:
        new_idx, old_idx = days[0], days[1]

    prefs = get_prefs(monday)
    # If forced_cf_days is empty, materialize the current plan's auto-picks
    # as the baseline first — otherwise the swap collapses the plan to a
    # single day instead of just substituting one slot.
    forced = list(prefs["forced_cf_days"])
    if not forced:
        forced = [r["weekday"] for r in current_plan(monday)
                  if r["day_type"] == "cf"]

    # Apply the swap on top of the baseline.
    if old_idx in forced:
        forced.remove(old_idx)
    if new_idx not in forced:
        forced.append(new_idx)

    new_unavail = sorted(
        (set(prefs["unavailable_days"]) - {new_idx}) | {old_idx})
    set_prefs(monday, unavailable_days=new_unavail,
              forced_cf_days=sorted(forced))
    replan_week(monday, force=True)
    return (
        f"✅ Swapped: {WEEKDAY_NAME[old_idx]} → {WEEKDAY_NAME[new_idx]} "
        f"({label}). Replanned.\n\n"
        + handle_show_plan(next_week=next_week))


def handle_scheduling_help() -> str:
    """Shown when a message has weekday tokens + workout intent but doesn't
    match any specific handler. Beats the LLM coach hallucinating fictional
    plans (Z2 every day, 5 CF days, etc.)."""
    return (
        "Tell me what to change and I'll replan around the locked 3 CF + 1 Z2 "
        "cadence:\n"
        "• `swap Sunday for Monday` — swap workout days\n"
        "• `I can't make Thursday` — drop a day, auto-pick a replacement\n"
        "• `pick Mon Tue Fri Sun for crossfit` — explicit picks\n"
        "• `I'm fine with muscle-ups this week` — tolerate a blacklisted move\n"
        "• `show plan` — see this week's grid\n"
        "• `clear preferences` — reset all overrides for this week"
    )


def _prorated_day_target(full_target: float,
                         now: datetime | None = None) -> float:
    """Linear prorate of a day's full target across the active waking
    window NUDGE_HOURLY_START..NUDGE_HOURLY_END (inclusive of both
    endpoints — span = END - START + 1 hours).

    • Before the window opens → 0.0 (don't shame anyone for not
      having burned anything by 9am)
    • Inside the window → full_target * (elapsed_hours / span)
    • After the window closes → full_target (clamped, no overshoot)

    `elapsed` is clamped to [1, span] so the at-window-open boundary
    returns full_target/span (not 0) and the at-window-close boundary
    returns exactly full_target (not 11/11 = 100% which is correct,
    but also not 12/11 = 109% which would be wrong).

    Why a fixed window rather than wall-clock 24h: the user's
    behavioral envelope is the waking window. Diluting their pacing
    expectation across sleeping hours produces an artificially low bar
    that's useless for nudging.
    """
    n = now or datetime.now()
    if n.hour < NUDGE_HOURLY_START:
        return 0.0
    if n.hour > NUDGE_HOURLY_END:
        return float(full_target)
    span = NUDGE_HOURLY_END - NUDGE_HOURLY_START + 1
    elapsed_raw = n.hour - NUDGE_HOURLY_START + 1
    elapsed = max(1, min(elapsed_raw, span))
    return float(full_target) * (elapsed / span)


def _prorated_week_target(full_target: float,
                          now: datetime | None = None) -> float:
    """Linear prorate of a weekly target across elapsed seconds from
    this week's Monday 00:00:00 to next Monday 00:00:00.

    Clamped to [0, full_target] — Monday before midnight returns 0,
    Sunday 23:59:59 returns ~full_target, never overshoots.

    Seconds-granular (not days-granular) so the pacing line updates
    smoothly through the day rather than jumping at midnight.
    """
    n = now or datetime.now()
    # Monday 00:00:00 of THIS week. weekday() returns 0 for Monday.
    week_start = (n - timedelta(days=n.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    elapsed_sec = (n - week_start).total_seconds()
    week_sec = 7 * 24 * 3600
    frac = max(0.0, min(elapsed_sec / week_sec, 1.0))
    return float(full_target) * frac


def handle_pace() -> str:
    """Today's burn vs prorated day-type target.

    Pre-prorate version compared current burn to the *full* day target
    regardless of time-of-day. That meant 9am 0/600 looked the same as
    9pm 0/600 — both reported as failing, neither actionable. With
    prorating the user sees "you're 80% of where you should be by 2pm"
    instead of "you're 30% of where you'd be at end-of-day", which is
    the actual coaching signal.

    The /week line was dropped here — `/week` covers it explicitly
    and stacking the two views in one response inflated the message
    length past the Telegram-ergonomic threshold.
    """
    now = datetime.now()
    plan_row = today_plan()
    full_target = plan_row["target_kcal"]
    actual = burn_for_date(now)
    kind = DAY_TYPE_LABEL[plan_row["day_type"]]
    gym = f"({plan_row['gym_label']})" if plan_row["gym_label"] else ""
    header = f"Today — {kind}{gym}".rstrip()

    if full_target == 0:
        # Rest day with no target — just report the actual burn.
        return (f"{header}\n"
                f"Actual: *{fmt_kcal(actual)}* / no target today.")

    if now.hour < NUDGE_HOURLY_START:
        # Pre-window: prorate is 0, but we still want to show the day
        # target so the user knows what they're aiming at.
        return (f"{header}\n"
                f"Actual: *{fmt_kcal(actual)}* (window starts "
                f"{NUDGE_HOURLY_START:02d}:00)\n"
                f"Day target: {fmt_kcal(full_target)}")

    expected = _prorated_day_target(full_target, now=now)
    pct = (actual / expected * 100) if expected > 0 else 0
    delta = actual - expected
    if delta >= 0:
        pace_line = (f"Actual: *{fmt_kcal(actual)}* / Expected so far: "
                     f"{fmt_kcal(expected)} ({pct:.0f}%, "
                     f"{fmt_kcal(delta)} ahead)")
    else:
        pace_line = (f"Actual: *{fmt_kcal(actual)}* / Expected so far: "
                     f"{fmt_kcal(expected)} ({pct:.0f}%, "
                     f"{fmt_kcal(-delta)} short)")
    return (f"{header}\n"
            f"{pace_line}\n"
            f"Day target: {fmt_kcal(full_target)}")


def handle_today_target() -> str:
    row = today_plan()
    kind = DAY_TYPE_LABEL[row["day_type"]]
    gym = f" ({row['gym_label']})" if row["gym_label"] else ""
    return (f"Today is *{kind}*{gym}. Ideal active burn: "
            f"*{fmt_kcal(row['target_kcal'])}*.")


def _extract_wod_summary(body: str, max_workouts: int = 2) -> str:
    """Pull the strength + main metcon out of a SugarWOD day body, skipping
    Levels / Reset / Accessory / Optional / Primer / Warm-up sections.
    Returns a short multi-line summary suitable for a Telegram message."""
    chunks = re.split(r"^0 results\s*$", body, flags=re.MULTILINE)
    important = []
    skip_titles_re = re.compile(
        r"^(?:\[|.*\b(?:Level|Reset|Accessor|Optional|Primer|Warm.?up)\b)",
        re.I)
    for chunk in chunks:
        lines = [l.rstrip() for l in chunk.splitlines() if l.strip() not in ("", "0")]
        if not lines:
            continue
        title = lines[0].lstrip(" ").strip()
        if skip_titles_re.match(title):
            continue
        body_text = "\n".join(lines[1:]).strip()
        # Trim long descriptions to keep the message scannable.
        if len(body_text) > 280:
            body_text = body_text[:280].rstrip() + "..."
        important.append((title, body_text))
        if len(important) >= max_workouts:
            break
    if not important:
        return ""
    return "\n\n".join(f"*{t}*\n{b}" for t, b in important)


def handle_workout_on(idx: int) -> str:
    """Return the planned workout (day-type + WOD content) for an arbitrary
    weekday this week. Used by 'what am I doing on Friday', 'Saturday's
    workout', etc. — distinct from handle_workout_today which only does
    today/tomorrow."""
    plan = current_plan()
    row = plan[idx]
    name = WEEKDAY_NAME[idx]
    kind = DAY_TYPE_LABEL[row["day_type"]]
    target = row["target_kcal"]

    if row["day_type"] == "rest":
        return (f"*{name}: {kind}* — no scheduled workout.\n"
                f"Target ~{fmt_kcal(target)} from walks, toddler time, "
                "or a 10-min mobility flush.")
    if row["day_type"] == "z2":
        return (f"*{name}: Zone-2 10K run* — target ~{fmt_kcal(target)}.\n"
                "Nasal breathing only, conversational pace. "
                "Save the intensity for CF days.")

    # CF day — pull the actual WOD from the gym schedule.
    gym_label = row.get("gym_label")
    summary = ""
    if gym_label:
        gym_days = parse_gym_plan()
        match = next((d for d in gym_days if d.label == gym_label), None)
        if match:
            summary = _extract_wod_summary(match.body)

    header = (f"*{name}: CrossFit ({gym_label})* — target "
              f"~{fmt_kcal(target)}\n") if gym_label else (
              f"*{name}: CrossFit* — target ~{fmt_kcal(target)}\n")
    body = summary or "_(WOD details not available — check SugarWOD app)_"
    return header + "\n" + body


def handle_workout_today(when: str = "today") -> str:
    """Yes/no answer for 'am I working out today' (or tomorrow). Distinct
    from today_target (returns kcal number) and daily burn (returns
    burn-so-far). This says what *kind* of day it is."""
    plan = current_plan()
    if when == "tomorrow":
        idx = (datetime.now().weekday() + 1) % 7
        when_label = "tomorrow"
    else:
        idx = datetime.now().weekday()
        when_label = "today"
    row = plan[idx]
    kind = DAY_TYPE_LABEL[row["day_type"]]
    gym = f" ({row['gym_label']})" if row["gym_label"] else ""
    if row["day_type"] == "rest":
        return (f"*No workout {when_label}.* {kind} day — keep it light. "
                f"Target ~{fmt_kcal(row['target_kcal'])} from walks, toddler "
                "time, or a 10-min mobility flush.")
    if row["day_type"] == "z2":
        return (f"*Yes — Zone-2 10K run {when_label}.* "
                f"Target ~{fmt_kcal(row['target_kcal'])}. "
                "Nasal breathing only, conversational pace.")
    return (f"*Yes — CrossFit{gym}* {when_label}. "
            f"Target ~{fmt_kcal(row['target_kcal'])}. "
            "Get to the gym; bridge with a 20-min walk after if you can.")


def handle_next_workout(kind_filter: str = "any") -> str:
    """'When is my next CrossFit session?' / 'next workout?' / 'next run?'

    kind_filter:
        "cf"  — only look for CF days
        "z2"  — only look for the Z2 day
        "any" — any non-rest day (CF or Z2)

    Walks forward from today through the current weekly_plan. If the
    target day type is today, says so. If not, walks Tue→Sun then wraps.
    Surfaces the gym pick + WOD details when available.

    Production bug 2026-05: this question used to fall through to the LLM
    which gave a generic 'use these commands' response instead of looking
    at the actual plan. Now it consults the plan directly.
    """
    plan = current_plan()
    today_idx = datetime.now().weekday()

    target_kinds: tuple[str, ...]
    label_text: str
    if kind_filter == "cf":
        target_kinds = ("cf",)
        label_text = "CrossFit"
    elif kind_filter == "z2":
        target_kinds = ("z2",)
        label_text = "Zone-2 run"
    else:
        target_kinds = ("cf", "z2")
        label_text = "workout"

    # Walk forward starting today, then wrap around to before-today.
    order = [(today_idx + i) % 7 for i in range(7)]
    for offset, wd in enumerate(order):
        row = plan[wd]
        if row["day_type"] not in target_kinds:
            continue
        kind_lbl = DAY_TYPE_LABEL[row["day_type"]]
        gym = f" ({row['gym_label']})" if row["gym_label"] else ""
        if offset == 0:
            when = "today"
        elif offset == 1:
            when = "tomorrow"
        else:
            when = WEEKDAY_NAME[wd]
        # If it's a CF day with a gym pick, surface the WOD details.
        if row["day_type"] == "cf" and row["gym_label"]:
            try:
                summary = _extract_wod_summary(
                    next((d.body for d in parse_gym_plan()
                          if d.weekday[:3] == WEEKDAY_NAME[wd]), ""),
                    max_workouts=2)
            except Exception:
                summary = ""
            extra = f"\n\n{summary}" if summary else ""
        else:
            extra = ""
        return (f"*Next {label_text}: {when}* — {kind_lbl}{gym}.\n"
                f"Target ~{fmt_kcal(row['target_kcal'])}." + extra)
    return (f"No {label_text} scheduled this week. Try `replan` to "
            "refresh, or `show plan` to see the current week.")


# Keep `handle_schedule` as an alias for back-compat with old chat shortcuts.
handle_schedule = handle_show_plan


# ───────────────────────── Intent router ──────────────────────
WEIGHT_RE   = re.compile(r"\b(?:weight|wt)[:\s]+(\d+\.?\d*)", re.I)
WEIGHT_QUERY_RE = re.compile(
    r"\b(what.?s\s+my\s+(?:current\s+)?weight|"
    r"current\s+weight|"
    r"how\s+much\s+do\s+I\s+weigh|"
    r"weight\s+now|latest\s+weight)\b", re.I)
TODAY_RE    = re.compile(r"\b(today|now)\b", re.I)
YEST_RE     = re.compile(r"\byesterday\b", re.I)
LASTWK_RE   = re.compile(r"\blast\s+week\b", re.I)
# "calories this week" / "burn this week" / "how many ... this week" — bare lookup
THIS_WEEK_RE = re.compile(
    r"\b(?:(?:calories?|cal|kcal|active|burn(?:ed|t)?).*\bthis\s+week|"
    r"this\s+week.*(?:calories?|cal|kcal|active|burn(?:ed|t)?)|"
    r"how\s+many.*this\s+week)\b", re.I)
# "caloric target for next week" / "target next week" / "weekly target"
NEXT_WEEK_TARGET_RE = re.compile(
    r"\b(?:next\s+week.*(?:target|goal|burn|calories?|kcal)|"
    r"(?:target|goal|burn|calories?|kcal).*next\s+week|"
    r"calorie?ic?\s+target|weekly\s+target|"
    r"target\s+for\s+(?:the\s+)?(?:week|next\s+week))\b", re.I)
# Scheduling questions that should return the weekly plan grid:
#   • "which days am I working out [this week / next week / rest of week]?"
#   • "when am I running next?"
#   • "what days should I crossfit?"
#   • "when do I CF?"
#   • "how about rest of the week?"  (no calorie keyword present)
PLAN_DAYS_RE = re.compile(
    r"(?:"
    # interrogative + day-noun
    r"\b(?:which\s+days?|what\s+days?|when\s+(?:do|am|will)\s+i|"
    r"when\s+should\s+i|days?\s+should\s+i|next\s+(?:run|crossfit|cf|wod)|"
    r"how\s+about\s+(?:the\s+)?rest|rest\s+of\s+(?:the\s+)?week)\b"
    r"[\s\S]{0,80}?"
    r"\b(?:crossfit|cf|wod|run(?:ning)?|rest|recover|zone\s*2|z2|"
    r"workout|working\s*out|workouts|next)\b"
    r"|"
    # "am I working out (this week|rest of week|next week)" without 'today'
    r"\bam\s+i\s+(?:working\s*out|running|doing\s+(?:crossfit|cf|wod))\b"
    r"[\s\S]{0,60}?"
    r"\b(?:this\s+week|next\s+week|rest\s+of\s+(?:the\s+)?week|"
    r"week|days?|when)\b"
    r"|"
    # standalone "rest of the week" / "how about rest" → user is asking for
    # the schedule continuation, not a kcal remainder (REMAIN_RE handles
    # the kcal version when calorie keywords are present)
    r"\b(?:rest\s+of\s+(?:the\s+)?week|how\s+about\s+(?:the\s+)?(?:rest|week))\b"
    r")", re.I)
NEXT_WEEK_RE = re.compile(r"\bnext\s+week\b", re.I)
# REMAIN_RE — kcal-remainder questions. Requires either an explicit calorie
# keyword OR the high-confidence phrasing "left/remaining for the week".
REMAIN_RE = re.compile(
    r"(?:"
    r"\b(?:remain|left|remaining)[\s\S]{0,30}?"
    r"\b(?:calories?|kcal|active|burn(?:ed|t)?|deficit)\b"
    r"|"
    r"\b(?:calories?|kcal|active|burn(?:ed|t)?|deficit)[\s\S]{0,30}?"
    r"\b(?:remain|left|remaining)\b"
    r"|"
    r"\brest\s+of\s+(?:the\s+)?week[\s\S]{0,40}?"
    r"\b(?:calories?|kcal|active|burn|target|goal|deficit)\b"
    r"|"
    r"\b(?:calories?|kcal|active|burn|deficit)[\s\S]{0,30}?"
    r"\brest\s+of\s+(?:the\s+)?week\b"
    r"|"
    r"\bweek[\s\S]{0,20}?\b(?:target|goal)\b"
    r"|"
    # "how much left/remaining for the week" — high-confidence kcal intent
    # in this agent's context even without an explicit calorie keyword.
    r"\b(?:how\s+much|what'?s)\s+(?:do\s+i\s+have\s+)?(?:left|remaining)\s+"
    r"(?:for\s+)?(?:the\s+)?week\b"
    r")", re.I)
SCHED_RE    = re.compile(
    r"\b(?:schedule|structure|"
    r"plan\s+(?:my|the|for|this|next)?\s*(?:week|workout)?|"
    r"show\s+(?:me\s+)?(?:the\s+)?(?:next\s+week.?s?|this\s+week.?s?|weekly)?\s*plan|"
    r"(?:next|this)\s+week.{0,5}plan|"
    r"3\s?cross|crossfit.{0,10}(?:week|plan))\b", re.I)
FILTER_RE   = re.compile(r"\b(filter|which days|eligible|skip|partner|handstand|muscle ?up|overhead squat|snatch in)\b", re.I)
REPLAN_RE   = re.compile(r"\b(replan|rebuild plan|reset plan|new plan)\b", re.I)
PACE_RE     = re.compile(r"\b(pace|on track|how am I doing|status)\b", re.I)
# "Catch-up" / recalibration intent — when the user wants Miya to look
# at where they are vs the weekly target and propose a redistribution.
# Distinct from PACE_RE (which just shows current numbers) — this one
# proposes new picks.
RECALIBRATE_RE = re.compile(
    r"(?:"
    r"\bcatch[\s-]?up\b|\bcatching\s+up\b|"
    r"\bbehind\s+(?:on|in)\s+(?:calories?|kcal|cal|burn|target|goal|pace)\b|"
    r"\bhow\s+do\s+i\s+(?:catch|hit|reach)\b|"
    r"\bhow\s+(?:can|should)\s+i\s+(?:get\s+to|hit|reach)\s+(?:my\s+)?"
    r"(?:weekly|week.?s?)\s+(?:target|goal)\b|"
    r"\bwhat\s+(?:should|can)\s+i\s+do\s+(?:this|the\s+rest\s+of\s+the)\s+week\b|"
    r"\brecalibrate\b|\bredistribute\b|"
    r"\brecommend\s+(?:days?|workouts?|schedule)\b|"
    r"\bplan\s+(?:my\s+)?week\s+to\s+(?:hit|catch)\b|"
    r"\badjust\s+(?:the\s+|my\s+)?(?:plan|schedule|week)\b"
    r")", re.I)
# Per-week prefs. UNAVAILABLE_RE catches "can't make X" / "skip X" / "out X".
UNAVAILABLE_RE = re.compile(
    r"\b(can(?:'?t| ?not)|cannot|won'?t|skip(?:ping)?|miss(?:ing)?|"
    r"out\s+(?:on|for)|busy|unavailable|no\s+gym)\b", re.I)
# PICK_RE catches "pick X Y Z for crossfit" / "do CF on X Y Z" / "I'll do X Y Z".
PICK_RE = re.compile(
    r"\b(pick|do (?:crossfit|cf|wod) on|crossfit on|cf on|"
    r"i(?:'?ll| will| want to) do.*?(?:crossfit|cf|wod|run|z2|zone\s*2))\b", re.I)
TOLERATE_RE = re.compile(
    r"\b(scale|tolerate|fine\s+with|ok(?:ay)?\s+with|ignore|allow|"
    r"let me do|i can do|i'?ll do|i can scale|happy to (?:scale|do)|"
    r"i'?m fine|will do)\b"
    r"[\s\S]*?\b(muscle[\s-]?ups?|muscleups?|partner|handstand(?:s)?|hspu|"
    r"overhead\s+squats?|ohs|snatch(?:es)?)\b", re.I)

# ─── Dislike capture — "skip X today / no X this week / never X" ───
# Three regexes by intent:
#   DISLIKE_RE     — user states a new negative preference
#   DROP_DISLIKE_RE — user takes one back ('actually I can do X')
#   LIST_DISLIKES_RE — user asks what's currently being skipped
#
# Movement vocabulary intentionally OPEN — anything reasonable matches
# (deadlift, burpee, rowing, anything custom). Validation happens at
# the storage layer (dislikes._normalize_movement). Compare with
# TOLERATE_RE which is restricted to the BLACKLIST vocabulary.
#
# Scope detection: 'today' / 'this week' (default) / 'ever|always|
# permanently|forever'. Default to 'week' so a bare "no deadlifts"
# doesn't accidentally become forever.
_MOVEMENT_TOKEN = (
    r"[a-z][a-z\s\-]{1,29}?"           # lowercase phrase 2–30 chars
)
DISLIKE_RE = re.compile(
    r"\b(?:no|skip|don'?t (?:want|do|suggest|put)|"
    r"i don'?t want|stop suggesting|avoid|drop|never (?:suggest|put|do))\b"
    r"\s+(" + _MOVEMENT_TOKEN + r")"
    r"(?:\s+(today|this\s+week|next\s+week|"
    r"ever|always|permanently|forever))?", re.I)

DROP_DISLIKE_RE = re.compile(
    r"\b(?:i\s+can|actually\s+i\s+can|bring\s+back|allow|re[\s-]?enable|"
    r"un[\s-]?skip|let\s+me\s+do)\s+(" + _MOVEMENT_TOKEN + r")"
    # Trailing "again|back|now" is optional and so is the whitespace
    # before it. Without `(?:\s+(?:again|back|now))?` the regex
    # required trailing whitespace even when the message ended right
    # after the movement ("bring back rowing").
    r"(?:\s+(?:again|back|now))?\b", re.I)

LIST_DISLIKES_RE = re.compile(
    r"\b(?:dislikes?|what\s+am\s+i\s+skipping|"
    r"what'?s\s+(?:on\s+the\s+)?skip\s+list|skip\s+list)\b", re.I)
CLEAR_PREFS_RE = re.compile(
    r"\b(clear (?:prefs|preferences|overrides)|reset (?:prefs|preferences|week)|"
    r"use defaults|forget my (?:prefs|preferences|overrides))\b", re.I)
# "I'd prefer X over Y" / "swap Y for X" / "use X instead of Y" / "X rather than Y"
SWAP_RE = re.compile(
    r"\b(prefer|over|instead\s+of|in\s+place\s+of|rather\s+than|"
    r"as\s+opposed\s+to|swap|switch|move|change)\b", re.I)
# Broader: "daily target", "target for today", "today's goal/ideal"
TODAY_TARGET_RE = re.compile(
    r"\b(today.?s?\s+(?:target|ideal|goal)|target\s+for\s+today|"
    r"daily\s+(?:target|ideal|goal)|ideal\s+today|day\s+target)\b", re.I)
# "What am I doing on Friday?" — looking at a specific weekday's workout.
# Distinct from PLAN_DAYS_RE (plural, full week) and WORKOUT_TODAY_RE
# (today/tomorrow only). Detected via a function (not a single regex)
# because the trigger is contextual: a question word + a single named
# weekday + workout-context-or-short.
def _is_workout_on_day_query(m: str) -> int | None:
    """Return weekday index (0=Mon..6=Sun) if the message is a 'what am I
    doing on X' / 'Friday's workout?' query. Else None.
    Requires exactly one named weekday — multiple weekdays imply a swap
    or pick, which other handlers own. Excludes messages that look like
    per-week pref mutators (UNAVAILABLE / PICK / SWAP / TOLERATE) so those
    keep flowing to their dedicated handlers."""
    # Skip if any per-week-pref keyword is present — those handlers own
    # the routing for their respective intents.
    if re.search(
            r"\b(can'?t|cannot|won'?t|skip(?:ping)?|miss(?:ing)?|busy|"
            r"unavailable|out\s+(?:on|for)|"
            r"prefer|swap|switch|move|change|"
            r"\bpick\b|fine\s+with|tolerate|scale|ignore|allow|"
            r"clear|reset|rather|instead\s+of|in\s+place\s+of)\b", m, re.I):
        return None
    days = parse_weekdays(m, include_relative=False)
    if len(days) != 1:
        return None
    has_question = re.search(
        r"\b(what|tell me|show( me)?|how|give me|describe)\b", m, re.I)
    has_workout_ctx = re.search(
        r"\b(doing|workout|wod|cf|crossfit|run|running|session|gym|"
        r"training|train|lift|lifting|going)\b", m, re.I)
    # Either: question + workout context, OR a terse day-specific ask
    # ("Friday workout?", "Wednesday's session?") that doesn't need an
    # interrogative.
    if has_question and (has_workout_ctx or len(m.split()) <= 8):
        return days[0]
    if has_workout_ctx and len(m.split()) <= 6:
        return days[0]
    return None


# "Am I working out today?" — must explicitly mention today/now or 'today' as
# the day-marker. Without that anchor, generic "am I working out" gets shadowed
# by scheduling questions like "which days am I working out" → those should
# go to PLAN_DAYS_RE / handle_show_plan instead.
# "When is my next CrossFit session" / "next workout" / "next run" /
# "next cf?". Distinct from WORKOUT_TODAY_RE (only reads today's row);
# this walks forward through the plan to find the next non-rest day.
# The regex is permissive — any phrase with "next" + a workout-kind
# token routes here. Tightened to allow trailing punctuation like "?"
# after a bare "next cf".
NEXT_WORKOUT_RE = re.compile(
    r"\bnext\s+(?:crossfit|cf|wod|workout|run|z2|zone\s*2|gym)\b|"
    r"\bwhen\s+is\s+(?:my\s+)?next\b|"
    r"\bmy\s+next\s+(?:crossfit|cf|wod|workout|run|z2|zone\s*2|"
    r"gym|session)\b|"
    r"\b(?:when|what'?s)\s+(?:is\s+)?(?:the\s+)?next\s+"
    r"(?:cf|crossfit|z2|run|workout)\b",
    re.I)

WORKOUT_TODAY_RE = re.compile(
    r"(?:"
    # "am I [verb] today/now"
    r"\bam\s+i\s+(?:working\s*out|workout|doing\s+(?:crossfit|cf|wod)|"
    r"going\s+to\s+(?:the\s+)?gym|running|on\s+a\s+(?:rest|workout|cf)\s+day)\s+"
    r"(?:today|now)\b"
    r"|"
    # "do I work out / crossfit / run today/now"
    r"\bdo\s+i\s+(?:work\s*out|crossfit|run)\s+(?:today|now)\b"
    r"|"
    # "is today a CF / rest / Z2 / run / workout / recovery day"
    r"\bis\s+today\s+(?:a\s+|my\s+)?(?:workout|cf|crossfit|rest|z2|run|recovery)\s+day\b"
    r"|"
    # "workout today?" alone
    r"\bworkout\s+today\b\??"
    r"|"
    # "running today" / "running tomorrow" / "workout tomorrow" — short
    # gym-status questions about today or tomorrow.
    r"\b(?:running|workout|crossfit|cf|wod)\s+(?:today|tomorrow)\b\??"
    r")", re.I)

# ─── Hindi / Hyderabadi-transliterated phrasings ───
# The user mixes English and Hindi (Dakhini) freely. These triggers route
# common Hindi questions to the correct deterministic handler instead of
# falling through to the LLM (which hallucinates plausible-but-wrong
# answers because it has all the context).
#
#   "aaj" (today) + workout/cf/wod          → handle_workout_today
#   "aaj ka workout"                        → handle_workout_today
#   "kya chal" / "kaisa hai" + (day-ish?)   → handle_pace (status check)
HINDI_AAJ_WORKOUT_RE = re.compile(
    r"\baaj\b[\s\S]*?\b(crossfit|cf|wod|workout|gym|run(?:ning)?|"
    r"rest|recovery|kal\s+kya|kya\s+karna)\b", re.I)
HINDI_STATUS_RE = re.compile(
    r"\b(kya|kaise|kaisa|kaisi)\s+(chal|hal|haal)\b", re.I)

# Word-number support so "one rest day" parses too.
_NUMS = r"(\d+|one|two|three|four|five|six|seven)"
_WORD_NUM = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7}
def _n(s: str) -> int:
    return _WORD_NUM[s.lower()] if s.lower() in _WORD_NUM else int(s)
# "N workouts ... M rest days" or "M rest days ... N workouts"
SPLIT_RE    = re.compile(
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?|days?\s+of\s+workout)"
    rf"[\s\S]{{0,80}}?{_NUMS}\s*(?:rest|recovery|off)\s*days?", re.I)
SPLIT_RE2   = re.compile(
    rf"{_NUMS}\s*(?:rest|recovery|off)\s*days?[\s\S]{{0,80}}?"
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?)", re.I)
# "I have 2 more workout days" (no rest specified → derive from days remaining in week)
WORKOUTS_ONLY_RE = re.compile(
    rf"{_NUMS}\s*(?:more\s+)?(?:workouts?|crossfit|cf|wod|sessions?|workout\s+days?)\b", re.I)
WEIGHIN_RE  = re.compile(r"\b(when.*weigh|weigh.?in.*(when|day|time)|should I weigh)\b", re.I)
HRV_RE      = re.compile(r"\bhrv\b[^0-9]{0,15}(\d{2,3})\b", re.I)
HRV_LOW_RE  = re.compile(r"\bhrv\b.*\b(low|tank|down|crash|drop)\b", re.I)
BREATH715_RE = re.compile(r"\b(7[/\-]15|7\s*15|breath(e|ing).*hrv|improve.*hrv|reset.*hrv)\b", re.I)
BOXBREATH_RE = re.compile(r"\bbox\s*breath", re.I)
PREFUEL_RE  = re.compile(r"\b(eat|fuel|snack).*(before|pre.?workout|pre.?run)|"
                         r"\b(pre.?workout|pre.?run)\b.*\b(eat|fuel|snack)", re.I)
COOLDOWN_RE = re.compile(r"\b(cool.?down|cooldown|stretch(?:ing|es)?|"
                         r"recovery\s+routine|post.?workout|post.?wod|"
                         r"mobility\s+(?:routine|flow|work))\b", re.I)
# Only fires for explicit A-vs-B comparisons. Plain "should I run" no longer
# triggers it (Bug 3 from 05/03 testing — false-positive on scheduling questions).
DECIDE_RE   = re.compile(r"\b(run(?:ning)?\s+(?:or|vs|versus)\s+(?:crossfit|wod|workout)|"
                         r"(?:crossfit|wod)\s+(?:or|vs|versus)\s+run(?:ning)?|"
                         r"should I (?:run\s+or\s+do\s+(?:crossfit|wod)|"
                         r"do\s+(?:crossfit|wod)\s+or\s+run))\b", re.I)
TIER_RE     = re.compile(r"\btier\s+(survival|re.?entry|baseline|performance|hammer)\b", re.I)
LOG_BURN_RE = re.compile(r"\b(burn(?:ed|t)?|did)\s+(\d{2,4})\s*(?:cal|kcal|calories|active)?\b", re.I)
LOG_KIND_RE = re.compile(r"\b(crossfit|cf|wod|run|10k|z2|walk|bike|row)\s+(\d{2,4})\b", re.I)
# FIX_BURN_RE — destructive rewrite of one day's burn. Matches:
#   "fix mon 581"
#   "set tuesday 1200"
#   "correct sunday 2150 kcal"
#   "overwrite friday 800 calories"
# Group 1 is the action verb (unused — anchors the intent), group 2
# is the weekday token, group 3 is the kcal value. The kcal range is
# {1,5} digits so a typo'd "fix mon 50000" still matches and falls
# through to handle_fix_burn's range guard rather than being silently
# ignored by the regex (which would mis-route to the LLM and produce
# a fabricated answer).
FIX_BURN_RE = re.compile(
    r"\b(fix|set|correct|overwrite)\s+"
    r"(mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\s+"
    r"(\d{1,5})\s*"
    r"(?:cal|kcal|calories|active)?\b",
    re.I)
TIMELINE_RE = re.compile(
    r"\b(to|target|reach|hit|want|aim|drop|cut|get|lose)\s+(?:to\s+)?"
    r"(\d{2,3})\s*(lbs?|kg|pounds?)?\b.*"
    r"\bby\s+([a-z]+\s+\d+|\d+/\d+|\d+-\d+)", re.I)
TIMELINE_SHORT_RE = re.compile(r"\b(timeline|how long).*\b(\d{2,3})\s*(lbs?|kg)?", re.I)
# No-arg form: "realistic timeline", "sustainable pace", "what's my deficit",
# "daily intake target". Defaults to TARGET_LBS (the seeded 80 kg intent).
TIMELINE_NO_ARG_RE = re.compile(
    r"\b(realistic\s+(?:target|timeline|date|goal|weight\s+loss|deficit|pace)|"
    r"sustainable\s+(?:target|timeline|date|deficit|pace|rate)|"
    r"weight\s+loss\s+(?:plan|timeline|date|rate|pace)|"
    r"daily\s+(?:intake|calorie\s+intake|cal\s+intake)\s+target|"
    r"what.{0,15}deficit|how (?:long|fast).{0,30}(?:lose|drop|cut))\b", re.I)
# Natural-language asks about WHEN the user hits target weight / goal weight.
# Falls into the dual-target dashboard which shows actual ETA dates
# (Aug 13, 2026 / Nov 3, 2026), not just "17 weeks".
TARGET_WEIGHT_RE = re.compile(
    r"\b(?:"
    # Tolerate typos and dropped auxiliaries: "will", "wil", "wll",
    # "i'll", or even bare "when I reach" with no auxiliary at all.
    # The auxiliary slot is a 2-5 char word starting with w/d/a/c/s, OR
    # absent. This catches "When wil I reach my target" without making
    # the regex permissive enough to false-fire elsewhere.
    r"when\s+(?:(?:will|wil|wll|i'?ll|do|am|can|should)\s+)?i\s+"
    r"(?:get\s+to|hit|reach|be\s+at|achieve|see|lose)\s+"
    r"(?:my\s+)?(?:target|goal|intermediate|final)?\s*"
    r"(?:weight|lbs?|kg|\d{2,3})|"
    r"how\s+long\s+(?:until|till|to)\s+"
    r"(?:i\s+)?(?:hit|reach|get\s+to|achieve|lose)\s+"
    r"(?:my\s+)?(?:target|goal|weight|\d{2,3})|"
    r"when\s+(?:will|wil|do)\s+i\s+(?:hit|reach|lose)\s+"
    r"(?:\d{2,3}\s*(?:lbs?|kg)|85\s*kg|84\s*kg|80\s*kg|176|185|target)|"
    r"target\s+date|when.*at\s+(?:80|84)\s*kg"
    r")\b", re.I)


def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    fmts = ["%b %d", "%B %d", "%b %d %Y", "%B %d %Y",
            "%m/%d", "%m/%d/%Y", "%m-%d", "%m-%d-%Y"]
    for f in fmts:
        try:
            d = datetime.strptime(s, f)
            if d.year == 1900:
                d = d.replace(year=datetime.now().year)
                if d < datetime.now():
                    d = d.replace(year=d.year + 1)
            return d
        except ValueError:
            continue
    return None


def _slash_help() -> str:
    """Static help text — listed here (not built dynamically from
    SLASH_COMMANDS) so the ordering matches what's pedagogically
    sensible rather than dict-insertion order."""
    return (
        "*Slash shortcuts* (deterministic, no LLM in the loop):\n"
        "• `/pace` — today's burn vs prorated day target\n"
        "• `/today` — bare burn-so-far for today\n"
        "• `/week` — week-to-date vs prorated week target + remaining\n"
        "• `/plan` — this week's planned grid\n"
        "• `/next` — next scheduled workout\n"
        "• `/fix <day> <kcal>` — overwrite recorded burn for one day "
        "(e.g. `/fix mon 581`)\n"
        "• `/help` — this card"
    )


# Slash command table — keys are lowercase, no leading slash. Each
# value is a zero-arg callable producing the response string. /fix is
# handled separately (in _try_slash_command) because it takes args.
#
# The values are bound at module-import time to the names visible in
# THIS module's globals; tests that monkeypatch e.g. `handle_pace` must
# patch on the module (`monkeypatch.setattr(handler, "handle_pace", ...)`)
# AND then rebuild SLASH_COMMANDS (or test the dispatch through route()
# which re-binds each call). For the monkeypatch-friendly path, each
# lambda re-resolves the name at call time by going through globals()
# — that way tests can patch `handler.handle_pace` and the slash
# dispatcher will see the patched version without rebuilding the dict.
SLASH_COMMANDS: dict[str, callable] = {
    "/pace":  lambda: globals()["handle_pace"](),
    "/today": lambda: globals()["handle_daily_burn"](datetime.now()),
    "/week":  lambda: globals()["handle_weekly_remaining"](),
    "/plan":  lambda: globals()["handle_show_plan"](),
    "/next":  lambda: globals()["handle_next_workout"](),
    "/help":  _slash_help,
}

# /fix <day> <kcal> — args-bearing slash command, peeled off before
# the zero-arg dispatch table lookup. Tolerates "/fix mon 581",
# "/FIX  monday  1200", "/fix tue 2150 kcal", "/fix wed 800 calories".
_SLASH_FIX_RE = re.compile(
    r"^/fix\s+"
    r"(mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\s+"
    r"(\d{1,5})\s*"
    r"(?:cal|kcal|calories|active)?\s*$",
    re.I)

# Strip optional @botname suffix that Telegram appends when the bot is
# in a group chat — "/pace@kobe_bot" should dispatch like "/pace".
_SLASH_BOT_SUFFIX_RE = re.compile(r"@[\w_]+\s*$")


def _try_slash_command(msg: str) -> str | None:
    """If `msg` is a recognized slash shortcut, run its handler and
    return the response. Otherwise return None so route() can fall
    through to the reasoner / legacy path.

    Normalization:
      • lowercase + strip
      • peel any trailing @botname suffix (group-chat addressing)
      • for the zero-arg table, exact-match the FIRST token only —
        trailing junk ("/pace something extra") still dispatches /pace
        (the trailing text is ignored). This matches Telegram's
        convention where users sometimes type "/pace please" or
        "/pace?" and expect the command to fire.
      • /fix is the exception — it requires the full regex to match
        end-to-end, since the args are load-bearing.

    Unknown shortcuts ("/pase" typo, "/foobar") return None so the
    reasoner has a chance to interpret them.
    """
    if not msg:
        return None
    norm = msg.strip().lower()
    norm = _SLASH_BOT_SUFFIX_RE.sub("", norm).strip()
    if not norm.startswith("/"):
        return None

    # /fix is args-bearing — check the full-line regex first.
    if norm.startswith("/fix"):
        m = _SLASH_FIX_RE.match(norm)
        if m:
            return handle_fix_burn(m.group(1), float(m.group(2)))
        # Malformed /fix — give the user a hint instead of falling
        # through to the reasoner (which would hallucinate). This is
        # consistent with the "explicit slash → deterministic answer"
        # contract: once the user typed /fix they're in command mode.
        return ("❌ `/fix` needs a day and a kcal value. "
                "Try `/fix mon 581` or `/fix monday 1200`.")

    # Zero-arg dispatch: first whitespace-separated token must match a
    # known key. Everything after is ignored.
    head = norm.split(None, 1)[0]
    handler_fn = SLASH_COMMANDS.get(head)
    if handler_fn is None:
        return None
    return handler_fn()


# ─────────────────────── Day-8: Delegation routing ───────────────────
# Per ADR-006/-007: when the user asks about Fraser's or Huberman's
# territory, Kobe must NOT synthesize a reply from training-data priors
# (the 2026-05-16 production bug — "what is the WOD" → Kobe invents a
# workout). Instead Kobe detects the out-of-domain shape and calls
# delegate_to(), forwarding the response with attribution.
#
# Today's `_should_delegate` is a DETERMINISTIC keyword detector — the
# LLM reasoner (Day-9+) will replace this with a model-driven decision.
# Keeping it deterministic today means the tests pin a stable contract;
# the Day-9 LLM upgrade preserves the contract shape (it just makes the
# inference fuzzier and broader-coverage).
#
# Mirrors `agents/fraser/handler.py::_should_delegate` exactly in shape,
# inverted in content: Kobe's "delegate to Fraser" patterns are the
# workout-prescription ones; Fraser's are the weight/HRV ones.

# Fraser's territory: workout design, CrossFit programming, scaled
# loads, WOD selection, gym programming, movement substitutions,
# warm-up/cool-down ATTACHED to today's WOD.
_FRASER_DELEGATION_PATTERNS = [
    # The 2026-05-16 bug query itself + obvious paraphrases.
    re.compile(r"\bwhat(?:'s|\s+is)\s+(?:my\s+|the\s+|today'?s\s+)?wod\b", re.I),
    re.compile(r"\b(?:give|show)\s+me\s+(?:the\s+|today'?s\s+|my\s+)?workout\b", re.I),
    re.compile(r"\btoday'?s\s+(?:crossfit|workout|wod|session|prescription)\b", re.I),
    # "design / scale / substitute / programming" intents.
    re.compile(r"\b(scale|substitute|sub)\s+.*\bwod\b", re.I),
    # Allow hyphenated movement names ("pull-ups for ring rows").
    re.compile(r"\b(can|how)\s+(?:i\s+)?(?:substitute|sub|swap)\s+[\w-]+(?:\s+\w+)?\s+for\s+[\w-]+", re.I),
    re.compile(r"\bworkout\s+(?:design|programming|prescription|card)\b", re.I),
    re.compile(r"\b(crossfit|cf)\s+programming\b", re.I),
    re.compile(r"\b(?:scaled?|adjust(?:ed)?)\s+(?:load|loads|weight|weights)\b", re.I),
    re.compile(r"\b(?:percentage|%)\s+of\s+(?:my\s+)?1rm\b", re.I),
    re.compile(r"\bi\s+want\s+to\s+do\s+prvn\b", re.I),
    re.compile(r"\bmake[-\s]?up\s+(?:session|workout|wod)\b", re.I),
    # "what am I doing at the gym today" — gym-prescription intent.
    re.compile(r"\bwhat.*(?:doing|do)\s+at\s+the\s+gym\b", re.I),
    re.compile(r"\bprescribe\s+(?:my\s+|a\s+)?workout\b", re.I),
]

# Huberman's territory: sleep quality, RHR trends, recovery color
# semantics (when asked AS a vitals interpretation question, not when
# Kobe is using the values internally for tier selection).
_HUBERMAN_DELEGATION_PATTERNS = [
    re.compile(r"\b(?:how|how\s+much)\s+(?:did\s+i\s+sleep|was\s+my\s+sleep)\b", re.I),
    re.compile(r"\bsleep\s+(?:quality|score|last\s+night|hours|trend)\b", re.I),
    re.compile(r"\brhr\b.*\b(?:trend|today|last|interpret|mean)\b", re.I),
    re.compile(r"\bresting\s+heart\s+rate\b", re.I),
    re.compile(r"\bhow\s+recovered\s+am\s+i\b", re.I),
    # "am I in red/yellow/green" — recovery COLOR signal, distinct
    # from tier-name selection (which stays with Kobe).
    re.compile(r"\bam\s+i\s+(?:in\s+)?(?:red|yellow|green)\s+(?:zone|signal|today)?\b", re.I),
    re.compile(r"\brecovery\s+(?:color|signal|status)\b", re.I),
]


def _should_delegate(msg: str) -> str | None:
    """Return the target agent name if `msg` is in Fraser/Huberman
    territory, None if Kobe should handle it.

    Order: Fraser patterns first (the 2026-05-16 bug's territory),
    then Huberman. First match wins. Pure-Kobe questions
    ("what's my weight", "log hrv 42", "tier hammer", "pace check")
    never match these patterns and route to Kobe's existing
    handlers / reasoner.
    """
    if not msg:
        return None
    for pat in _FRASER_DELEGATION_PATTERNS:
        if pat.search(msg):
            return "fraser"
    for pat in _HUBERMAN_DELEGATION_PATTERNS:
        if pat.search(msg):
            return "huberman"
    return None


def _delegate_and_forward(target: str, msg: str) -> str:
    """Call core.delegation.delegate_to(target, msg) and shape the
    response into Kobe's outbound text. Forwarding with attribution
    per ADR-007: "Fraser says: ..." for structured replies. On
    delegation failure (loop / depth / agent_not_registered /
    disabled), surface the fallback_reply transparently.
    """
    from core import delegation as _delegation
    result = _delegation.delegate_to(target, msg)
    if result.get("agent") and result.get("reply"):
        return f"{result['agent']} says: {result['reply']}"
    return result.get(
        "fallback_reply",
        f"Couldn't hand that off to {target}. Try /pace, /plan, "
        "or rephrase.")


def route(msg: str) -> str:
    """Top-level inbound dispatcher.

    Order (Day-8, post-ADR-006/-007):
      1. Slash commands (`/pace`, `/today`, ...) → deterministic
         handler, no LLM. Cheap, ~zero latency, always wins.
      2. Cross-agent delegation — if the message is in Fraser's or
         Huberman's territory, hand off via core.delegation. This is
         the discipline that stops the 2026-05-16 bug from recurring:
         Kobe NEVER answers WOD/scaled-load/sleep questions from
         training-data priors.
      3. RAHAT_LEGACY_DISPATCH=1 env → regex `_legacy_route`.
      4. Default (Phase 4, model-first) → `reasoner.reason(msg)`.
      5. Reasoner crash → fall back to `_legacy_route`.

    The slash check runs BEFORE delegation on purpose: /pace and /week
    are unambiguous Kobe intent and should never round-trip through
    another agent, even if the message contained the word "WOD".

    Phase 4 (model-first): the reasoner path is the long-run answer.
    Day-9+ replaces `_should_delegate`'s deterministic detector with
    a model-driven decision invoked by the reasoner itself; today's
    code keeps the delegation step deterministic for testability and
    will retire when Day-9 lands.

    Reasoner failures (Anthropic + Gemini both down) automatically
    cascade into the legacy path, so this is also the live resilience
    boundary.
    """
    # 1. Slash dispatcher (Day-1 R1 step 1).
    slash = _try_slash_command(msg)
    if slash is not None:
        return slash

    # 2. Cross-agent delegation (Day-8, ADR-006/-007). Runs BEFORE the
    # legacy regex router AND the model-first reasoner so any "what's
    # the WOD" shape lands at Fraser without spending a Kobe token on
    # a guaranteed-wrong answer.
    target = _should_delegate(msg)
    if target is not None:
        return _delegate_and_forward(target, msg)

    if os.getenv("RAHAT_LEGACY_DISPATCH", "").lower() in ("1", "true", "yes"):
        return _legacy_route(msg)
    try:
        from agents.the_scientist import reasoner
        return reasoner.reason(msg)
    except Exception as e:
        # Hard fail in the reasoner code itself (not a model error) — last
        # resort to legacy. Tools' charter / DB errors are caught inside
        # the reasoner and should never bubble here, but if they do we
        # don't want the user to see a stack trace in Telegram.
        print(f"[scientist.route] reasoner crash, falling back: {e}")
        return _legacy_route(msg)


def _legacy_route(msg: str) -> str:
    # Normalize iOS / Telegram autocorrect characters BEFORE any regex runs
    # — most regexes assume ASCII apostrophes. Curly apostrophe (U+2019)
    # in "can't" was the cause of the 02:19 AM bug where "I can't workout
    # on Thursday" fell through to the LLM and got a fabricated plan.
    m = (msg.strip()
            .replace("’", "'")   # right single quote → '
            .replace("‘", "'")   # left single quote → '
            .replace("“", '"')   # left double quote → "
            .replace("”", '"'))  # right double quote → "

    # --- mutators first (set state, log data) ---
    if (mw := WEIGHT_RE.search(m)):
        return handle_weight(float(mw.group(1)))
    if WEIGHT_QUERY_RE.search(m):
        return handle_current_weight()
    if (mh := HRV_RE.search(m)):
        return handle_hrv(float(mh.group(1)))
    if (mt := TIER_RE.search(m)):
        return handle_set_tier(mt.group(1))
    if (mk := LOG_KIND_RE.search(m)):
        return handle_manual_burn(float(mk.group(2)), kind=mk.group(1).lower())
    if (mb := LOG_BURN_RE.search(m)) and TODAY_RE.search(m):
        return handle_manual_burn(float(mb.group(2)), kind="today")
    # Destructive day-rewrite — "fix mon 581", "set tuesday 1200",
    # "correct sunday 2150 kcal". Routes to handle_fix_burn which
    # DELETEs+INSERTs against raw_vitals + workout_log. Must come
    # AFTER LOG_BURN_RE so "burned 800 today" (additive log) wins
    # over an accidental fix-like match. The /fix slash command
    # routes to the same handler via _SLASH_FIX_RE.
    if (mf := FIX_BURN_RE.search(m)):
        return handle_fix_burn(mf.group(2), float(mf.group(3)))

    # --- specific lookups (must beat generic TODAY/REMAIN matches) ---
    # "When is my next CrossFit session" — walks forward through the
    # plan; must fire before WORKOUT_TODAY_RE (which only reads today's
    # row) and before the LLM fallback (which would hallucinate).
    if NEXT_WORKOUT_RE.search(m):
        if re.search(r"\b(z2|zone\s*2|run)\b", m, re.I):
            return handle_next_workout(kind_filter="z2")
        if re.search(r"\b(crossfit|cf|wod|gym)\b", m, re.I):
            return handle_next_workout(kind_filter="cf")
        return handle_next_workout(kind_filter="any")
    # 02:19 AM bug fix: 'am I working out today' was returning the burn-so-far
    # because TODAY_RE matched first. Catch the workout-status question here.
    if WORKOUT_TODAY_RE.search(m):
        when = "tomorrow" if re.search(r"\btomorrow\b", m, re.I) else "today"
        return handle_workout_today(when=when)
    # Hindi/Dakhini: "aaj crossfit hai na", "aaj ka workout kya hai" —
    # without these, the LLM fallback fabricates today's workout from
    # context. Route to the deterministic same-day handler.
    if HINDI_AAJ_WORKOUT_RE.search(m):
        return handle_workout_today(when="today")
    # Hindi/Dakhini status check: "kya chal ra" / "kaisa hai" — route to
    # pace handler so the user gets actual numbers instead of an LLM
    # paraphrase of their context.
    if HINDI_STATUS_RE.search(m):
        return handle_pace()
    # "What am I doing on Friday?" — single named weekday + workout intent
    # → show that day's planned session with WOD details.
    day_idx = _is_workout_on_day_query(m)
    if day_idx is not None:
        return handle_workout_on(day_idx)
    # "daily target for today" → today's plan target, not burn-so-far.
    if TODAY_TARGET_RE.search(m):
        return handle_today_target()
    # Catch-up / recalibration: "how do I catch up", "how can I hit my
    # weekly target", "behind on calories" — must fire BEFORE
    # NEXT_WEEK_TARGET_RE because phrases like "hit my weekly target"
    # would otherwise be misrouted to next-week's plain target lookup
    # instead of a redistribution proposal.
    if RECALIBRATE_RE.search(m):
        return handle_recalibrate()
    # B1 fix: "caloric target for next week" → plan-based active-burn target.
    if NEXT_WEEK_TARGET_RE.search(m):
        return handle_next_week_target()
    # B2 fix: "calories this week" → bare weekly burn vs target.
    if THIS_WEEK_RE.search(m):
        return handle_weekly_remaining()
    # B3 fix: "which days should I crossfit/rest/run zone 2 next week" → plan view.
    if PLAN_DAYS_RE.search(m):
        return handle_show_plan(next_week=bool(NEXT_WEEK_RE.search(m)))

    # --- decisions / advice ---
    if WEIGHIN_RE.search(m):
        return handle_weighin_when()
    if HRV_LOW_RE.search(m):
        return handle_breathing("7-15") + "\n\n" + handle_post_recovery()
    if BREATH715_RE.search(m):
        return handle_breathing("7-15")
    if BOXBREATH_RE.search(m):
        return handle_breathing("box")
    if PREFUEL_RE.search(m):
        return handle_pre_fuel()
    if COOLDOWN_RE.search(m):
        return handle_post_recovery()
    if DECIDE_RE.search(m):
        return handle_decision_run_or_wod()

    # --- weight timeline ---
    if (mtl := TIMELINE_RE.search(m)):
        target = float(mtl.group(2))
        unit = (mtl.group(3) or "lbs").lower()
        if "kg" in unit:
            target = target * 2.20462
        when = _parse_date(mtl.group(4))
        return handle_weight_timeline(target, when)
    if (mts := TIMELINE_SHORT_RE.search(m)):
        target = float(mts.group(2))
        unit = (mts.group(3) or "lbs").lower()
        if "kg" in unit:
            target = target * 2.20462
        return handle_weight_timeline(target)
    if TIMELINE_NO_ARG_RE.search(m):
        return handle_weight_timeline()
    if TARGET_WEIGHT_RE.search(m):
        return handle_weight_timeline()

    # --- split-target math (must run before REMAIN, since "...left" matches REMAIN too) ---
    # Optional override: "to hit 6500" / "for 6000" inside the same message.
    target_override = None
    if (mt := re.search(r"\b(?:hit|reach|target|for|to)\s+(\d{4})\b", m, re.I)):
        target_override = float(mt.group(1))
    if (ms := SPLIT_RE.search(m)):
        return handle_split_target(_n(ms.group(1)), _n(ms.group(2)), target_override)
    if (ms := SPLIT_RE2.search(m)):
        return handle_split_target(_n(ms.group(2)), _n(ms.group(1)), target_override)
    # Trigger split when the user explicitly states a target ("hit 6500"),
    # asks about distribution ("how many should I burn"), or signals
    # remaining-budget intent ("I have N workouts left/remaining").
    if WORKOUTS_ONLY_RE.search(m) and re.search(
            r"\b(hit|reach|target|goal|6\d{3}|5\d{3}|"
            r"left|remaining|have|how (?:many|much))\b", m, re.I):
        n = _n(WORKOUTS_ONLY_RE.search(m).group(1))
        days_left = 7 - datetime.now().weekday()
        rests = max(days_left - n, 0)
        return handle_split_target(n, rests, target_override)

    # --- per-week pref mutators ---
    # MUST run BEFORE the generic standard lookups (today/yesterday/remain).
    # Otherwise a message like "I can't workout Thursday, can I work out today?"
    # gets eaten by TODAY_RE on the trailing "today" instead of marking
    # Thursday unavailable.
    next_week_q = bool(NEXT_WEEK_RE.search(m))
    if CLEAR_PREFS_RE.search(m):
        return handle_clear_prefs(next_week=next_week_q)
    if TOLERATE_RE.search(m):
        return handle_tolerate(m, next_week=next_week_q)
    # Dislike-list query must run before LIST_DISLIKES words leak into
    # other handlers ('dislikes' is short and matchy).
    if LIST_DISLIKES_RE.search(m):
        return handle_list_dislikes()
    # Drop dislike before add — "I can do deadlifts again" should NOT
    # match the bare DISLIKE_RE.
    if (md := DROP_DISLIKE_RE.search(m)):
        return handle_drop_dislike(md.group(1))
    if (mdis := DISLIKE_RE.search(m)):
        movement = mdis.group(1).strip().lower()
        # Filter out obviously meta-noisy matches — single short words
        # ("no idea", "no thanks") and scheduler verbs that DISLIKE_RE
        # might overfit on.
        if movement in {"idea", "thanks", "thank", "way", "more", "less",
                        "problem", "worries", "good"} or len(movement) < 3:
            pass  # fall through to other handlers
        else:
            scope_token = (mdis.group(2) or "week").lower().strip()
            scope = ("today" if scope_token == "today"
                     else "always" if scope_token in
                          {"ever", "always", "permanently", "forever"}
                     else "week")
            return handle_dislike_movement(movement, scope)
    # SWAP must run before UNAVAILABLE/PICK because "prefer Mon over Sun"
    # mentions both weekdays.
    if SWAP_RE.search(m) and len(parse_weekdays(m)) >= 2:
        return handle_swap(m, next_week=next_week_q)
    if UNAVAILABLE_RE.search(m) and parse_weekdays(m):
        return handle_unavailable(m, next_week=next_week_q)
    if PICK_RE.search(m) and parse_weekdays(m):
        return handle_pick_days(m, next_week=next_week_q)
    if (parse_weekdays(m) or
        re.search(r"\b(workouts?|crossfit|cf|wod|runs?|z2|zone\s*2|"
                  r"rest|recover|sessions?)\b", m, re.I)) and re.search(
            r"\b(can'?t|prefer|swap|switch|move|skip|miss|busy|"
            r"unavailable|update|adjust|change|reschedul|fix|fewer|less|"
            r"reduce|drop|cut)\b", m, re.I):
        return handle_scheduling_help()

    # --- standard lookups (after per-week mutators) ---
    if YEST_RE.search(m):
        return handle_daily_burn(datetime.now() - timedelta(days=1))
    if LASTWK_RE.search(m):
        return handle_last_week()
    if REMAIN_RE.search(m):
        return handle_weekly_remaining()
    if TODAY_RE.search(m):
        return handle_daily_burn(datetime.now())

    if REPLAN_RE.search(m):
        return handle_replan()
    if SCHED_RE.search(m):
        return handle_show_plan(next_week=next_week_q)
    # (RECALIBRATE_RE already checked higher in the router so phrases
    # like "hit my weekly target" beat NEXT_WEEK_TARGET_RE.)
    if PACE_RE.search(m):
        return handle_pace()
    if TODAY_TARGET_RE.search(m):
        return handle_today_target()
    if FILTER_RE.search(m):
        return handle_filter()

    return llm_coach(msg)


def llm_coach(msg: str) -> str:
    if not client:
        return ("LLM not configured. Try: 'today', 'yesterday', 'last week', "
                "'remaining', 'schedule', 'filter', 'when should I weigh in', "
                "'hrv 45', 'pre-workout fuel', 'cooldown', '7/15 breathing', "
                "'tier survival', 'wod 850', 'wt 195'.")
    burned, _ = burn_this_week()
    target = weekly_target()
    remaining = max(target - burned, 0.0)
    days_left = 7 - datetime.now().weekday()
    weight = latest_weight()
    tier = state_get("recovery_tier", DEFAULT_TIER)
    elig = ", ".join(d.label for d in eligible_cf_days()) or "none"

    prompt = (
        f"Athlete: Venkat (6'1\"). Weight {weight:.1f} lbs.\n"
        f"Targets: 84 kg ({INTENT_INTERMEDIATE_LBS:.1f} lbs) intermediate, "
        f"80 kg ({INTENT_TARGET_LBS:.1f} lbs) final.\n"
        f"LOCKED rate: {LOCKED_LOSS_LB_PER_WEEK} lb/wk → daily intake "
        f"{DAILY_INTAKE_KCAL} kcal, weekly active {WEEKLY_ACTIVE_TARGET_KCAL} kcal.\n"
        f"LOCKED CADENCE — exactly 3 PRVN CrossFit + 1 Zone-2 10K + 3 active rest "
        "per week. NEVER more sessions, NEVER more Z2 runs, NEVER 'add a Z2 to a "
        "CF day'. The user's body is calibrated for this load. Anything else "
        "causes injury, HRV crash, or burnout.\n"
        f"Tier: {tier}. BMR {BMR_KCAL}.\n"
        f"Week burn: {burned:.0f} / {target:.0f} kcal "
        f"(remaining {remaining:.0f} over {days_left} days).\n"
        f"Eligible CF days (no partner/handstand/muscle-up/OHS/snatch-in-strength): {elig}.\n"
        f"User message: {msg}\n"
        f"Rules: data-driven CrossFit + Z2 coach. Lbs only. ≤6 lines.\n"
        f"\n"
        f"VOICE — Hyderabadi (Dakhini) wit + PM brevity (per PRD §3):\n"
        f" • Mix English + Hyderabadi phrases naturally. NOT pure Hindi.\n"
        f" • Numbers, dates, exact protocols stay in English for clarity.\n"
        f" • Vocabulary you can use sparingly: hau (yes), nakko (don't),\n"
        f"   miya/bhai (friendly address), bole to (i.e.), light lo (chill),\n"
        f"   samjhe (got it), chal (let's go), abhi (now), bohot (very).\n"
        f" • One Hyderabadi phrase per response is plenty — DON'T parody.\n"
        f" • Address as 'bhai' or 'miya', not 'sir' or 'mate'.\n"
        f" • Keep it dry and direct, like a Hyderabadi gym coach who's\n"
        f"   seen it all. Not flowery, not over-friendly.\n"
        f" • Example: 'Hau bhai, today's burn 850. Locked rate, light lo.'\n"
        f"   NOT: 'Aaj bhai aapka burn 850 hai, namaste!' (too forced).\n"

        f"NEVER recommend a deficit faster than {MAX_LOSS_LB_PER_WEEK} lb/wk — causes "
        "muscle loss + HRV crash + scale stalls. Use the LOCKED numbers above as "
        "the baseline; don't propose a different intake, active target, or cadence.\n"
        "If the user is asking about scheduling (which days to work out, swapping "
        "days, missing a day), say: 'Use `swap X for Y`, `I can't make X`, or "
        "`pick X Y Z for crossfit` — those replan deterministically against your "
        "locked cadence.' Do NOT propose a plan yourself — the deterministic "
        "scheduler in this agent is the only correct source for that.\n"
        "If user only asked about a single day's burn, do NOT mention weekly target.\n"
        "Use 7/15 breathing for HRV recovery. Bias toward run for fat loss.\n"
        "If HRV likely low (back-to-back hammer days, sick, sleep-deprived), "
        "scale back rather than push.\n"
        "\n"
        "ANTI-HALLUCINATION RULES (critical — these are the most common\n"
        "ways the LLM gets the user wrong):\n"
        " • DO NOT invent or compute weight-timeline math (e.g. \"17 days to\n"
        "   target\"). If asked about ETA / target dates / how long to lose X,\n"
        "   reply: 'Ask `when will I get to my target weight` or `how long to\n"
        "   80 kg` — those use the deterministic timeline math.'\n"
        " • DO NOT claim what TODAY'S workout is. The user's gym programming\n"
        "   isn't in this prompt. Reply: 'Ask `aaj ka workout` / `workout\n"
        "   today` / `what am I doing today` — that pulls the actual WOD.'\n"
        " • DO NOT make up burn numbers, weights, or HRV values. If asked,\n"
        "   reply: 'Ask `today` / `current weight` / `pace` for the live numbers.'\n"
        " • Hindi / Dakhini phrasings (`aaj`, `kya chal`, `hai na`) are\n"
        "   first-class — answer in the same register, but route the user to\n"
        "   a deterministic command if they're asking for specific data."
    )
    try:
        res = client.models.generate_content(model=MODEL_ID, contents=prompt)
        return res.text
    except Exception as e:
        # SECURITY: never echo raw exception strings to user-facing
        # channels. The Gemini SDK's HTTPError carries the full request
        # URL — including the `?key=<GEMINI_API_KEY>` query param — in
        # its `str(e)`. The previous `return f"❌ LLM error: {e}"` line
        # leaked the API key into Telegram twice in one week and forced
        # two key rotations. The exception detail still goes to stderr
        # so it shows up in vault/miya.log for operator debugging, but
        # the user sees a sanitized message with no URL, no query
        # params, and no exception type.
        print(f"[llm_coach] failed: {type(e).__name__}: {e}", file=sys.stderr)
        return "❌ LLM call failed — check vault/miya.log for details."


# ─────────────────────────── Nudges ───────────────────────────
def daily_target() -> float:
    """Today's ideal active burn — driven by the locked weekly plan."""
    try:
        return float(today_plan()["target_kcal"])
    except Exception:
        tier = state_get("recovery_tier", DEFAULT_TIER)
        return float(TIERS.get(tier, TIERS[DEFAULT_TIER])["daily"])


def latest_hrv() -> float | None:
    """Most recent logged HRV value (any source). Returns None if nothing logged."""
    con = _db()
    try:
        row = con.execute(
            "SELECT value FROM hrv_log ORDER BY ts DESC LIMIT 1").fetchone()
        return float(row[0]) if row else None
    finally:
        con.close()


def _internal_safety_downgrade() -> tuple[bool, str | None]:
    """Scientist-internal safety guard.

    When the Scientist's own HRV reading is in the RED band, it downgrades
    its performance messaging to recovery messaging on its own — this is
    normal coaching, not governance. A real governance veto from Bajrangi
    (when that agent ships) takes precedence and is checked separately.
    """
    hrv = latest_hrv()
    if hrv is None or hrv >= HRV_RED:
        return False, None
    return True, f"HRV {hrv:.0f} ms in RED band — downgrading to recovery"


def maybe_morning_briefing() -> str | None:
    """At 08:00, send the day's plan: type, gym pick, target, week so far.
    Suppressed if another agent has posted a veto for this nudge in
    governance_log; downgraded to recovery messaging if the Scientist's own
    HRV check is in the RED band."""
    now = datetime.now()
    if now.hour != NUDGE_MORNING_HOUR:
        return None
    today = now.strftime("%Y-%m-%d")
    if nudge_already_sent("morning_brief", today):
        return None
    row = today_plan()
    burned, _ = burn_this_week()
    target = weekly_target()
    kind = DAY_TYPE_LABEL[row["day_type"]]
    gym = f" ({row['gym_label']})" if row["gym_label"] else ""
    mark_nudge("morning_brief", today)

    # External veto from another agent (e.g. Bajrangi) — drop entirely.
    vetoed, reason = check_external_veto("morning_brief")
    if vetoed:
        return None

    # Scientist's own HRV-RED downgrade.
    downgrade, why = _internal_safety_downgrade()
    if downgrade:
        return (
            f"⚠️ *{why}*\n"
            f"{WEEKDAY_NAME[row['weekday']]} was scheduled as *{kind}*{gym}, "
            "but today is recovery only.\n"
            "Prescription: total rest, 20 min 7/15 breathing, "
            "magnesium, in bed by 10pm."
        )
    # Daily recalibration: am I on track to hit the weekly target? If
    # behind, append a redistribution proposal so the user knows exactly
    # which days to convert from rest → CrossFit (or where to extend Z2)
    # to close the gap. This is the "review every morning + tell me how
    # to get to the goal" loop.
    recalc = compute_week_recalibration(now)
    # Surface the active goal/commitment if one exists in the memory
    # substrate — so morning briefs reflect the user's real-time intent
    # (e.g. hammer week pushing for 198 lbs by May 22) rather than just
    # the locked default plan.
    goal_line = ""
    try:
        from core import memory as _mem
        # list_entities() defaults to status='active' and excludes
        # entities past their valid_until — anything returned here is
        # currently in force.
        for ent in _mem.list_entities("scientist", type="goal"):
            payload = ent.get("payload") or {}
            tlbs = payload.get("target_lbs")
            if tlbs is None and payload.get("target_kg"):
                try:
                    tlbs = round(float(payload["target_kg"]) * 2.20462, 1)
                except Exception:
                    pass
            # Extractor canonical key is target_date_iso; tolerate target_date too.
            tdate = (payload.get("target_date_iso")
                     or payload.get("target_date"))
            if tlbs and tdate:
                goal_line = (f"\n🎯 Goal: *{tlbs} lbs by "
                             f"{str(tdate)[:10]}* (committed).")
                break
    except Exception:
        pass
    base = (
        f"☀️ *Morning brief — {WEEKDAY_NAME[row['weekday']]}*\n"
        f"Today: *{kind}*{gym}. Ideal burn: {fmt_kcal(row['target_kcal'])}.\n"
        f"Week so far: {fmt_kcal(burned)} / {fmt_kcal(target)}."
        f"{goal_line}"
    )
    extra_lines: list[str] = []
    # Surface missed workouts FIRST — they're often the cause of the
    # gap, and the user wants them called out explicitly per the
    # 2026-05 spec ("if burn < 700, treat as missed and recalibrate").
    if recalc.get("missed"):
        names = ", ".join(
            f"{m['weekday_name']} {m['day_type_label']} ({fmt_kcal(m['actual_burn'])})"
            for m in recalc["missed"])
        extra_lines.append("")
        extra_lines.append(f"⚠️ *Missed: {names}.* Treating as rest days.")
    if recalc["on_track"] and not recalc.get("missed"):
        return base
    # Behind or has missed workouts — append the gap summary +
    # redistribution proposal so the user has a concrete action.
    extra_lines.append("")
    extra_lines.append(recalc["summary"])
    if recalc["proposal"]:
        names = " ".join(p["weekday_name"] for p in recalc["proposal"])
        extra_lines.append(
            f"_Apply with `pick {names} for crossfit` to lock it in._")
    return base + "\n" + "\n".join(extra_lines)


def maybe_recovery_nudge() -> str | None:
    """At 21:00, if today's burn is below the day-type target, recommend recovery."""
    now = datetime.now()
    if now.hour != NUDGE_RECOVERY_HOUR:
        return None
    today = now.strftime("%Y-%m-%d")
    if nudge_already_sent("recovery_21", today):
        return None
    today_burn = burn_for_date(now)
    row = today_plan()
    target = row["target_kcal"]
    if target == 0 or today_burn >= target:
        return None
    deficit = target - today_burn
    kind = DAY_TYPE_LABEL[row["day_type"]]
    mark_nudge("recovery_21", today)
    if row["day_type"] == "rest":
        prescription = ("25–35 min easy walk + 5 min mobility "
                        "(couch stretch / pigeon / thread-the-needle).")
    elif row["day_type"] == "z2":
        prescription = ("Skipped the run? Cap the loss with a 30-min brisk "
                        "walk + 10 min thoracic mobility.")
    else:
        prescription = ("Add a 20-min Zone-2 walk + 100 air squats, or "
                        "log it to workout_log if you trained off-watch.")
    return (
        f"🌙 9pm check — {kind} day. Today: {fmt_kcal(today_burn)} / "
        f"{fmt_kcal(target)} ({fmt_kcal(deficit)} short).\n"
        f"{prescription}"
    )


def maybe_walk_nudge() -> str | None:
    """During waking hours on a lagging day, nudge a walk vs the day-type pace.
    Performance-flavored; suppressed if another agent has vetoed walk nudges,
    or if the Scientist's own HRV check is in the RED band."""
    now = datetime.now()
    if not (NUDGE_HOURLY_START <= now.hour <= NUDGE_HOURLY_END):
        return None
    today = now.strftime("%Y-%m-%d")
    slot = f"walk_{now.hour:02d}"
    if nudge_already_sent(slot, today):
        return None
    vetoed, _ = check_external_veto("walk_nudge")
    if vetoed:
        mark_nudge(slot, today)   # respect the throttle so we don't re-check every minute
        return None
    downgrade, _ = _internal_safety_downgrade()
    if downgrade:
        mark_nudge(slot, today)
        return None
    row = today_plan()
    target = row["target_kcal"]
    if target == 0:
        return None
    today_burn = burn_for_date(now)
    # On scheduled training days, don't nag in the morning before they've trained.
    if row["day_type"] in ("cf", "z2") and now.hour < 14 and today_burn < target:
        return None
    # On rest days, only nudge if behind a non-trivial floor.
    if row["day_type"] == "rest" and today_burn >= NONWORKOUT_BURN_FLOOR:
        return None
    span = NUDGE_HOURLY_END - NUDGE_HOURLY_START + 1
    elapsed = max(now.hour - NUDGE_HOURLY_START + 1, 1)
    pace = target * (elapsed / span)
    if today_burn >= 0.7 * pace:
        return None
    mark_nudge(slot, today)
    if row["day_type"] == "rest":
        suggestion = ("Take a 10–15 min walk *or* a 10-min stretch/cooldown "
                      "(pigeon, couch stretch, thoracic foam roller).")
    else:
        suggestion = "Take a 10–15 min walk this hour."
    return (
        f"🚶 Pace check ({now.strftime('%-I%p')}) — "
        f"{DAY_TYPE_LABEL[row['day_type']]} day. "
        f"Today: {fmt_kcal(today_burn)} vs pace {fmt_kcal(pace)}. {suggestion}"
    )


def maybe_weekly_reset() -> str | None:
    """Sun 23:55 → recap the week ending tonight + lock in the next week's target.

    Workout week is Mon 00:00 → Sun 23:59 local time. The fresh `weekly_campaigns`
    row written here makes the new week's target queryable from anywhere
    (dashboards, future agents) without needing to re-derive it from the tier.
    Throttled once per week_key via nudge_log so a slow Sunday doesn't double-fire.
    """
    now = datetime.now()
    if now.weekday() != 6 or now.hour != 23 or now.minute < 55:
        return None
    monday, sunday_end = week_bounds(now)
    week_key = monday.strftime("%Y-%m-%d")
    if nudge_already_sent("week_reset", week_key):
        return None

    burned = burn_for_range(monday, sunday_end)
    target = weekly_target()
    pct = (burned / target * 100) if target else 0
    tier = state_get("recovery_tier", DEFAULT_TIER)
    next_mon = monday + timedelta(days=7)

    # Lock in next week's campaign row at the active tier's weekly target.
    con = _db()
    try:
        con.execute(
            "INSERT OR IGNORE INTO weekly_campaigns "
            "(week_start, target_active_calories) VALUES (?, ?)",
            (next_mon.strftime("%Y-%m-%d"), target))
        con.commit()
    finally:
        con.close()

    mark_nudge("week_reset", week_key)
    delta = burned - target
    verdict = (f"+{fmt_kcal(delta)} over"   if delta >= 0
               else f"{fmt_kcal(-delta)} short")
    return (
        f"📅 *Week ending* {monday.strftime('%b %-d')} – {sunday_end.strftime('%b %-d')}\n"
        f"Total: *{fmt_kcal(burned)}* / {fmt_kcal(target)} ({pct:.0f}%, {verdict}).\n"
        f"\n*New week starts Monday 00:00* — tier `{tier}`.\n"
        f"Target: {fmt_kcal(target)} | Daily pad: {fmt_kcal(daily_target())}.\n"
        f"Counters reset. Fresh slate."
    )


# ─────────────────────────── Loop ─────────────────────────────
# Telegram has a hard 4096-char per-message limit. Long-form coaching
# replies (Gemini-style meal plans + weekly schedule + roadmap) can run
# 5–8K chars. We split on paragraph boundaries to preserve readability,
# fall back to mid-message splits if a single paragraph exceeds the
# cap, and log every send result so failures surface in the log.
_TELEGRAM_MAX_CHARS = 4000  # 96-char headroom for the parse_mode wrapper


def _split_for_telegram(text: str, limit: int = _TELEGRAM_MAX_CHARS) -> list[str]:
    """Split `text` into <= limit-char chunks. Prefer paragraph
    boundaries (\\n\\n), fall back to single newlines, then to hard
    char cuts as a last resort. Markdown formatting is preserved as
    long as code-fence and bold-pair boundaries don't fall mid-chunk
    — for that level of safety we'd need a markdown-aware splitter,
    but at our message shapes paragraph splits are sufficient.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Try paragraph split first.
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:  # too early — try line split
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:  # still too early — hard cut at space
            cut = remaining.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit  # last resort — split mid-token
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send(text: str) -> None:
    if not (TOKEN and CHAT_ID):
        print(text)
        return
    chunks = _split_for_telegram(text)
    for i, chunk in enumerate(chunks):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk,
                      "parse_mode": "Markdown"},
                timeout=15)
            if not r.ok:
                # Telegram returns 400 for malformed Markdown — retry
                # without parse_mode so the user sees the text rather
                # than nothing.
                print(f"[send] HTTP {r.status_code}: {r.text[:200]} — "
                      f"retrying chunk {i+1}/{len(chunks)} as plain text")
                r2 = requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    json={"chat_id": CHAT_ID, "text": chunk},
                    timeout=15)
                if not r2.ok:
                    print(f"[send] FALLBACK FAILED: HTTP {r2.status_code}: "
                          f"{r2.text[:200]}")
            elif len(chunks) > 1:
                print(f"[out] sent chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        except Exception as e:
            print(f"[send] EXCEPTION on chunk {i+1}/{len(chunks)}: "
                  f"{type(e).__name__}: {e}")


def start():
    if TOKEN:
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook")
    tier = state_get("recovery_tier", DEFAULT_TIER)
    print(f"🔬 Scientist live | model={MODEL_ID} | tier={tier} | db={cio.DB_PATH}")
    last_id = 0
    last_tick_minute = -1
    while True:
        try:
            if TOKEN:
                r = requests.get(
                    f"https://api.telegram.org/bot{TOKEN}/getUpdates"
                    f"?offset={last_id+1}&timeout=10").json()
                for up in r.get("result", []):
                    last_id = up["update_id"]
                    msg = up.get("message", {}) or up.get("edited_message", {})
                    txt = msg.get("text")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if not txt:
                        continue
                    print(f"[in] chat={chat_id} text={txt!r}")
                    if CHAT_ID and chat_id and chat_id != str(CHAT_ID):
                        print(f"[skip] chat_id mismatch (expected {CHAT_ID})")
                        continue
                    try:
                        reply = route(txt)
                    except Exception as e:
                        reply = f"❌ handler error: {e}"
                        print(reply)
                    send(reply)

            now = datetime.now()
            if now.minute != last_tick_minute:
                last_tick_minute = now.minute
                # Apple Watch syncs land in raw_vitals via vitals_listener
                # without going through sync_weight(). Recalibrate every
                # minute so the seeded ETAs always reflect the freshest
                # weight, regardless of source. Cheap (two UPDATEs).
                try:
                    recalibrate_intents()
                except Exception as e:
                    print(f"recalibrate tick: {e}")
                for nudge in (maybe_morning_briefing(),
                              maybe_weekly_reset(),
                              maybe_recovery_nudge(),
                              maybe_walk_nudge()):
                    if nudge:
                        send(nudge)

            time.sleep(1)
        except Exception as e:
            print(f"loop error: {e}")
            time.sleep(5)


