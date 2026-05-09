#!/usr/bin/env bash
# scripts/jobs/evolve.sh — every-other-night test + doc evolution.
#
# Triggered by ~/Library/LaunchAgents/com.rahat.evolve.plist at 02:00,
# but only on odd-numbered days of the month (the plist's StartCalendar-
# Interval array is configured for that). This isn't strictly every other
# day, but it's close enough and keeps the launchd config simple.
#
# Goals (per the user spec, 2026-05-09):
#   1. Scan the last 48h of commits for code paths that gained behavior
#      but no test coverage.
#   2. Author 5-10 new test cases / evals targeting those gaps.
#   3. Refresh README sections that reference moved or renamed files.
#   4. Open a PR against main with the proposed changes.
#
# Safety posture:
#   - This job NEVER pushes to main directly.
#   - All proposed changes go on a branch `evolve/<date>` and a PR is
#     opened for human review.
#   - The "author tests via LLM" step is the riskiest piece — see
#     CAVEATS below.
#
# CURRENT STATUS — 2026-05-09:
#   This script is SCAFFOLDED, not finished. The auto-author step is a
#   stub that prints what it WOULD do but does not yet write or commit
#   anything. We need at least one human-reviewed cycle before we trust
#   the LLM to author tests that gate auto-commits to main.
#
# How it'll work once implemented:
#   - `git log --since="48 hours ago" --name-only --format=%H` to get
#     touched files.
#   - For each changed core/* or agents/* file, find any new functions
#     or new branches in those functions.
#   - For each new symbol, compose a prompt: "Here's the code; here are
#     existing tests that match the file's style; write 1-3 new tests
#     that exercise this symbol's behavior + edge cases."
#   - Save to tests/proposed/<date>/test_*.py (NOT tests/ proper — humans
#     review and move).
#   - Run the suite WITH the proposed tests included; if any fail, the
#     proposed test is wrong (or the code is wrong) — the PR description
#     surfaces both possibilities.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/vault/jobs"
mkdir -p "$LOG_DIR"
JOB_LOG="$LOG_DIR/evolve.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$JOB_LOG" >&2; }

log "=== evolve.sh start ==="
log "STATUS: scaffold — auto-author step is a stub."

# 1. What changed in the last 48h?
CHANGED="$(git log --since='48 hours ago' --name-only --pretty=format: \
           --diff-filter=AM 2>/dev/null \
           | sort -u | grep -E '^(core|agents)/.*\.py$' || true)"

if [ -z "$CHANGED" ]; then
    log "no core/ or agents/ Python files changed in the last 48h — nothing to evolve"
    exit 0
fi

log "files touched in last 48h:"
echo "$CHANGED" | sed 's/^/    /' | tee -a "$JOB_LOG" >&2

# 2. STUB: would author tests here. For now, just report.
PROPOSED_DIR="$REPO_ROOT/tests/proposed/$(date +%Y-%m-%d)"
mkdir -p "$PROPOSED_DIR"
cat > "$PROPOSED_DIR/PROPOSAL.md" <<EOF
# Evolve proposal — $(date +%Y-%m-%d)

This file was written by \`scripts/jobs/evolve.sh\`. The script is currently
in scaffold mode — it identifies code that would benefit from new tests
but does not yet author them. A human (you) reviews this proposal and
either runs the LLM author step manually or writes the tests directly.

## Files changed in the last 48h

\`\`\`
$CHANGED
\`\`\`

## Recommended manual next step

\`\`\`bash
# Open one of the changed files and grep for symbols that don't appear
# in tests/:
for f in $CHANGED; do
    for sym in \$(grep -oE '^def [a-z_][a-z0-9_]*' "\$f" | awk '{print \$2}'); do
        if ! grep -rq "\$sym" tests/ 2>/dev/null; then
            echo "UNTESTED: \$f :: \$sym"
        fi
    done
done
\`\`\`

When you're ready to wire in LLM-authored tests, replace the stub
section in \`scripts/jobs/evolve.sh\` (search for \`# 2. STUB\`).
EOF

log "wrote $PROPOSED_DIR/PROPOSAL.md"

# 3. NOT YET: doc refresh. README scanning is its own can of worms; deferred.
log "doc-refresh step: deferred (see TODO in script header)"

# 4. NOT YET: PR open. Will be wired up after the author step is real.
log "PR open: deferred (no real changes proposed yet)"

log "=== evolve.sh done ==="
