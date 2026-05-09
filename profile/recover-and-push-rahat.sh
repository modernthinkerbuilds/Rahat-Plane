#!/usr/bin/env bash
# recover-and-push-rahat.sh
#
# Recovery script for the divergent-history situation on Rahat-Plane.
#
# What happened:
#   - Local staging/ clone has the old My-Agent-Fleet history.
#   - Remote Rahat-Plane has a different history (force-pushed at some point).
#   - Local also has unstaged changes from earlier work.
#
# What this does:
#   1. Stashes any unstaged work (safe — recoverable via `git stash list`)
#   2. Hard-resets local main to match the remote exactly
#   3. Re-applies the README-OPENCLAW backup + new Rahat README
#   4. Commits and pushes cleanly
#
# Your earlier unstaged changes are preserved in `git stash`. To inspect them
# afterwards: `git stash list` and `git stash show -p stash@{0}`.

set -euo pipefail

# ---- config ----
USERNAME="modernthinkerbuilds"
REPO_NAME="Rahat-Plane"
LOCAL_CLONE="$HOME/developer/agency/rahat/staging"
SOURCE_README="$HOME/developer/agency/rahat/profile/README-RAHAT-REPO.md"
COMMIT_MSG="${1:-docs: replace upstream readme with Rahat positioning}"

# ---- helpers ----
log()  { printf "\033[1;36m[recover]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[recover]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[recover]\033[0m %s\n" "$*"; exit 1; }

# ---- preflight ----
[[ -f "$SOURCE_README" ]] || fail "Source README not found: $SOURCE_README"
[[ -d "$LOCAL_CLONE/.git" ]] || fail "Local clone not found at $LOCAL_CLONE"
cd "$LOCAL_CLONE"

# ---- 0. show user what state we're in ----
log "Current state:"
git status --short | head -40 || true
printf "\n"

# ---- 1. stash anything dirty ----
if ! git diff-index --quiet HEAD -- 2>/dev/null || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  STASH_NAME="pre-rahat-deploy-$(date +%s)"
  log "Stashing unstaged + untracked changes as '$STASH_NAME'..."
  git stash push -u -m "$STASH_NAME"
  log "Stashed. Recover later with: git stash list  (then: git stash pop)"
else
  log "Working tree is clean — nothing to stash."
fi

# ---- 2. fetch + hard reset to match remote ----
log "Fetching origin/main..."
git fetch origin main

log "Hard-resetting local main to match origin/main."
log "  (this discards the previous unsuccessful README commit; we'll re-apply it cleanly)"
git checkout main 2>/dev/null || git checkout -b main
git reset --hard origin/main

# ---- 3. re-apply the README change cleanly ----
# Back up upstream README if it looks like OpenClaw's and isn't already backed up
if [[ -f "README.md" && ! -f "README-OPENCLAW.md" ]] && grep -q "OpenClaw" README.md 2>/dev/null; then
  log "Backing up OpenClaw README → README-OPENCLAW.md"
  git mv README.md README-OPENCLAW.md
elif [[ -f "README-OPENCLAW.md" ]]; then
  log "README-OPENCLAW.md already exists — keeping it."
fi

log "Installing new Rahat README."
cp "$SOURCE_README" "$LOCAL_CLONE/README.md"

# ---- 4. commit ----
git add README.md
[[ -f "README-OPENCLAW.md" ]] && git add README-OPENCLAW.md

if git diff --cached --quiet; then
  warn "No changes to commit — README on remote may already match your new version."
else
  git commit -m "$COMMIT_MSG"
  log "Committed: $COMMIT_MSG"
fi

# ---- 5. push ----
log "Pushing to origin/main..."
git push origin main

printf "\n\033[1;32m✓ Done.\033[0m  Visit: https://github.com/%s/%s\n" "$USERNAME" "$REPO_NAME"

if git stash list | grep -q "pre-rahat-deploy"; then
  printf "\n\033[1;33mNote:\033[0m Your earlier unstaged changes are saved in git stash.\n"
  printf "      To see them:    git stash list\n"
  printf "      To inspect:     git stash show -p stash@{0}\n"
  printf "      To restore:     git stash pop\n"
  printf "      To discard:     git stash drop stash@{0}\n\n"
fi
