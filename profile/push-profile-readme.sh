#!/usr/bin/env bash
# push-profile-readme.sh
#
# Pushes README-PROFILE.md to the special "magic" profile repo:
# https://github.com/modernthinkerbuilds/modernthinkerbuilds
#
# This is the repo whose README renders at the top of your GitHub profile page.
# If the repo doesn't exist yet, this script creates it (gh CLI required).

set -euo pipefail

# ---- config ----
USERNAME="modernthinkerbuilds"
PROFILE_REPO_DIR="$HOME/developer/$USERNAME"
SOURCE_README="$HOME/developer/agency/rahat/profile/README-PROFILE.md"
COMMIT_MSG="${1:-feat: initialize profile readme}"

# ---- helpers ----
log()  { printf "\033[1;36m[profile]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[profile]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[profile]\033[0m %s\n" "$*"; exit 1; }

# ---- preflight ----
[[ -f "$SOURCE_README" ]] || fail "Source README not found: $SOURCE_README"
command -v git >/dev/null || fail "git is not installed."

# ---- create local repo if missing ----
if [[ ! -d "$PROFILE_REPO_DIR/.git" ]]; then
  log "Local repo not found at $PROFILE_REPO_DIR — initializing."
  mkdir -p "$PROFILE_REPO_DIR"
  cd "$PROFILE_REPO_DIR"
  git init -b main >/dev/null
else
  log "Local repo exists at $PROFILE_REPO_DIR — using it."
  cd "$PROFILE_REPO_DIR"
fi

# ---- copy README in ----
log "Copying profile README into place."
cp "$SOURCE_README" "$PROFILE_REPO_DIR/README.md"

# ---- stage + commit if there are changes ----
git add README.md
if git diff --cached --quiet; then
  warn "No changes to commit. README is already up to date."
else
  git commit -m "$COMMIT_MSG"
  log "Committed: $COMMIT_MSG"
fi

# ---- ensure remote ----
if ! git remote get-url origin >/dev/null 2>&1; then
  log "No 'origin' remote set."
  if command -v gh >/dev/null; then
    log "Creating GitHub repo via gh CLI..."
    gh repo create "$USERNAME/$USERNAME" --public --source=. --remote=origin --push
    log "Created and pushed to https://github.com/$USERNAME/$USERNAME"
    exit 0
  else
    warn "gh CLI not installed."
    cat <<EOF

To finish setup manually:
  1. Go to https://github.com/new
  2. Owner: $USERNAME    Repo name: $USERNAME    Visibility: Public
  3. Do NOT initialize with README/license/.gitignore
  4. Click "Create repository"
  5. Then run from inside $PROFILE_REPO_DIR:
       git remote add origin https://github.com/$USERNAME/$USERNAME.git
       git push -u origin main

EOF
    exit 0
  fi
fi

# ---- push ----
log "Pushing to origin/main..."
git push -u origin main
log "Done. Visit: https://github.com/$USERNAME"
