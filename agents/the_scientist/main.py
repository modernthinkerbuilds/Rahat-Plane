"""
The Scientist — Rahat agent: data-driven CrossFit + Z2 coach.

Design (L8 control-plane lens):
- Deterministic core does the math (burn lookups, weekly remaining, scheduler,
  gym-day filter, weight-loss timeline, HRV interpretation, weigh-in timing,
  split-target distribution, nudge thresholds). The LLM is only invoked for
  free-form coaching questions.
- Intent router dispatches the inbound Telegram message to typed handlers.
- Background tickers (9pm recovery check, hourly walk nudge) run on the same
  poll loop. Throttling state lives in the vault DB.
- Single source of truth for athlete constants, tiers, blacklists, and
  fueling/breathing protocols at top of file.

Data sources:
- vault/rahat.db
    raw_vitals(active_calories, weight)
    weekly_campaigns(target_active_calories, ...)
    hrv_log(value, ts)             — added by this agent
    weighin_log(weight_lbs, ts)    — added by this agent
    user_state(key, value)         — added by this agent (recovery_tier, etc.)
    workout_log(kind, kcal, ts)    — added by this agent (manual entries)
    nudge_log(kind, day)           — throttling
- workspace/gym-programming/weekly_plan.txt — gym's weekly schedule

Intent surface:
  burn lookups       "today" / "yesterday" / "last week"
  weekly math        "remaining" / "left this week"
  split target       "I have 2 workouts and 1 rest day, how many each"
  scheduler          "schedule" / "plan my week"
  gym filter         "filter" / "which days"
  weigh-in timing    "when should I weigh in"
  HRV log + read     "hrv 27" / "my hrv is 44"
  breathing          "7/15" / "box breathing" / "improve hrv"
  pre-workout fuel   "what should I eat before"
  post recovery      "cooldown" / "stretch"
  run vs CF          "should I run or crossfit"
  recovery tier      "tier survival" / "tier hammer"
  manual log         "burned 1463 today" / "wod 850" / "run 1100"
  weight timeline    "to 185 by oct 15" / "how long to 84 kg"
  weight anchor      "wt 195" / "weight 195"
  fallback           LLM coach with full context (no goal-nag on day burn)
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

# ── Path bootstrap so `from agents.the_scientist.protocols import …`
# resolves regardless of whether main.py is loaded as a module ("sci"
# via eval_suite) or as a package member. Adds the repo root to
# sys.path; idempotent.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Pure math + constants live in protocols.py so other agents (Coach,
# Curriculum, etc.) can import them without pulling in this module's
# Telegram poll loop or genai client. See agents/the_scientist/protocols.py.
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

# core.io is the single source of truth for DB_PATH (centralized
# 2026-05-09 to retire the parallel sci.DB_PATH module attribute that
# 12 test sites used to patch). _db() and the startup banner read
# cio.DB_PATH at call time, so RAHAT_TEST_MODE / RAHAT_DB_PATH overrides
# and per-test cio.DB_PATH = X patches both work without any further
# wiring inside this module.
from core import io as cio  # noqa: E402

# ─────────────────────────── Config ───────────────────────────
load_dotenv()
API_KEY  = os.getenv("GEMINI_API_KEY")
TOKEN    = os.getenv("SCIENTIST_BOT_TOKEN")
CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
HOME     = Path.home()
PLAN_PATH = HOME / "developer/agency/rahat/staging/workspace/gym-programming/weekly_plan.txt"


# Thin wrappers preserve the legacy zero-arg call sites — they read
# from this module's `PLAN_PATH`, which the eval suite reassigns to
# the fixture. Behavior is identical to the pre-extract version.
def parse_gym_plan(text: str | None = None) -> list[GymDay]:
    return _proto_parse_gym_plan(text, plan_path=PLAN_PATH)


def eligible_cf_days(days: list[GymDay] | None = None) -> list[GymDay]:
    if days is None:
        days = parse_gym_plan()
    return _proto_eligible_cf_days(days)

# Athlete constants, tier tables, blacklists, and nudge tunables now
# live in agents/the_scientist/protocols.py — imported above. Remove
# duplicate definitions here to prevent silent drift.

client = genai.Client(api_key=API_KEY) if API_KEY else None


def _active_model() -> str:
    if not client:
        return "gemini-1.5-flash"
    try:
        flash = [m.name for m in client.models.list() if "flash" in m.name.lower()]
        return sorted(flash)[-1] if flash else "gemini-1.5-flash"
    except Exception:
        return "gemini-1.5-flash"


MODEL_ID = _active_model()


# ── DB helpers extracted to agents/the_scientist/state.py ──
# Phase 4d (R1) Step 1a: the connection factory, KV state get/set,
# burn-window aggregations, weekly-target resolution, and intent-ledger
# readers all moved into state.py. This re-export preserves the legacy
# `sci.<name>` import contract used by ScientistAgent's importlib loader
# and by every eval file.
from agents.the_scientist.state import *  # noqa: F401, F403, E402


# ─────────────────────── Weekly plan (3 CF + 1 Z2) ────────────
# (moved to agents/the_scientist/state.py in Phase 4d R1 Step 2a)
# day_type_target, replan_week, current_plan, today_plan all live
# in state.py now; main.py imports them via 'from state import *'.









# ───────────────────── Week recalibration ─────────────────────
# (compute_week_recalibration + detect_missed_workouts moved to
# state.py in Phase 4d R1 Step 2a. handle_recalibrate (a handler)
# stays here until Step 2b folds it into handler.py.)







# ── Per-week preference overrides + logs (moved to state.py) ──
# Phase 4d (R1) Step 1b: get_prefs/set_prefs/clear_prefs,
# latest_weight/sync_weight/recalibrate_intents,
# log_hrv/log_workout/last_hammer_day/nudge_already_sent/mark_nudge
# moved to agents/the_scientist/state.py. state.py mirrors main.py's
# full protocols import list, so any constant main.py uses (HAMMER_KCAL,
# INTENT_*_LBS, etc.) is available to the moved functions too. The
# Step-1a `from agents.the_scientist.state import *` re-export covers
# these names; the `import json as _json` alias rode along.


# ───────────────────────── Gym plan ──────────────────────────
# DAY_HEADER, GymDay, parse_gym_plan, eligible_cf_days, fmt_kcal, fmt_lbs
# all imported from protocols.py (top of file). The PLAN_PATH-aware
# wrappers `parse_gym_plan` and `eligible_cf_days` defined right after
# the protocols import preserve the legacy zero-arg call sites.



# ── Handlers + router + nudges + loop (extracted to handler.py) ──
# Phase 4d (R1) Step 2b: handle_recalibrate + Sections 8-11 of main.py
# all moved to agents/the_scientist/handler.py. This star re-export
# preserves the legacy sci.<name> import contract used by ScientistAgent's
# importlib loader and by the eval suite.
from agents.the_scientist.handler import *  # noqa: F401, F403, E402

if __name__ == "__main__":
    start()
