#!/usr/bin/env bash
# push-architecture-work.sh — clean recovery + 3 logical commits + push
#
# Why a script: the multi-line commit commands kept breaking when pasted
# into zsh because (a) the # comment lines weren't recognized without
# `interactive_comments`, and (b) the clipboard kept inserting markdown
# auto-links like [main.py](http://main.py) into the commit messages.
# Script files don't have either problem.
#
# Run from the repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/push-architecture-work.sh
#
# Or save executable + run:
#     chmod +x scripts/push-architecture-work.sh
#     ./scripts/push-architecture-work.sh
#
# The script COMMITS but does NOT push automatically. After it runs,
# review `git log --oneline -4` and then `git push` yourself.

set -euo pipefail

cd "$(dirname "$0")/.."  # repo root regardless of where the script is run from

echo "━━━ 1. Recovery: remove stale .git/index.lock ━━━"
if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
    echo "  removed"
else
    echo "  (no lock present)"
fi

echo ""
echo "━━━ 2. Sync with remote (avoid 'fetch first' rejection) ━━━"
git fetch origin
echo ""
echo "Local vs remote:"
git rev-list --left-right --count main...origin/main 2>/dev/null \
    || echo "  (couldn't compare — first push or branch mismatch)"

echo ""
read -rp "Pull --rebase origin/main now? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    git pull --rebase origin main || {
        echo ""
        echo "❌ Rebase had conflicts. Resolve them, then run:"
        echo "    git rebase --continue"
        echo "    bash scripts/push-architecture-work.sh"
        exit 1
    }
else
    echo "Skipped pull. If 'git push' fails with 'fetch first', re-run and say y."
fi

echo ""
echo "━━━ 3. Verify identity ━━━"
git_email=$(git config user.email || echo "")
git_name=$(git config user.name || echo "")
echo "  user.email = ${git_email:-<unset>}"
echo "  user.name  = ${git_name:-<unset>}"
if [ -z "$git_email" ] || [ -z "$git_name" ]; then
    echo ""
    echo "❌ Git identity not set. Run:"
    echo "    git config user.email modernthinkerbuilds@gmail.com"
    echo "    git config user.name 'Venkat Sadras'"
    exit 1
fi

echo ""
echo "━━━ 4. Commit 1 of 3 — architecture documentation + diagrams ━━━"
git add specs/
git status --short specs/
git commit -m "docs: ARB-grade architecture document with three SVG diagrams" -m "$(cat <<'BODY'
Three-plane decomposition (Control / Data / Runtime), six ADR-style
decisions with alternatives and revisit triggers, Now/Next/Later roadmap
with explicit promotion rule, reliability + operations, mobile path,
open questions. Companion SVG diagrams in specs/diagrams/.

- specs/ARCHITECTURE.md (~12,500 words)
- specs/diagrams/01-three-plane-architecture.svg
- specs/diagrams/02-now-next-later-roadmap.svg
- specs/diagrams/03-routing-and-trace-flow.svg
- specs/diagrams/README.md (embedding usage notes)
- specs/ADR-001-rahat-control-plane.md (rev 2)
- specs/RUNBOOK-miya-cutover.md
BODY
)"

echo ""
echo "━━━ 5. Commit 2 of 3 — control-plane infrastructure ━━━"
git add core/
git add agents/the_scientist/agent.py
git add agents/the_scientist/protocols.py
git add agents/the_scientist/eval_suite.py
git add agents/the_scientist/eval_via_agent.py
git add agents/the_scientist/eval_extended.py
git status --short core/ agents/the_scientist/agent.py agents/the_scientist/protocols.py \
    agents/the_scientist/eval_suite.py agents/the_scientist/eval_via_agent.py \
    agents/the_scientist/eval_extended.py
git commit -m "feat(core): three-plane control plane — orchestrator, charter, voice, eval harness" -m "$(cat <<'BODY'
- core/agent.py: Agent base contract (name, triggers, route, tick)
- core/io.py: tool helpers (Telegram, Gemini, db) — single chokepoint
- core/miya.py: orchestrator with hybrid regex+Flash router,
  Charter-mediated outbound, voice dressing
- core/charter.py: policy plane — @policy registry, approve/modify/veto
  verdicts written to governance_log
- core/decisions.py: append-only trace log with span() context manager,
  replay-ready
- core/episodes.py: episodic memory primitives (open/close/note/find)
- core/voice.py: Hyderabadi/Dakhini voice layer (deterministic
  phrasebook, idempotent, preserves data)
- core/eval.py: generalized eval harness for any agent
- core/miya_main.py + com.rahat.miya.plist: launchd entry point
- agents/the_scientist/protocols.py: extracted pure math + constants
- agents/the_scientist/agent.py: ScientistAgent(Agent) wrapper
- agents/the_scientist/eval_*.py: 142 + 142 + 46 = 330 cases, three
  independent paths, all 100% green
BODY
)"

echo ""
echo "━━━ 6. Commit 3 of 3 — Scientist enhancements + ops ━━━"
git add agents/the_scientist/main.py
git add agents/the_scientist/com.rahat.scientist.plist 2>/dev/null || true
git add scripts/ 2>/dev/null || true
git status --short agents/the_scientist/main.py scripts/
git commit -m "feat(scientist): recalibration loop, no-plan fallback, Hindi routing, LLM guardrails" -m "$(cat <<'BODY'
- compute_week_recalibration() + handle_recalibrate(): daily gap
  analysis, rest-to-CF redistribution proposal with specific picks
- maybe_morning_briefing(): appends recalibration when behind on
  weekly target
- replan_week(): falls back to Mon/Wed/Fri default when no gym plan
  synced; handle_show_plan surfaces a sync-prompt warning
- TARGET_WEIGHT_RE: tolerates typos (wil/wll) and missing aux verbs
- HINDI_AAJ_WORKOUT_RE + HINDI_STATUS_RE: Hyderabadi/Dakhini routing
  for aaj/kya chal/hai na — prevents LLM hallucination on these queries
- llm_coach prompt: anti-hallucination rules (no fabricated timelines,
  today's WOD, or numbers); Dakhini voice register
- handle_unavailable(): excludes today/tomorrow when an explicit named
  weekday is also present (compound message disambiguation)
- latest_weight(): prefers Apple Watch (raw_vitals) on same-day ties
  to manual weighin_log
- scripts/scientist.sh: launchd ergonomics (start/stop/restart/logs)
BODY
)"

echo ""
echo "━━━ 7. Done ━━━"
echo "Three commits added. Review with:"
echo ""
echo "    git log --oneline -5"
echo ""
echo "When ready, push with:"
echo ""
echo "    git push origin main"
echo ""
echo "(Or to a feature branch first for review: "
echo "    git checkout -b feat/control-plane && git push -u origin feat/control-plane)"
