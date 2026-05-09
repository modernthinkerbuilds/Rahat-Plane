#!/usr/bin/env bash
# scripts/jobs/greenstreak.sh — auto-commit uncommitted work to main on green.
#
# Triggered by ~/Library/LaunchAgents/com.rahat.greenstreak.plist at 23:30.
# Depends on regression.sh having already produced tests/last_run_status.json
# at 23:00. If status.pass is false, this script does nothing (loudly).
#
# Per the user's choice on 2026-05-09: "everything if all 5 layers green."
# This means EVERY uncommitted file (subject to the denylist below) lands
# on main. Doc-only mode is implemented in code via $GREENSTREAK_DOCS_ONLY=1
# but defaults off.
#
# Hard denylist (NEVER auto-committed regardless of test outcome):
#   .env, .env.*, vault/*, staging/*, *.db*, *.sqlite*, tests/last_run_*,
#   __pycache__/*, *.pyc, .DS_Store
#
# What this script does:
#   1. Read tests/last_run_status.json. Bail with rc=0 (no-op) if pass=false.
#   2. cd to main. Pull --ff-only.
#   3. Stage every modified/untracked file MINUS the denylist.
#   4. Group commits by directory (core/, agents/, tests/, specs/, profile/, root).
#   5. Commit the report files in a separate small commit.
#   6. Push main. (Push is enabled iff $GREENSTREAK_PUSH=1, default 1.)
#
# Why commit-by-group: a single mega-commit is unreadable in `git log`.
# Grouping by directory matches how you actually think about the repo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/vault/jobs"
mkdir -p "$LOG_DIR"
JOB_LOG="$LOG_DIR/greenstreak.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$JOB_LOG" >&2; }

DOCS_ONLY="${GREENSTREAK_DOCS_ONLY:-0}"   # 0 = commit everything, 1 = docs/tests/specs only
DO_PUSH="${GREENSTREAK_PUSH:-1}"          # 0 = stay local, 1 = push

log "=== greenstreak.sh start (DOCS_ONLY=$DOCS_ONLY DO_PUSH=$DO_PUSH) ==="

STATUS="$REPO_ROOT/tests/last_run_status.json"
if [ ! -f "$STATUS" ]; then
    log "ABORT: no $STATUS — regression.sh hasn't run yet (or failed before writing)"
    exit 0
fi

PY="$REPO_ROOT/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

PASS=$("$PY" -c "import json,sys; print(json.load(open('$STATUS')).get('pass'))")
if [ "$PASS" != "True" ]; then
    log "ABORT: status.pass=$PASS — not committing red builds to main"
    exit 0
fi
log "status.pass=True — proceeding"

# Identity (idempotent — git config is a no-op if already set)
[ -z "$(git config user.email)" ] && git config user.email "modernthinkerbuilds@gmail.com"
[ -z "$(git config user.name)" ]  && git config user.name "Rahat Greenstreak"

# Land on main. Reject if we're not on a clean main (don't auto-merge stuff).
CURBR="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURBR" != "main" ]; then
    log "current branch is '$CURBR' — checking out main"
    # Stash anything uncommitted so checkout can succeed.
    git stash push --include-untracked --message "greenstreak-pre-checkout-$(date +%s)" >/dev/null || true
    git checkout main
    git stash pop >/dev/null 2>&1 || true
fi

# Pull (ff-only — never auto-merge).
git fetch origin --quiet
git merge --ff-only origin/main >/dev/null 2>&1 || \
    log "WARN: main is not ff-fast-forwardable to origin/main — local commits ahead?"

DATE_TAG="$(date +%Y-%m-%d)"
TIME_TAG="$(date +%H%M)"

# Denylist match function — returns 0 (match) if path matches any pattern.
DENY=( '.env' '.env.*' 'vault/*' 'staging/*' '*.db' '*.db-shm' '*.db-wal'
       '*.sqlite' '*.sqlite3' 'tests/last_run_*' '__pycache__/*' '*.pyc' '.DS_Store' )
is_denied() {
    local p="$1"
    for pat in "${DENY[@]}"; do
        # shellcheck disable=SC2053
        [[ "$p" == $pat ]] || [[ "$(basename "$p")" == $pat ]] && return 0
    done
    return 1
}

# Doc-only filter — if GREENSTREAK_DOCS_ONLY=1, only allow paths that
# match these "low-risk" globs.
SAFE_GLOBS=( 'README*.md' 'docs/*' 'specs/*' 'tests/*' 'profile/*' '*.md' )
is_safe_only() {
    local p="$1"
    for pat in "${SAFE_GLOBS[@]}"; do
        # shellcheck disable=SC2053
        [[ "$p" == $pat ]] || [[ "$(basename "$p")" == $pat ]] && return 0
    done
    return 1
}

# Build candidate list, grouped.
CANDIDATES="$(mktemp -t greenstreak.XXXXXX)"
trap 'rm -f "$CANDIDATES"' EXIT

while IFS= read -r line; do
    path="${line:3}"
    case "$path" in
        tests/last_run_*) continue ;;   # report commit handles these
    esac
    if is_denied "$path"; then
        log "DENY: $path"
        continue
    fi
    if [ "$DOCS_ONLY" = "1" ] && ! is_safe_only "$path"; then
        log "SKIP (docs-only mode): $path"
        continue
    fi
    case "$path" in
        core/*)    group="core" ;;
        agents/*)  group="agents" ;;
        tests/*)   group="tests" ;;
        specs/*)   group="specs" ;;
        profile/*) group="profile" ;;
        bridges/*) group="bridges" ;;
        scripts/*) group="scripts" ;;
        *)         group="root" ;;
    esac
    printf '%s\t%s\n' "$group" "$path" >> "$CANDIDATES"
done < <(git status --porcelain)

if [ ! -s "$CANDIDATES" ]; then
    log "no candidates — nothing to commit"
    # Still commit the report files in their own commit (the daily heartbeat).
fi

COMMIT_COUNT=0
for group in core agents tests specs profile bridges scripts root; do
    GROUP_FILES=()
    while IFS=$'\t' read -r g f; do
        [ "$g" = "$group" ] && GROUP_FILES+=("$f")
    done < "$CANDIDATES"
    [ "${#GROUP_FILES[@]}" -eq 0 ] && continue
    git add -- "${GROUP_FILES[@]}"
    if git diff --cached --quiet; then
        log "group=$group: nothing to commit (already clean after add)"
        continue
    fi
    git commit \
        -m "greenstreak ($group): $DATE_TAG $TIME_TAG" \
        -m "Auto-committed because the full hermetic test suite passed (\`python -m tests.run_all\`)." \
        -m "Group: \`$group/\`. Files: ${#GROUP_FILES[@]}." >/dev/null
    log "committed group=$group (${#GROUP_FILES[@]} files)"
    COMMIT_COUNT=$((COMMIT_COUNT + 1))
done

# Always commit the report files in their own tiny commit — the daily
# heartbeat that keeps the contribution graph alive even on pure-green-
# no-changes nights.
git add -- tests/last_run_report.md tests/last_run_status.json \
           tests/last_run.json tests/last_run_stdout.log 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -m "greenstreak: daily test heartbeat $DATE_TAG $TIME_TAG" >/dev/null
    log "committed report heartbeat"
    COMMIT_COUNT=$((COMMIT_COUNT + 1))
fi

log "total commits this run: $COMMIT_COUNT"

# Push.
if [ "$DO_PUSH" = "1" ] && [ "$COMMIT_COUNT" -gt 0 ]; then
    if git push origin main 2>&1 | tail -3 >> "$JOB_LOG"; then
        log "pushed main to origin"
    else
        log "WARN: push failed — main is ahead locally; will retry tomorrow"
    fi
fi

log "=== greenstreak.sh done ==="
exit 0
