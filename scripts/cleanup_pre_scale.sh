#!/usr/bin/env bash
# Pre-scale cleanup — clean slate before expanding the agent mesh.
# Safe by default: DRY-RUN unless you pass --apply. Destructive doc/branch
# pruning and the launchd change are behind their own explicit flags so
# nothing irreversible happens by accident.
#
#   ./scripts/cleanup_pre_scale.sh                 # dry-run, show everything
#   ./scripts/cleanup_pre_scale.sh --apply         # delete zero-risk caches only
#   ./scripts/cleanup_pre_scale.sh --apply --archives   # + superseded archives/runbooks (git-tracked, recoverable)
#   ./scripts/cleanup_pre_scale.sh --disable-old-kobe   # launchctl disable com.rahat.miya (cutover lock-in)
#
# Inventory rationale: specs/PRE_SCALE_PLAN_2026-06-14.md §A, §G.
set -euo pipefail
cd "$(dirname "$0")/.."

APPLY=0; ARCHIVES=0; DISABLE_KOBE=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --archives) ARCHIVES=1 ;;
    --disable-old-kobe) DISABLE_KOBE=1 ;;
    *) echo "unknown flag: $a"; exit 2 ;;
  esac
done
run() { if [ "$APPLY" = 1 ]; then echo "  rm: $1"; rm -rf "$1"; else echo "  would rm: $1 ($(du -sh "$1" 2>/dev/null | cut -f1))"; fi; }

echo "== 1. Zero-risk caches (regenerable, gitignored) =="
# __pycache__, pyc, pytest cache — never load-bearing.
if [ "$APPLY" = 1 ]; then
  find . -path ./venv -prune -o -path ./.venv -prune -o -name '__pycache__' -type d -print -exec rm -rf {} + 2>/dev/null | sed 's/^/  rm: /' || true
  find . -path ./venv -prune -o -path ./.venv -prune -o -name '*.pyc' -delete 2>/dev/null || true
  rm -rf .pytest_cache && echo "  rm: .pytest_cache"
else
  echo "  would rm: all __pycache__/, *.pyc, .pytest_cache (outside venv/) — ~$(find . -path ./venv -prune -o -name '__pycache__' -type d -print 2>/dev/null | wc -l | tr -d ' ') dirs"
fi
echo "  (transient test-runner outputs)"
for f in tests/last_run.json tests/last_run_report.md tests/last_run_status.json tests/last_run_stdout.log; do
  [ -e "$f" ] && run "$f"
done

echo
echo "== 2. Superseded archives + stale runbooks (git-tracked; recoverable via git) =="
echo "   (only acts with --archives)"
STALE=(
  "specs/archive/2026-06-10"
  "private/archive_2026-05-30"
  "specs/RUNBOOK-miya-cutover.md"        # documents old Scientist->Miya v1 swap; superseded by PHASE_E_CUTOVER_RUNBOOK
  "specs/RUNBOOK-model-first-cutover.md" # 2026-05 Gemini pivot, executed
)
for p in "${STALE[@]}"; do
  [ -e "$p" ] || continue
  if [ "$ARCHIVES" = 1 ] && [ "$APPLY" = 1 ]; then echo "  git rm: $p"; git rm -r --quiet "$p" 2>/dev/null || rm -rf "$p";
  else echo "  would remove (with --archives --apply): $p ($(du -sh "$p" 2>/dev/null | cut -f1))"; fi
done

echo
echo "== 3. PARKED — do NOT delete (ADR-014: OpenClaw kept ready) =="
echo "  KEEP: new_plane/openclaw_plugin/  bridges/openclaw_adapters/  staging/fleet/  core/huberman_bridge.py"

echo
echo "== 4. Ambiguous — review before deleting (left untouched) =="
echo "  specs/OPENCLAW_INTEGRATION_GUIDE.md, specs/L8_AGENT_ARCHITECT_KICKOFF_PROMPT.md,"
echo "  duplicate L9 vs L9_L10 handoff in specs/test_lead/findings/, specs/RAHAT_COMMERCIALIZATION_PARKED.md"

echo
echo "== 5. Stale git branches (review; not auto-deleted) =="
git for-each-ref --format='  %(refname:short)  [%(upstream:track)]' refs/heads/ 2>/dev/null | grep -vE ' main ' || true
echo "  candidates to prune once confirmed merged/absorbed:"
echo "   feat/adr-011-llm-core, fix/2026-06-08-morning-brief-..., test-lead-agent-2026-06-10"
echo "   KEEP-parked: feat/new-plane-stage0 (Stage-0 scaffold)"

echo
echo "== 6. Lock in the cutover (launchd) =="
if [ "$DISABLE_KOBE" = 1 ]; then
  echo "  disabling com.rahat.miya so it can't auto-start on reboot..."
  launchctl disable "gui/$UID/com.rahat.miya" && echo "  done. (undo: launchctl enable gui/$UID/com.rahat.miya)"
else
  echo "  (run with --disable-old-kobe, OR manually:)"
  echo "    launchctl disable gui/\$UID/com.rahat.miya"
fi
echo "  verify only v2 remains:  launchctl list | grep com.rahat.miya"

echo
echo "Done (mode: APPLY=$APPLY ARCHIVES=$ARCHIVES DISABLE_KOBE=$DISABLE_KOBE)."
[ "$APPLY" = 0 ] && echo "DRY-RUN — re-run with --apply to act."
