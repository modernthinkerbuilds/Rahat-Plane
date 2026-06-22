#!/usr/bin/env bash
# install_hooks.sh — idempotent installer for the local git hooks.
#
# Usage:
#   bash scripts/install_hooks.sh         # install
#   bash scripts/install_hooks.sh --check # verify install only
#   bash scripts/install_hooks.sh --force # overwrite even if installed
#
# What it does:
#   1. Verifies you're in the rahat repo (git rev-parse).
#   2. Symlinks scripts/hooks/pre-push  →  .git/hooks/pre-push
#   3. Symlinks scripts/hooks/pre-merge →  .git/hooks/pre-merge
#      (informational only — git doesn't have a real pre-merge hook;
#      this is invoked via `make pre-merge` and the CI workflow)
#   4. Marks all hook scripts executable.
#
# Idempotent — running twice is a no-op.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MODE="install"
for arg in "$@"; do
    case "$arg" in
        --check) MODE="check" ;;
        --force) MODE="force" ;;
        *) echo "unknown arg: $arg"; exit 2 ;;
    esac
done

HOOK_SRC="scripts/hooks"
HOOK_DST=".git/hooks"

if [[ ! -d "$HOOK_SRC" ]]; then
    echo "✗ $HOOK_SRC not found — run from repo root with scripts/hooks/ present"
    exit 1
fi

mkdir -p "$HOOK_DST"

install_hook() {
    local name="$1"
    local src="$HOOK_SRC/$name"
    local dst="$HOOK_DST/$name"

    if [[ ! -f "$src" ]]; then
        echo "  ⊘ $name — source missing: $src"
        return
    fi

    chmod +x "$src"

    if [[ "$MODE" == "check" ]]; then
        if [[ -L "$dst" ]]; then
            local existing="$(readlink "$dst")"
            if [[ "$existing" == "../../$src" || "$existing" == "$src" ]]; then
                echo "  ✓ $name installed (symlink → $existing)"
                return 0
            fi
        elif [[ -f "$dst" ]]; then
            echo "  ⚠ $name present but NOT a symlink to $src — run --force to fix"
            return 1
        fi
        echo "  ✗ $name not installed — run without --check"
        return 1
    fi

    if [[ -e "$dst" ]] && [[ "$MODE" != "force" ]]; then
        if [[ -L "$dst" ]] && [[ "$(readlink "$dst")" == "../../$src" ]]; then
            echo "  ✓ $name already installed (idempotent)"
            return 0
        fi
        echo "  ⚠ $name exists at $dst but doesn't match — use --force to overwrite"
        return 1
    fi

    # Symlink so edits to scripts/hooks/* take effect immediately.
    rm -f "$dst"
    ln -s "../../$src" "$dst"
    echo "  ✓ $name installed (symlink: $dst → ../../$src)"
}

echo "════════════════════════════════════════════════════════════════════"
echo "  Installing rahat git hooks (mode: $MODE)"
echo "════════════════════════════════════════════════════════════════════"
install_hook "pre-push"
install_hook "pre-merge"
echo ""

if [[ "$MODE" == "check" ]]; then
    echo "Run without --check to install if anything is missing."
else
    echo "Done. From now on, 'git push' runs the pre-push gate locally."
    echo ""
    echo "To verify: bash scripts/install_hooks.sh --check"
    echo "To bypass (emergency only): git push --no-verify"
fi
