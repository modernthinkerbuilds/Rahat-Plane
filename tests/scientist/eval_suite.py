"""Staff TE eval suite for the Sports Scientist agent.

Runs against an ISOLATED copy of vault/rahat.db (never touches the live
file — lesson learned the hard way). Each test asserts a substring is
present in the response. ~60 cases organized by category.

Asserts the legacy regex+handler dispatch contract. The model-first
reasoner has its own suite in `eval_reasoner.py` (B8 cases). Forcing
`RAHAT_LEGACY_DISPATCH=1` here keeps the contract crisp regardless of
which dispatcher is the production default.

Run: python3 agents/the_scientist/eval_suite.py
"""
from __future__ import annotations

import importlib.util
import os
os.environ.setdefault("RAHAT_LEGACY_DISPATCH", "1")
import shutil
import sqlite3
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from core import io as cio

# Stub google.genai so we don't need GEMINI_API_KEY for offline tests.
g = types.ModuleType("google"); sys.modules["google"] = g
ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga
class _StubClient:
    def __init__(self, *a, **k): pass
    class models:
        @staticmethod
        def list(): return []
        @staticmethod
        def generate_content(**k):
            # When LLM IS expected (fallback cases), return a stable marker.
            return type("R", (), {"text": "[LLM-FALLBACK]"})()
ga.Client = _StubClient

# Resolve paths relative to this file so the suite works on the Mac and in
# any sandbox/CI checkout — never assumes /Users/you/.
ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DB = ROOT / "vault" / "rahat.db"

# Synthesize a deterministic gym plan. The live `staging/.../weekly_plan.txt`
# is .gitignored and produced by the SugarWOD bridge — not present in CI / a
# fresh checkout. We seed every weekday with a clean, non-blacklisted WOD so
# `eligible_cf_days()` returns all 7 days and the auto-picker has real choices.
# This makes the suite hermetic: behavior is identical on the Mac, in CI, and
# inside any sandbox.
def _fixture_plan_text() -> str:
    days = ["Mon 04", "Tue 05", "Wed 06", "Thu 07", "Fri 08", "Sat 09", "Sun 10"]
    blocks = []
    for header in days:
        blocks.append("\n".join([
            header, "", "", "0",
            " Strength",
            "Back squat 5x5 @ 75%",
            "",
            "0 results",
            " WOD",
            "5 rounds for time: 400m run, 21 kettlebell swings, 12 pull-ups",
            "",
            "0 results",
        ]))
    return "\n".join(blocks) + "\n"

# Copy the live DB to a temp file so the suite is fully isolated.
tmpdir = tempfile.mkdtemp(prefix="sci_eval_")
TEST_DB = Path(tmpdir) / "rahat.db"
shutil.copy(LIVE_DB, TEST_DB)

# Hermetic plan fixture next to the test DB.
PLAN_FILE = Path(tmpdir) / "weekly_plan.txt"
PLAN_FILE.write_text(_fixture_plan_text())

# Load the agent module with the test DB path patched in.
spec = importlib.util.spec_from_file_location(
    "sci", ROOT / "agents" / "the_scientist" / "main.py")
sci = importlib.util.module_from_spec(spec); sys.modules["sci"] = sci
spec.loader.exec_module(sci)
cio.DB_PATH = TEST_DB
sci.PLAN_PATH = PLAN_FILE
# 2026-05 refactor: handler.py was extracted from main.py and now owns
# its own PLAN_PATH constant. Patch BOTH so any handler reading the
# module-local constant (handle_filter, handle_eligible_cf_days,
# handle_show_plan, parse_gym_plan) sees the test fixture.
try:
    from agents.the_scientist import handler as _h
    _h.PLAN_PATH = PLAN_FILE
except Exception:
    pass

# Reset volatile state for predictable tests.
con = sqlite3.connect(str(TEST_DB))
for t in ("user_state", "nudge_log", "weekly_plan",
          "week_preferences", "intents", "weighin_log"):
    try: con.execute(f"DELETE FROM {t}")
    except: pass
con.commit(); con.close()
sci._db().close()       # boot seed both intents
sci.handle_weight(196.0)  # establish a known starting weight

# ─────────────────────────── Test cases ───────────────────────────
# (label, query, expected_substring)
TESTS: list[tuple[str, str, str]] = [
    # ─── A. Core daily-burn lookups ──────────────────────────
    ("today bare",            "today",                                "Today ("),
    ("now",                   "now",                                  "Today ("),
    ("yesterday",             "yesterday",                            "Yesterday"),
    ("burned today (not log)","how much have I burned today",         "Today ("),
    ("calories today",        "calories today",                       "Today ("),

    # ─── B. Weekly summary ──────────────────────────
    ("this week burn",        "calories this week",                   "Week so far"),
    ("calories remaining",    "calories remaining this week",         "Remaining"),
    ("burn left",             "how much active burn is left",         "Remaining"),
    ("kcal left",             "how much kcal is left for the week",   "Remaining"),
    ("how much left",         "how much do I have left for the week", "Remaining"),
    ("last week",             "calories last week",                   "Last week"),

    # ─── C. Current weight ──────────────────────────
    ("current weight",        "what's my current weight",             "Current weight"),
    ("weight now",            "weight now",                           "Current weight"),
    ("how much weigh",        "how much do I weigh",                  "Current weight"),
    ("latest weight",         "latest weight",                        "Current weight"),

    # ─── D. Plan / schedule ──────────────────────────
    ("show plan",             "show plan",                            "This week"),
    ("schedule",              "schedule",                             "This week"),
    ("plan my week",          "plan my week",                         "This week"),
    ("which days CF",         "which days am I doing crossfit",       "This week"),
    ("which days workout",    "which days am I working out this week","This week"),
    ("rest of week",          "how about rest of the week",           "This week"),
    ("when running",          "when am I running this week",          "This week"),
    ("when running next",     "when am I running next",               "This week"),
    ("when CF",               "when am I doing CrossFit",             "This week"),
    ("show next week",        "show me next week's plan",             "Next week"),
    ("plan next week",        "what's the plan for next week",        "Next week"),
    ("next week target",      "what is the caloric target for next week", "Next week"),

    # ─── E. Workout-today disambiguation ────────────
    ("am I working out today","am I working out today",               "today"),
    ("is today CF",           "is today a CF day",                    "today"),
    ("is today rest",         "is today a rest day",                  "today"),
    ("workout today?",        "workout today?",                       "today"),
    ("today's target",        "what's my target for today",           "Today is"),
    ("daily target",          "daily target",                         "Today is"),

    # ─── F. Per-week overrides ──────────────────────
    ("can't Wed",             "I can't make Wednesday",               "Marked Wed"),
    ("skip Tue",              "skip Tuesday",                         "Marked Tue"),
    ("can't workout Thu",     "I can't workout on Thursday",          "Marked Thu"),
    ("busy Fri",              "I'm busy Friday",                      "Marked Fri"),
    ("won't make Sat",        "I won't make Saturday",                "Marked Sat"),
    ("compound w/ today",     "I can't workout on Thursday, can I work out today?",
                                                                      "Marked Thu"),
    ("curly apostrophe",      "I can’t make Wednesday",          "Marked Wed"),
    ("typo no apostrophe",    "i cant make tuesday",                  "Marked Tue"),
    ("can't make today",      "I can't make today",                   "Marked"),

    ("pick days",             "pick Mon Tue Fri for crossfit",        "Locked picks"),
    ("CF on days",            "do CF on Tue Thu Sat",                 "Locked picks"),
    ("4 CF days",             "pick Mon Tue Fri Sun for crossfit",    "Locked picks"),

    ("tolerate muscle-up",    "I'm fine with muscle-ups this week",   "Tolerating"),
    ("scale handstand",       "I can scale handstand this week",      "Tolerating"),

    ("swap days",             "swap Sunday for Monday",               "Swapped"),
    ("prefer over",           "I'd prefer Monday over Sunday",        "Swapped"),
    ("instead of",            "use Tuesday instead of Wednesday",     "Swapped"),
    ("rather than",           "Friday rather than Thursday",          "Swapped"),
    ("move day",              "move Saturday to Friday",              "Swapped"),

    ("clear prefs",           "clear preferences",                    "Cleared"),
    ("reset week",            "reset week",                           "Cleared"),

    # ─── G. Weight + timeline ──────────────────────
    ("log weight",            "wt: 197.5",                            "Weight logged"),
    ("log weight verbose",    "weight: 197.5",                        "Weight logged"),
    ("when target weight",    "when will I get to my target weight",  "Weight timeline"),
    ("target date",           "what's my target date",                "Weight timeline"),
    ("realistic timeline",    "realistic timeline",                   "Weight timeline"),
    ("sustainable pace",      "sustainable pace",                     "Weight timeline"),
    ("daily intake target",   "daily intake target",                  "Weight timeline"),
    ("by date achievable",    "want to get to 185 by Dec 1",          "Weight timeline"),
    ("by date refused",       "I want 176 lbs by July 1",             "above your sustainable"),
    ("kg target",             "how long to 80 kg",                    "Weight timeline"),

    # ─── H. Coaching ──────────────────────────────
    ("hrv yellow",            "hrv 38",                               "YELLOW"),
    ("hrv green",             "hrv 50",                               "GREEN"),
    ("hrv red",               "hrv 25",                               "RED"),
    ("hrv elite",             "hrv 85",                               "ELITE"),
    ("hrv low advice",        "my hrv feels low",                     "breathing"),
    ("7/15 breathing",        "give me 7/15 breathing",               "7/15 breathing"),
    ("box breathing",         "box breathing please",                 "Box breathing"),
    ("pre fuel",              "what should I eat before my run",      "Pre-workout"),
    ("cooldown",              "give me a cooldown",                   "Post-WOD"),
    ("stretch",               "stretching routine",                   "Post-WOD"),
    ("decide",                "should I run or do crossfit",          "for fat loss"),
    ("weigh-in when",         "when should I weigh in",               "weigh"),
    # 2026-05 handler.py refactor changed pace-check format from
    # "Today: ..." to "Today — <DayType>\nActual: *X*". Anchor on the
    # new invariants.
    ("pace",                  "pace check",                           "Today —"),
    ("on track",              "am I on track",                        "Actual:"),

    # ─── I. Tier management ────────────────────────
    ("tier survival",         "tier survival",                        "Tier set"),
    ("tier hammer",           "tier hammer",                          "Tier set"),
    ("tier baseline",         "tier baseline",                        "Tier set"),

    # ─── J. Manual logging ─────────────────────────
    ("log wod",               "wod 920",                              "Logged"),
    ("log run",               "run 1100",                             "Logged"),
    ("log walk",              "walk 250",                             "Logged"),
    ("log burned today",      "burned 1463 today",                    "Logged"),

    # ─── K. Robustness ─────────────────────────────
    ("mixed case",            "AM I WORKING OUT TODAY",               "today"),
    ("trailing whitespace",   "today  ",                              "Today ("),

    # ─── L. LLM fallback (unrelated questions) ─────
    ("greeting",              "hi",                                   "[LLM-FALLBACK]"),
    ("thanks",                "thanks",                               "[LLM-FALLBACK]"),

    # ─── M. Adversarial / real-world phrasings ─────
    ("rough phrasing",        "yo what days am i working out this week",   "This week"),
    ("with punctuation",      "Today??",                                   "Today ("),
    # Multi-question — answering with weight is reasonable (highest-priority
    # match in the router); user can ask the second question separately.
    ("multi-question",        "what's my weight, am I training today?",    "Current weight"),
    # Colloquial "I'm done for today" — TODAY_RE matching and showing
    # the burn-so-far is a reasonable interpretation.
    ("colloquial",            "I'm done for today",                        "Today ("),
    ("ambiguous time",        "running tomorrow?",                         "tomorrow"),
    ("kg in pick",             "pick Mon Wed Fri for cf",                  "Locked picks"),
    ("can't do Wednesday",    "I cannot do Wednesday this week",           "Marked Wed"),
    ("won't",                 "I won't be at the gym Friday",              "Marked Fri"),
    ("travel",                "I'm traveling Friday",                      "[LLM-FALLBACK]"),
    ("multi-skip",             "skip Tue and Wed",                         "Marked"),

    # ─── N. Math correctness (substring + check numbers) ────
    ("split target 1",        "I have 2 workouts and 1 rest day",          "Per workout day"),
    ("split target 2",        "I have 3 workouts left",                    "Per workout day"),

    # ─── O. Cadence-protection (LLM should not fabricate) ──
    # When a scheduling question slips through, the scheduling-help
    # fallback should fire — never let LLM propose its own cadence.
    # "adjust my schedule" now hits the recalibration handler — better
    # response (gap analysis + actionable picks) than the previous show-plan.
    ("vague schedule",        "can you adjust my schedule",                "Week recalibration"),
    ("vague reduce",          "I have less time this week, fewer workouts","Tell me what to change"),

    # ─── P. State persistence — overrides survive lookups ─
    ("persist: skip Wed",     "I can't make Wednesday",                    "Marked Wed"),
    ("persist: show plan",    "show plan",                                 "Wed: Active rest → ideal 0"),
    ("persist: clear",        "clear preferences",                         "Cleared"),
    ("persist: post-clear",   "show plan",                                 "Wed: CrossFit"),

    # ─── Q. Idempotency ──────────────────────────
    ("idempotent skip 1",     "I can't make Friday",                       "Marked Fri"),
    ("idempotent skip 2",     "I can't make Friday",                       "Marked Fri"),

    # ─── R. Number formatting ────────────────────
    ("weight decimal",        "wt: 197.4",                                 "Weight logged"),
    ("weight no decimal",     "wt: 197",                                   "Weight logged"),
    ("weight space",          "weight 197.4",                              "Weight logged"),

    # ─── R2. Direct gym-filter + replan handlers ──
    # handle_filter: blacklist-aware which-days view of the gym programming.
    # With the hermetic fixture every day is clean → expect at least one day
    # listed without a blocker phrase.
    ("filter days",           "filter the gym days",                       "Mon"),
    ("eligible days",         "which days are eligible",                   "Mon"),
    # handle_replan: forces a fresh auto-pick for the current week.
    ("replan",                "replan the week",                           "This week"),
    ("rebuild plan",          "rebuild plan",                              "This week"),

    # ─── T. Typo tolerance (production bugs) ─────
    # User reported "When wil I reach my target weight" (typo: "wil" not
    # "will") fell through to LLM, which then hallucinated "17 days to
    # target" — the real answer at 0.75 lb/wk is ~14 weeks. TARGET_WEIGHT_RE
    # now tolerates auxiliary-verb typos and even the bare "When I reach".
    ("typo wil",              "When wil I reach my target weight",         "Weight timeline"),
    ("typo wll",              "When wll I get to my target weight",        "Weight timeline"),
    ("missing aux",           "When I reach my target weight",             "Weight timeline"),
    ("reach phrasing",        "when will I reach my target weight",        "Weight timeline"),

    # ─── U. Hindi / Dakhini routing (production bugs) ─────
    # User is Hyderabadi and freely mixes Hindi with English. Without these
    # triggers, "Aaj crossfit hai na" / "Aaj ka workout kya hai" fall to
    # LLM which guesses today's workout without consulting the actual plan.
    # The LLM hallucinations were dangerous because they sounded authoritative.
    ("aaj crossfit",          "Aaj crossfit hai na",                       "today"),
    ("aaj workout",           "Aaj ka workout kya hai",                    "today"),
    ("aaj cf hai",            "aaj cf hai kya",                            "today"),
    ("aaj wod",               "aaj wod kya hai",                           "today"),
    ("kya chal status",       "kya chal ra hai",                           "Today —"),
    ("kaise hal",             "kaise hal hai",                             "Today —"),

    # ─── V. LLM anti-hallucination contract ─────
    # Casual greetings should fall to LLM (acceptable), but the LLM should
    # NEVER fabricate timeline math, today's workout, or specific numbers.
    # We can't easily test the LLM's output here (it's stubbed), but we
    # can verify the route correctly identifies these as LLM territory
    # rather than mis-classifying as a known intent.
    ("casual hi",             "kya chal ra miya",                          "Today —"),
    ("hello casual",          "hey miya",                                  "[LLM-FALLBACK]"),

    # ─── W. Recalibration handler — "how do I catch up?" ─────
    # User wants Miya to look at the gap vs weekly target and propose a
    # redistribution. The hermetic fixture has all 7 days CF-eligible so
    # the auto-picker fills 3 CF + 1 Z2 + 3 rest, weekly target 6,000.
    # With burned=0 at start, the user is on track (planned covers it).
    # We just verify the handler fires for various phrasings.
    ("catch up",              "how do I catch up this week",               "Week recalibration"),
    ("behind on cal",         "I'm behind on calories",                    "Week recalibration"),
    ("recalibrate",           "recalibrate the week",                      "Week recalibration"),
    ("what should I do",      "what should I do this week",                "Week recalibration"),
    ("hit my goal",           "how can I hit my weekly target",            "Week recalibration"),

    # ─── X. Next-workout handler — production bug 2026-05 ─────
    # "When is my next CrossFit session" used to fall through to the
    # LLM which gave a generic "use these commands" response. Now it
    # walks the plan and returns the next CF day with WOD details.
    ("next crossfit",         "when is my next CrossFit session",          "Next CrossFit"),
    ("next cf casual",        "next cf?",                                  "Next CrossFit"),
    ("next workout",          "when is my next workout",                   "Next workout"),
    ("my next cf",            "what's my next CrossFit day",               "Next CrossFit"),
    ("next run",              "when is my next run",                       "Next Zone-2 run"),
    ("next z2",               "next z2 day",                               "Next Zone-2 run"),

    # ─── S. Day-specific workout lookup ─────────
    # "What am I doing on Friday?" → that day's session details, not the
    # full weekly grid (PLAN_DAYS_RE) and not today's status (WORKOUT_TODAY).
    ("doing on Friday",       "what am I doing on Friday",                 "Fri:"),
    ("doing on Tuesday",      "what am I doing on Tuesday",                "Tue:"),
    ("Saturday workout",      "Saturday's workout",                        "Sat:"),
    ("Wed session",           "Wednesday session",                         "Wed:"),
    ("show me Mon",           "show me Monday",                            "Mon:"),
    ("tell me Thu",           "tell me about Thursday",                    "Thu:"),
    ("Sunday wod",            "Sunday wod",                                "Sun:"),
    ("what's Fri",            "what's Friday's WOD",                       "Fri:"),
    # Plural / multiple-day phrasings should NOT hit this — they go to
    # the weekly plan grid via PLAN_DAYS_RE.
    ("plural which days",     "which days am I working out this week",     "This week"),
]


# ─────────────────────────── Runner ───────────────────────────
def run() -> tuple[int, int, list]:
    passed = failed = 0
    failures = []
    for label, query, expected in TESTS:
        try:
            actual = sci.route(query) or ""
            if expected.lower() in actual.lower():
                passed += 1
                continue
            failed += 1
            failures.append((label, query, expected, actual[:150]))
        except Exception as e:
            failed += 1
            failures.append((label, query, expected, f"EXCEPTION: {e}"))
    return passed, failed, failures


def main():
    p, f, failures = run()
    total = p + f
    print(f"\n{'='*60}")
    print(f"  EVAL SUITE — {p}/{total} passed ({100*p/total:.0f}%)")
    print(f"{'='*60}\n")
    if failures:
        print(f"FAILURES ({len(failures)}):\n")
        for label, query, expected, actual in failures:
            print(f"  ❌ {label}")
            print(f"      query:    {query!r}")
            print(f"      expected: {expected!r}")
            print(f"      actual:   {actual[:200]!r}\n")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
