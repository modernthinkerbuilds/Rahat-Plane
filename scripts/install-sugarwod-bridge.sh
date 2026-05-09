#!/usr/bin/env bash
# install-sugarwod-bridge.sh — one-time setup for the SugarWOD → Scientist
# bridge. Idempotent — safe to re-run if anything goes sideways.
#
# Run from the repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/install-sugarwod-bridge.sh
#
# After this finishes, click your "Scientist Sync" bookmark on
# app.sugarwod.com/workouts/calendar to push this week's WODs.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "============================================================"
echo "  SugarWOD bridge install"
echo "============================================================"

# ─── 1. Python deps ───
echo ""
echo "Step 1/5 — install FastAPI deps (idempotent)"
pip3 install --user --quiet fastapi uvicorn pydantic 2>&1 | tail -3 || {
    echo "  pip3 install failed. If you're on Apple Silicon and pip3 is"
    echo "  pointing at a system Python, try:"
    echo "    brew install python@3.11"
    echo "    /opt/homebrew/bin/pip3 install --user fastapi uvicorn pydantic"
    exit 1
}
python3 -c "import fastapi, uvicorn, pydantic; print('  imports OK')"

# ─── 2. Ensure the staging directory exists ───
echo ""
echo "Step 2/5 — create staging/workspace/gym-programming/"
mkdir -p staging/workspace/gym-programming/archive
echo "  done"

# ─── 3. Install the launchd plist ───
echo ""
echo "Step 3/5 — install ~/Library/LaunchAgents/com.rahat.sugar.bridge.plist"
PLIST_SRC="bridges/sugarwod/com.rahat.sugar.bridge.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.rahat.sugar.bridge.plist"
if [ ! -f "$PLIST_SRC" ]; then
    echo "  ❌ source plist not found: $PLIST_SRC"
    echo "  Are you in the rahat repo root?"
    exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
echo "  copied"

# ─── 4. Load it (or reload if already loaded) ───
echo ""
echo "Step 4/5 — load via launchctl"
# bootout first in case a previous install is registered (idempotent)
launchctl bootout "gui/$(id -u)/com.rahat.sugar.bridge" 2>/dev/null || true
sleep 1
launchctl load "$PLIST_DST"
echo "  loaded"

# ─── 5. Health check ───
echo ""
echo "Step 5/5 — health check (waiting up to 5s for bridge to bind port)"
for i in 1 2 3 4 5; do
    sleep 1
    if curl -s -m 2 http://localhost:8765/health 2>/dev/null | grep -q '"ok":true'; then
        echo "  ✅ bridge alive at http://localhost:8765"
        curl -s http://localhost:8765/health
        echo ""
        echo ""
        echo "============================================================"
        echo "  DONE — next steps"
        echo "============================================================"
        echo ""
        echo "1. Open Chrome → https://app.sugarwod.com/workouts/calendar"
        echo "2. Click your 'Scientist Sync' bookmark in the bookmark bar"
        echo "   (if you don't have it yet, see bridges/sugarwod/bookmarklet.js)"
        echo "3. Expect popup: ✓ Sent week 20260504 — 7 days, N workouts."
        echo ""
        echo "Then in Telegram: send 'replan' to rebuild this week's plan"
        echo "with the fresh gym data."
        exit 0
    fi
    echo "  …waiting (attempt $i/5)"
done

echo ""
echo "❌ Bridge didn't come up on port 8765 after 5 seconds."
echo ""
echo "Diagnostic — run the server in foreground to see the error:"
echo "    python3 bridges/sugarwod/server.py"
echo ""
echo "Common causes:"
echo "  - Port 8765 already in use by another process:"
echo "      lsof -i :8765"
echo "  - Python 3 is not the same one launchd uses (path issue):"
echo "      head -20 ~/Library/LaunchAgents/com.rahat.sugar.bridge.plist"
echo "  - FastAPI dependencies not in the right Python:"
echo "      python3 -c 'import fastapi'"
exit 1
