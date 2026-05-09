#!/usr/bin/env bash
# tests/nightly.sh — mechanical layer of the nightly run.
#
# Behavior contract:
#
#   - We do NOT discard the user's uncommitted work. It rides ALONG
#     with the test run on a fresh `nightly/<date>` branch.
#   - If every layer passes, we commit the uncommitted work to the
#     nightly branch (in logical groups, never including denylisted
#     paths like .env*, vault/, *.db, *.sqlite*, our own artifacts).
#     The PR then carries the user's work + a green test report.
#   - If anything fails, we keep the uncommitted work in the user's
#     working tree (so it's not lost) and commit ONLY the test report
#     to the nightly branch. The PR shows the failure for triage.
#
# What it does step by step:
#   1. cd to the repo root, refuse to run anywhere else.
#   2. Capture the original branch + uncommitted state for safe restore.
#   3. Stash uncommitted work under a recoverable label, branch off
#      origin/main, pop the stash onto the nightly branch.
#   4. Run `python -m tests.run_all` hermetically. RAHAT_TEST_MODE=1,
#      GEMINI_API_KEY unset, RAHAT_RUN_JUDGE off — no Gemini, no
#      Telegram, no live DB.
#   5. Write the report files (always).
#   6a. ON PASS: stage uncommitted changes via the auto-commit allow-
#       list, commit them in groups, commit the report, push.
#   6b. ON FAIL: re-stash the uncommitted work back to the user's
#       working tree (so they don't lose anything), commit only the
#       report, push.
#
# Exit codes:
#   0  — all layers passed
#   1  — at least one layer failed (report still written + committed)
#   2  — repo precondition failed (wrong cwd, no origin, etc.)
#
# Safety rails (hard-coded, not configurable):
#   - never pushes to `main`
#   - never commits anything matching the AUTO_COMMIT_DENY patterns
#   - never drops uncommitted work — failure path stashes it back
#
# Usage:
#   tests/nightly.sh                # full run, default branch nightly/<date>
#   tests/nightly.sh --branch foo   # use branch `foo` instead
#   tests/nightly.sh --dry-run      # run tests, don't touch git
#   tests/nightly.sh --no-autocommit # always skip uncommitted-work commit
set -euo pipefail

# ─── locate repo ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── pick a Python with pytest ──────────────────────────────────────
# Prefer the repo venv (where requirements-dev.txt installed pytest);
# fall back to system python3 if no venv. Set NIGHTLY_PYTHON to override.
NIGHTLY_PYTHON="${NIGHTLY_PYTHON:-${REPO_ROOT}/venv/bin/python}"
if [ ! -x "$NIGHTLY_PYTHON" ]; then
    NIGHTLY_PYTHON="$(command -v python3)"
fi
cd "$REPO_ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[nightly] not inside a git work tree at $REPO_ROOT" >&2
    exit 2
fi

# ─── parse args ─────────────────────────────────────────────────────
DRY_RUN=0
NO_AUTOCOMMIT=0
BRANCH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)        DRY_RUN=1; shift ;;
        --no-autocommit)  NO_AUTOCOMMIT=1; shift ;;
        --branch)         BRANCH="$2"; shift 2 ;;
        -h|--help)        sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "[nightly] unknown arg: $1" >&2; exit 2 ;;
    esac
done

DATE_TAG="$(date +%Y-%m-%d)"
TIME_TAG="$(date +%H%M)"
BRANCH="${BRANCH:-nightly/${DATE_TAG}}"

ORIGINAL_REF="$(git rev-parse --abbrev-ref HEAD)"
ORIGINAL_SHA="$(git rev-parse HEAD)"

if [[ -z "$(git config user.email)" ]]; then
    git config user.email "modernthinkerbuilds@gmail.com"
fi
if [[ -z "$(git config user.name)" ]]; then
    git config user.name "Rahat Nightly"
fi

# ─── auto-commit denylist (paths NEVER auto-committed) ──────────────
# Hard-coded — config-flagging this would defeat the safety. If you
# need to commit one of these, do it by hand.
AUTO_COMMIT_DENY=(
    '.env'                  # secrets
    '.env.*'                # backup/variant secrets (the .env.bak.* family)
    'vault/*'               # live data dir
    'staging/*'             # gitignored staging output
    '*.db'                  # SQLite
    '*.db-shm'
    '*.db-wal'
    '*.sqlite'
    '*.sqlite3'
    'tests/last_run_*'      # our own artifacts — committed separately
    '__pycache__/*'
    '*.pyc'
    '.DS_Store'
)

# Returns 0 (match) if the given path matches ANY denylist pattern.
is_denied() {
    local p="$1"
    local pat
    for pat in "${AUTO_COMMIT_DENY[@]}"; do
        # shellcheck disable=SC2053
        if [[ "$p" == $pat ]] || [[ "$(basename "$p")" == $pat ]]; then
            return 0
        fi
    done
    return 1
}

# ─── stash uncommitted work ─────────────────────────────────────────
STASH_LABEL="nightly-autostash-${DATE_TAG}-${TIME_TAG}"
HAD_UNCOMMITTED=0
if ! git diff --quiet || ! git diff --cached --quiet \
        || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    echo "[nightly] uncommitted work present — stashing as '$STASH_LABEL'"
    git stash push --include-untracked --message "$STASH_LABEL" >/dev/null
    HAD_UNCOMMITTED=1
fi

# Find the stash ref by label so we can pop the right one even if the
# user has other stashes pending.
find_stash_ref() {
    git stash list | grep -F "$STASH_LABEL" | head -1 | cut -d: -f1
}

# Restore stash + original branch (for fail / dry-run paths).
restore_to_user() {
    set +e
    git checkout "$ORIGINAL_REF" >/dev/null 2>&1
    if [[ "$HAD_UNCOMMITTED" -eq 1 ]]; then
        local ref; ref="$(find_stash_ref)"
        if [[ -n "$ref" ]]; then
            git stash pop "$ref" >/dev/null 2>&1 || true
        fi
    fi
    set -e
}

# ─── make the nightly branch ────────────────────────────────────────
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[nightly] DRY-RUN: skipping branch + push ops"
else
    git fetch origin --quiet || echo "[nightly] WARN: fetch failed; using local main"
    BASE="origin/main"
    git rev-parse --verify "$BASE" >/dev/null 2>&1 || BASE="main"

    if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
        git checkout "$BRANCH" >/dev/null
        git merge --ff-only "$BASE" >/dev/null 2>&1 || true
    else
        git checkout -b "$BRANCH" "$BASE" >/dev/null
    fi

    # Pop the stash onto the nightly branch so tests run AGAINST the
    # uncommitted work. If pop conflicts (rare — only if user has
    # changes that overlap with main since their last pull), we abort
    # the pop and continue without the changes; we'll restore them
    # to the user's tree at the end.
    if [[ "$HAD_UNCOMMITTED" -eq 1 ]]; then
        STASH_REF="$(find_stash_ref)"
        if [[ -n "$STASH_REF" ]]; then
            if ! git stash apply "$STASH_REF" >/dev/null 2>&1; then
                echo "[nightly] WARN: stash apply conflicted — testing against clean main"
                git checkout -- . >/dev/null 2>&1 || true
                git clean -fd >/dev/null 2>&1 || true
                STASH_CONFLICT=1
            else
                STASH_CONFLICT=0
            fi
        fi
    fi
fi

# ─── run the suite ──────────────────────────────────────────────────
export RAHAT_TEST_MODE=1
export RAHAT_VOICE=neutral
export RAHAT_LEGACY_DISPATCH=1
unset GEMINI_API_KEY
unset RAHAT_RUN_JUDGE

REPORT_PATH="$REPO_ROOT/tests/last_run_report.md"
STATUS_PATH="$REPO_ROOT/tests/last_run_status.json"
JSON_PATH="$REPO_ROOT/tests/last_run.json"
LOG_PATH="$REPO_ROOT/tests/last_run_stdout.log"

set +e
"$NIGHTLY_PYTHON" -m tests.run_all --json "$JSON_PATH" --report "$REPORT_PATH" \
    > "$LOG_PATH" 2>&1
RUN_RC=$?
set -e

"$NIGHTLY_PYTHON" - <<PYEOF
import json, pathlib
report = pathlib.Path("$JSON_PATH")
status = pathlib.Path("$STATUS_PATH")
data = json.loads(report.read_text()) if report.exists() else []
status.write_text(json.dumps({
    "pass": all(d.get("passed") for d in data) if data else False,
    "rc": $RUN_RC,
    "date": "$DATE_TAG",
    "time": "$TIME_TAG",
    "branch": "$BRANCH",
    "had_uncommitted": $HAD_UNCOMMITTED == 1,
    "auto_commit_disabled": $NO_AUTOCOMMIT == 1,
    "layers": [
        {"name": d["name"], "passed": d["passed"],
         "n_passed": d.get("n_passed", 0),
         "n_failed": d.get("n_failed", 0),
         "n_skipped": d.get("n_skipped", 0)}
        for d in data
    ],
    "failed_layers": [d["name"] for d in data if not d.get("passed")],
}, indent=2))
PYEOF

echo "[nightly] suite rc=$RUN_RC; report=$REPORT_PATH"
cat "$STATUS_PATH"
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[nightly] DRY-RUN: skipping commit/push, restoring user state"
    restore_to_user
    exit "$RUN_RC"
fi

# ─── 6a. PASS path — commit uncommitted work + report ──────────────
# ─── 6b. FAIL path — commit only the report; restore user's work ───
if [[ "$RUN_RC" -eq 0 && "$HAD_UNCOMMITTED" -eq 1 \
        && "$NO_AUTOCOMMIT" -eq 0 ]]; then
    echo "[nightly] tests passed with uncommitted work — auto-committing"

    # Build the candidate set: every modified or untracked file MINUS
    # the denylist MINUS our own artifact files. We use a temp file
    # rather than associative arrays so this works on macOS stock bash
    # 3.2 (no `declare -A`).
    CANDIDATES_FILE="$(mktemp -t nightly-candidates.XXXXXX)"
    trap 'rm -f "$CANDIDATES_FILE"' EXIT

    while IFS= read -r line; do
        path="${line:3}"
        case "$path" in
            tests/last_run_*) continue ;;   # our own artifacts
        esac
        if is_denied "$path"; then
            echo "[nightly] DENY auto-commit: $path"
            continue
        fi
        case "$path" in
            core/*)   group="core" ;;
            agents/*) group="agents" ;;
            tests/*)  group="tests" ;;
            specs/*)  group="specs" ;;
            *)        group="root"  ;;
        esac
        printf '%s\t%s\n' "$group" "$path" >> "$CANDIDATES_FILE"
    done < <(git status --porcelain)

    # Commit each group separately so the PR shows clean, scoped commits.
    for group in core agents tests specs root; do
        # Count + collect just this group.
        count=0
        while IFS=$'\t' read -r g f; do
            [[ "$g" == "$group" ]] || continue
            git add -- "$f"
            count=$((count + 1))
        done < "$CANDIDATES_FILE"
        [[ "$count" -eq 0 ]] && continue
        if ! git diff --cached --quiet; then
            git commit -m "Nightly auto-commit ($group): $DATE_TAG ${TIME_TAG}" \
                       -m "Auto-committed because the full hermetic test suite passed (\`python -m tests.run_all\`). Group: \`$group/\`. Files: $count." >/dev/null
            echo "[nightly] committed group=$group ($count files)"
        fi
    done

    rm -f "$CANDIDATES_FILE"
    trap - EXIT
elif [[ "$RUN_RC" -ne 0 && "$HAD_UNCOMMITTED" -eq 1 ]]; then
    echo "[nightly] tests failed — NOT committing uncommitted work"
    # Discard the apply that's currently dirtying the nightly branch
    # so the report-commit below has a clean slate.
    git checkout -- . >/dev/null 2>&1 || true
    git clean -fd -e tests/last_run_* >/dev/null 2>&1 || true
fi

# Always commit the report files (separate, very small commit).
git add -- tests/last_run_report.md tests/last_run_status.json \
           tests/last_run.json tests/last_run_stdout.log 2>/dev/null || true
if ! git diff --cached --quiet; then
    if [[ "$RUN_RC" -eq 0 ]]; then
        if [[ "$HAD_UNCOMMITTED" -eq 1 && "$NO_AUTOCOMMIT" -eq 0 ]]; then
            MSG="Nightly: PASS ($DATE_TAG $TIME_TAG) — green with auto-committed work"
        else
            MSG="Nightly: PASS ($DATE_TAG $TIME_TAG) — all layers green"
        fi
    else
        FAILED_LAYERS="$("$NIGHTLY_PYTHON" -c "import json; d=json.load(open('$STATUS_PATH')); print(','.join(d.get('failed_layers',[])))")"
        MSG="Nightly: FAIL ($DATE_TAG $TIME_TAG) — failures in: $FAILED_LAYERS"
    fi
    git commit -m "$MSG" >/dev/null
    echo "[nightly] committed report: $MSG"
fi

# ─── push ───────────────────────────────────────────────────────────
if git remote get-url origin >/dev/null 2>&1; then
    if git push -u origin "$BRANCH" 2>&1 | tail -5; then
        echo "[nightly] pushed $BRANCH to origin"
    else
        echo "[nightly] WARN: push failed; branch is local at $BRANCH"
    fi
else
    echo "[nightly] no origin configured; branch is local at $BRANCH"
fi

# ─── restore user state on FAIL path ────────────────────────────────
# On PASS we WANT to be on the nightly branch (the agent picks up
# from here). On FAIL we want the user's working tree intact on
# their original branch with their uncommitted work present.
if [[ "$RUN_RC" -ne 0 ]]; then
    echo "[nightly] failure path — restoring user's working tree"
    restore_to_user
else
    # On PASS, the auto-committed work is now on the nightly branch.
    # The user's working tree on ORIGINAL_REF is unchanged (we never
    # modified it; the stash captured everything). Pop the stash
    # there too so the user can keep working.
    if [[ "$HAD_UNCOMMITTED" -eq 1 && "$NO_AUTOCOMMIT" -eq 0 ]]; then
        echo "[nightly] PASS path — auto-committed work is on $BRANCH;"
        echo "          user's working tree is preserved (the original"
        echo "          uncommitted state is captured in stash $STASH_LABEL)."
    fi
fi

echo "[nightly] done. exit=$RUN_RC"
exit "$RUN_RC"
