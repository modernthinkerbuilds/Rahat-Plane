#!/usr/bin/env bash
# finish-commit-3.sh — commit the third batch (Scientist enhancements + ops)
# after the heredoc bug in push-architecture-work.sh.
#
# Run from the repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/finish-commit-3.sh
#
# Commits but does not push automatically. Review with `git log` then push.

set -euo pipefail

cd "$(dirname "$0")/.."

# Build the commit message in a temp file — sidesteps the apostrophe-in-heredoc
# issue that crashed push-architecture-work.sh on line 138.
MSG_FILE=$(mktemp)
trap 'rm -f "$MSG_FILE"' EXIT

cat > "$MSG_FILE" <<MSG
feat(scientist): recalibration loop, no-plan fallback, Hindi routing, LLM guardrails

- compute_week_recalibration() + handle_recalibrate(): daily gap
  analysis, rest-to-CF redistribution proposal with specific picks
- maybe_morning_briefing(): appends recalibration when behind on
  weekly target
- replan_week(): falls back to Mon/Wed/Fri default when no gym plan
  synced; handle_show_plan surfaces a sync-prompt warning
- TARGET_WEIGHT_RE: tolerates typos (wil/wll) and missing aux verbs
- HINDI_AAJ_WORKOUT_RE + HINDI_STATUS_RE: Hyderabadi/Dakhini routing
  for aaj/kya chal/hai na (prevents LLM hallucination on these queries)
- llm_coach prompt: anti-hallucination rules (no fabricated timelines,
  WOD claims, or numbers); Dakhini voice register
- handle_unavailable(): excludes today/tomorrow when an explicit named
  weekday is also present (compound message disambiguation)
- latest_weight(): prefers Apple Watch (raw_vitals) on same-day ties
  to manual weighin_log
- scripts/scientist.sh: launchd ergonomics (start/stop/restart/logs)
MSG

echo "━━━ Staging ━━━"
git add agents/the_scientist/main.py
git add scripts/ 2>/dev/null || true
git add agents/the_scientist/com.rahat.scientist.plist 2>/dev/null || true
git status --short

echo ""
echo "━━━ Committing (message from temp file, no quoting issues) ━━━"
git commit -F "$MSG_FILE"

echo ""
echo "━━━ Done ━━━"
echo "Review with:  git log --oneline -5"
echo "Push with:    git push origin main"
