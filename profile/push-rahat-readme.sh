#!/usr/bin/env bash
# push-rahat-readme.sh
#
# Replaces the OpenClaw README on the Rahat-Plane repo with the new
# Rahat-positioned README, backing up the old one as README-OPENCLAW.md.
#
# Targets: https://github.com/modernthinkerbuilds/Rahat-Plane

set -euo pipefail

# ---- config ----
USERNAME="modernthinkerbuilds"
REPO_NAME="Rahat-Plane"
LOCAL_CLONE="$HOME/developer/agency/rahat/staging"
SOURCE_README="$HOME/developer/agency/rahat/profile/README-RAHAT-REPO.md"
NEW_REMOTE_URL="https://github.com/$USERNAME/$REPO_NAME.git"
COMMIT_MSG="${1:-docs: replace upstream readme with Rahat positioning}"

# ---- helpers ----
log()  { printf "\033[1;36m[rahat]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[rahat]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[rahat]\033[0m %s\n" "$*"; exit 1; }

# ---- preflight ----
[[ -f "$SOURCE_README" ]] || fail "Source README not found: $SOURCE_README"
[[ -d "$LOCAL_CLONE/.git" ]] || fail "Local clone not found at $LOCAL_CLONE. Clone Rahat-Plane there first."
command -v git >/dev/null || fail "git is not installed."

cd "$LOCAL_CLONE"

# ---- update remote URL if it still points at the old repo name ----
CURRENT_URL=$(git remote get-url origin 2>/dev/null || echo "")
if [[ "$CURRENT_URL" != "$NEW_REMOTE_URL" ]]; then
  log "Remote URL is '$CURRENT_URL'."
  log "Updating remote URL → $NEW_REMOTE_URL"
  git remote set-url origin "$NEW_REMOTE_URL"
else
  log "Remote URL already correct."
fi

# ---- pull latest to avoid conflicts ----
log "Fetching origin..."
git fetch origin main || warn "Fetch failed — check your network or auth."
log "Pulling latest main (with rebase)..."
git pull --rebase origin main || warn "Pull failed. If there are conflicts, resolve them, then re-run."

# ---- back up upstream OpenClaw README if not already done ----
if [[ -f "README.md" && ! -f "README-OPENCLAW.md" ]]; then
  # Only back up if the current README looks like OpenClaw's (heuristic: contains "OpenClaw")
  if grep -q "OpenClaw" README.md 2>/dev/null; then
    log "Backing up OpenClaw README → README-OPENCLAW.md"
    git mv README.md README-OPENCLAW.md
  else
    warn "Existing README.md doesn't look like OpenClaw's — skipping backup."
  fi
elif [[ -f "README-OPENCLAW.md" ]]; then
  log "README-OPENCLAW.md backup already exists — skipping."
fi

# ---- copy the new Rahat README in ----
log "Installing new Rahat README."
cp "$SOURCE_README" "$LOCAL_CLONE/README.md"

# ---- stage + commit if there are changes ----
git add README.md
[[ -f "README-OPENCLAW.md" ]] && git add README-OPENCLAW.md

if git diff --cached --quiet; then
  warn "No changes to commit. README is already up to date."
else
  git commit -m "$COMMIT_MSG"
  log "Committed: $COMMIT_MSG"
fi

# ---- push ----
log "Pushing to origin/main..."
git push origin main
log "Done. Visit: https://github.com/$USERNAME/$REPO_NAME"
