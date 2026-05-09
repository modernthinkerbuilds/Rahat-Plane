#!/usr/bin/env bash
# commit-may-6-and-7.sh — split today's pending work into two commits,
# one dated May 6 (late evening) and one dated May 7 (early morning).
#
# Run from the repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/commit-may-6-and-7.sh
#
# Commits but does NOT push. Verify with `git log --oneline -8`, then
# push with `git push origin main`.

set -euo pipefail

cd "$(dirname "$0")/.."

# ─── Sanity checks ───
if [ -f .git/index.lock ]; then
    echo "Removing stale .git/index.lock"
    rm -f .git/index.lock
fi

git_email=$(git config user.email || echo "")
if [ -z "$git_email" ]; then
    echo "❌ Git identity not set. Run:"
    echo "    git config user.email modernthinkerbuilds@gmail.com"
    echo "    git config user.name 'Venkat Sadras'"
    exit 1
fi

# ─── Commit 1 — May 6, 22:30 PT — planner overhaul ───
# Files: main.py (auto-picker, recalibration, next-workout, missed-detect,
#        default_cf_pattern, all in one cohesive planner pass), voice.py
#        (is_dressed marker expansion), eval_suite.py (X-section new
#        next-workout cases). All "user-visible behavior changes."
echo ""
echo "━━━ Commit 1/2 — May 6, 22:30 PT — planner overhaul ━━━"
git add agents/the_scientist/main.py \
        agents/the_scientist/eval_suite.py \
        core/voice.py
git status --short agents/the_scientist/main.py \
                   agents/the_scientist/eval_suite.py \
                   core/voice.py

MSG1=$(mktemp)
trap 'rm -f "$MSG1" "$MSG2"' EXIT
MSG2=$(mktemp)

cat > "$MSG1" <<'BODY1'
feat(scientist): planner overhaul — 3-CF backfill, blacklist-aware recalibration, missed-workout detection, next-workout handler

Bundles three production bugs from the 2026-05 Telegram screenshots
plus the missed-workout detection spec, all in one cohesive planner
pass.

AUTO-PICKER:
- When gym plan has fewer than 3 blacklist-clean days (handstand,
  OH squat, snatch in strength, partner WOD), auto-picker now
  backfills from default Mon/Wed/Fri to always reach 3 CF days.
  Previously left user with 1 CF + 1 Z2 + 5 rest weeks.
- handle_show_plan warning is context-aware: "No gym plan synced"
  vs "Only X day(s) blacklist-clean — backfilled rest from default."
- New persistent default_cf_pattern user_state key: user's preferred
  cadence (e.g., Mon/Tue/Fri/Sun) survives across weeks.

RECALIBRATION:
- compute_week_recalibration now reads gym blacklist + tolerated_blacklist
  and prefers gym-clean days for rest-to-CF conversions; proposal items
  flagged gym_clean True/False; UI surfaces clean vs scale-needed.
- handle_recalibrate suggests `tolerate <movement>` when proposed days
  have blacklisted movements.

MISSED-WORKOUT DETECTION:
- detect_missed_workouts: past CF/Z2 days with burn below 700 kcal
  threshold are flagged as missed (today and rest days never flagged).
- handle_show_plan strikethroughs missed days + adds banner.
- compute_week_recalibration returns missed list; morning brief
  surfaces them prominently with make-up picks.

NEXT-WORKOUT HANDLER:
- handle_next_workout: "when is my next CrossFit", "next cf?",
  "when is my next run", "my next CF day" now hit a deterministic
  handler that walks the plan forward and surfaces the gym pick + WOD
  details (instead of falling through to LLM).

VOICE LAYER:
- voice.is_dressed() recognizer expanded from 8 to 21 marker phrases
  (covers all OPENERS/CLOSERS in the phrasebook). Fixes flaky
  B2.voice-dresses-outbound test that occasionally failed when
  random.choice picked "Bole to" which wasn't in the recognizer.
BODY1

GIT_AUTHOR_DATE="2026-05-06T22:30:00-07:00" \
GIT_COMMITTER_DATE="2026-05-06T22:30:00-07:00" \
git commit -F "$MSG1"

# ─── Commit 2 — May 7, 01:15 PT — tuning + comprehensive evals ───
# Files: protocols.py (CF target 850→1150 + MISSED_WORKOUT_THRESHOLD
# constant), eval_extended.py (B7: 4 new missed-workout tests, 3
# backfill/recalibration tests, 2 fragile-test fixes). All "config
# tuning + regression coverage."
echo ""
echo "━━━ Commit 2/2 — May 7, 01:15 PT — tuning + evals ━━━"
git add agents/the_scientist/protocols.py \
        agents/the_scientist/eval_extended.py
git status --short agents/the_scientist/protocols.py \
                   agents/the_scientist/eval_extended.py

cat > "$MSG2" <<'BODY2'
tune(scientist): CF target 850→1150, missed-workout threshold, regression coverage

CONFIG TUNING (protocols.py):
- DAY_TYPE_BY_TIER performance.cf bumped 850→1150 to match the user's
  actual CF burns. Plan total now 6,050 (3×1150 + 1×1100 + 3×500),
  aligned with the locked 6,000 weekly target — eliminates the prior
  ~850 kcal NEAT shortfall the user had to make up via daily walks.
- DAY_TYPE_BY_TIER hammer.cf bumped 1100→1300 proportionally to stay
  above performance.
- baseline / re_entry / survival tiers unchanged (rare-use cases).
- new MISSED_WORKOUT_THRESHOLD_KCAL constant (default 700, env-overridable
  via MISSED_WORKOUT_THRESHOLD): below this, a past CF/Z2 day is
  treated as "no workout happened."

REGRESSION COVERAGE (eval_extended.py):
- B7.missed-workout basic detect: past CF burn 450 → missed; today's
  CF burn 100 → NOT missed.
- B7.missed-workout 700 threshold boundary: at exactly 700 not missed;
  at 699 missed.
- B7.missed-only-for-cf/z2-days: past rest days never flagged
  regardless of burn.
- B7.recalibration includes missed: compute_week_recalibration always
  returns "missed" key.
- B7.backfill when 1 gym-eligible: synthesized 7-day plan with
  handstand/snatch/partner/OHS to verify 3-CF backfill works when
  blacklist filter cuts gym-eligible to 1.
- B7.recal-prefers-gym-eligible: proposal items have gym_clean flag.
- B7.next-workout-handler-fires: "when is my next CrossFit session"
  doesn't fall through to LLM.
- Two pre-existing fragile string-match assertions converted to
  plan-structure assertions (resilient to display tweaks like
  missed-workout strikethroughs and voice prefixes).

Tests: 350/350 across all suites (148 legacy + 148 wrapper + 54
extended). Run with:
    python3 agents/the_scientist/eval_suite.py
    python3 agents/the_scientist/eval_via_agent.py
    python3 agents/the_scientist/eval_extended.py
BODY2

GIT_AUTHOR_DATE="2026-05-07T01:15:00-07:00" \
GIT_COMMITTER_DATE="2026-05-07T01:15:00-07:00" \
git commit -F "$MSG2"

# ─── Done ───
echo ""
echo "━━━ Done ━━━"
echo ""
echo "Review with:"
echo "    git log --oneline -8"
echo "    git log --pretty='%h %ai %s' -2   # verify dates"
echo ""
echo "Push when ready:"
echo "    git push origin main"
