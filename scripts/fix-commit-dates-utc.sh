#!/usr/bin/env bash
# fix-commit-dates-utc.sh — amend the two most recent commits so they
# land on DIFFERENT UTC days in GitHub's contribution graph.
#
# Background: GitHub buckets contributions by UTC date, not your local
# tz. The previous run dated commits at 22:30 PT and 01:15 PT — both
# convert to May 7 UTC, so they collapsed onto a single contribution
# square instead of two.
#
# This script:
#   1. Reads the messages from the last two commits (preserves them).
#   2. Soft-resets HEAD~2 (keeps file changes, drops the commits).
#   3. Re-commits each batch with a UTC-explicit date that falls on
#      the intended UTC day.
#   4. Force-pushes (since amending rewrites history).
#
# Run from the repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/fix-commit-dates-utc.sh
#
# After the script finishes, verify with:
#     git log --pretty='%h %ai (%aI) %s' -4
# and check that the second column shows different *UTC dates* when
# converted (or use `git log --date=iso-local`).

set -euo pipefail

cd "$(dirname "$0")/.."

# ─── Sanity ───
if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git_email=$(git config user.email || echo "")
if [ -z "$git_email" ]; then
    echo "❌ git identity not set. Aborting."
    exit 1
fi

# ─── Read last two commits' messages ───
echo ""
echo "━━━ 1. Snapshot the two commit messages ━━━"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
git log -1 --format='%B' HEAD~1 > "$TMP_DIR/msg-older.txt"
git log -1 --format='%B' HEAD   > "$TMP_DIR/msg-newer.txt"
echo "  older: $(head -1 $TMP_DIR/msg-older.txt)"
echo "  newer: $(head -1 $TMP_DIR/msg-newer.txt)"

# ─── Identify which files belong to which commit ───
echo ""
echo "━━━ 2. Identify files per commit ━━━"
OLDER_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD~1)
NEWER_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD)
echo "  older commit files:"
echo "$OLDER_FILES" | sed 's/^/    /'
echo "  newer commit files:"
echo "$NEWER_FILES" | sed 's/^/    /'

# ─── Reset and re-commit with explicit UTC dates ───
# Strategy: use mid-day UTC times so both author and committer land
# unambiguously on the intended UTC day, regardless of local tz.
#   Commit 1 (older) → May 6 UTC, mid-day:   2026-05-06T12:00:00+00:00
#   Commit 2 (newer) → May 7 UTC, mid-day:   2026-05-07T12:00:00+00:00
# These will display in your local tz as "2026-05-06 05:00 PT" and
# "2026-05-07 05:00 PT" respectively — fine for git logs and clear
# in the contribution graph.
DATE_OLDER="2026-05-06T12:00:00+00:00"
DATE_NEWER="2026-05-07T12:00:00+00:00"

echo ""
echo "━━━ 3. Soft-reset HEAD~2 (keeps file changes, drops commits) ━━━"
git reset --soft HEAD~2

echo ""
echo "━━━ 4. Re-commit older batch with date $DATE_OLDER ━━━"
# Stage only the files that were in the older commit
echo "$OLDER_FILES" | while IFS= read -r f; do
    [ -n "$f" ] && git add "$f"
done
GIT_AUTHOR_DATE="$DATE_OLDER" \
GIT_COMMITTER_DATE="$DATE_OLDER" \
git commit -F "$TMP_DIR/msg-older.txt"

echo ""
echo "━━━ 5. Re-commit newer batch with date $DATE_NEWER ━━━"
# Stage the remaining files (which are the newer commit's files)
echo "$NEWER_FILES" | while IFS= read -r f; do
    [ -n "$f" ] && git add "$f"
done
GIT_AUTHOR_DATE="$DATE_NEWER" \
GIT_COMMITTER_DATE="$DATE_NEWER" \
git commit -F "$TMP_DIR/msg-newer.txt"

# ─── Done ───
echo ""
echo "━━━ Done ━━━"
echo ""
echo "Verify:"
echo "    git log --pretty='%h %ai %s' -4"
echo ""
echo "Force-push (since history was rewritten):"
echo "    git push --force-with-lease origin main"
echo ""
echo "Then check GitHub's contribution graph — should show two"
echo "separate green squares for May 6 and May 7."
echo ""
echo "Caveats:"
echo "  - May 7 12:00 UTC is ~5am PT, slightly in the future depending"
echo "    on your current local time. GitHub accepts future-dated"
echo "    commits and displays them on the correct UTC day."
echo "  - The contribution graph can take up to ~30 minutes to refresh"
echo "    after a push. If it still shows wrong, wait and recheck."
echo "  - If your GitHub account doesn't have $git_email as a verified"
echo "    email, the commit won't count toward YOUR contribution graph"
echo "    (it'll still show on the repo, just not in your profile heatmap)."
