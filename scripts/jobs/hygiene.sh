#!/usr/bin/env bash
# scripts/jobs/hygiene.sh — daily repo + system cleanup.
#
# Triggered by ~/Library/LaunchAgents/com.rahat.hygiene.plist at 00:30.
# Idempotent — running it twice in a row produces no change on the
# second run. That's the whole point of hygiene: small, deterministic,
# boring.
#
# What it does:
#   1. Sweep __pycache__/, *.pyc, .DS_Store, .lock.cleared.* leftovers
#      from the Cowork-sandbox runs.
#   2. Vacuum the SQLite ledger (`vault/rahat.db`) — reclaims pages,
#      keeps the file from growing unboundedly. Skipped if RAHAT_TEST_MODE=1.
#   3. Rotate vault/jobs/*.log if any exceeds 5 MB.
#   4. Prune merged-into-main branches (local + tracking refs).
#   5. `git gc --auto` so .git/ stays compact.
#   6. Tail the regression log; if last run failed, write a marker file
#      `vault/jobs/ALERT_REGRESSION_RED` so a later notifier can surface it.
#
# Non-goals:
#   - No git commits.
#   - No git push.
#   - No code changes.
#   - No test runs.
#
# This script touches the filesystem but leaves the repo's commit graph
# alone. Safe to run as the very last job of the night.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/vault/jobs"
mkdir -p "$LOG_DIR"
JOB_LOG="$LOG_DIR/hygiene.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$JOB_LOG" >&2; }

log "=== hygiene.sh start ==="

# 1. Sweep caches + sandbox leftovers.
SWEPT=0
while IFS= read -r p; do
    rm -rf "$p" 2>/dev/null && SWEPT=$((SWEPT + 1)) || true
done < <(find . -type d -name __pycache__ -not -path './venv/*' -not -path './.git/*' 2>/dev/null)
while IFS= read -r p; do
    rm -f "$p" 2>/dev/null && SWEPT=$((SWEPT + 1)) || true
done < <(find . -type f \( -name '*.pyc' -o -name '.DS_Store' \) -not -path './venv/*' -not -path './.git/*' 2>/dev/null)
# Cowork-sandbox leftovers — these accumulate inside .git/ from FUSE-mount
# runs that couldn't unlink lock files. Harmless but ugly.
while IFS= read -r p; do
    rm -f "$p" 2>/dev/null && SWEPT=$((SWEPT + 1)) || true
done < <(find .git -name '*.cleared*' -o -name '*.removed' -o -name '*.stale' 2>/dev/null)
while IFS= read -r p; do
    rm -f "$p" 2>/dev/null && SWEPT=$((SWEPT + 1)) || true
done < <(find .git/objects -name 'tmp_obj_*' 2>/dev/null)
log "swept $SWEPT cache/leftover entries"

# 2. SQLite vacuum — reclaim pages from deletes, keep DB compact.
DB="$REPO_ROOT/vault/rahat.db"
if [ -f "$DB" ] && [ "${RAHAT_TEST_MODE:-0}" != "1" ]; then
    BEFORE=$(stat -f%z "$DB" 2>/dev/null || stat -c%s "$DB" 2>/dev/null || echo 0)
    if sqlite3 "$DB" 'VACUUM;' 2>/dev/null; then
        AFTER=$(stat -f%z "$DB" 2>/dev/null || stat -c%s "$DB" 2>/dev/null || echo 0)
        log "vacuumed $DB: ${BEFORE}B → ${AFTER}B"
    else
        log "VACUUM skipped (sqlite3 cli missing or DB locked)"
    fi
fi

# 3. Rotate any vault/jobs/*.log over 5 MB.
for L in "$LOG_DIR"/*.log; do
    [ -f "$L" ] || continue
    SIZE=$(stat -f%z "$L" 2>/dev/null || stat -c%s "$L" 2>/dev/null || echo 0)
    if [ "$SIZE" -gt 5242880 ]; then
        mv "$L" "$L.$(date +%Y%m%d).old"
        : > "$L"
        log "rotated $L (was ${SIZE}B)"
    fi
done

# 4. Prune branches merged into main. Skip protected names.
git fetch --prune origin --quiet 2>>"$JOB_LOG" || log "fetch failed (offline?)"
PROTECTED='^(main|master|develop|HEAD|nightly/[0-9]{4}-[0-9]{2}-[0-9]{2})$'
PRUNED=0
while IFS= read -r b; do
    b="${b## }"
    [ -z "$b" ] && continue
    [ "$b" = "main" ] && continue
    if echo "$b" | grep -qE "$PROTECTED"; then continue; fi
    # Only delete if fully merged into main.
    if git branch --merged main 2>/dev/null | grep -q "^  $b\$"; then
        if git branch -d "$b" >/dev/null 2>&1; then
            log "pruned merged branch: $b"
            PRUNED=$((PRUNED + 1))
        fi
    fi
done < <(git branch | sed 's/^[* ] //')
log "pruned $PRUNED merged branches"

# 5. Compact .git.
git gc --auto --quiet 2>>"$JOB_LOG" || log "git gc skipped"

# 6. Surface regression failures from the last run.
STATUS="$REPO_ROOT/tests/last_run_status.json"
if [ -f "$STATUS" ]; then
    PY="$REPO_ROOT/venv/bin/python"
    [ -x "$PY" ] || PY="$(command -v python3)"
    PASS=$("$PY" -c "import json; print(json.load(open('$STATUS')).get('pass'))" 2>/dev/null || echo "?")
    if [ "$PASS" = "False" ]; then
        echo "regression FAILED on $(date)" > "$LOG_DIR/ALERT_REGRESSION_RED"
        log "ALERT: last regression run was RED — see $LOG_DIR/ALERT_REGRESSION_RED"
    else
        rm -f "$LOG_DIR/ALERT_REGRESSION_RED"
    fi
fi

log "=== hygiene.sh done ==="
