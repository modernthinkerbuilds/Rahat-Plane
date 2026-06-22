#!/usr/bin/env bash
# install_new_miya.sh — render + install the new Miya v2 launchd job.
#
# Run ONCE on your Mac to set up the always-on service. After this,
# new Miya v2 boots automatically on login and restarts on crash.
#
# Pre-conditions checked before install:
#   - .venv/bin/python exists (run weekend_setup.sh first)
#   - NEW_MIYA_BOT_TOKEN is in .env (NOT equal to SCIENTIST_BOT_TOKEN)
#   - OPENCLAW_ADAPTER_URL points at a reachable adapter
#   - vault/ directory exists for log output
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist
#   rm ~/Library/LaunchAgents/com.rahat.miya.v2.plist
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
TEMPLATE="$REPO/scripts/com.rahat.miya.v2.plist.template"
RENDERED="$REPO/scripts/com.rahat.miya.v2.plist"
LAUNCHAGENT="$HOME/Library/LaunchAgents/com.rahat.miya.v2.plist"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"

# ─── pre-flight ───────────────────────────────────────────────────────
echo "=== pre-flight ==="
if [ ! -d "$REPO" ]; then
  echo "✗ repo not found at $REPO"
  exit 2
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "✗ python venv not found at $PYTHON_BIN"
  echo "  Run weekend_setup.sh first to create .venv"
  exit 2
fi
echo "  ✓ python: $PYTHON_BIN"

if [ ! -f "$REPO/.env" ]; then
  echo "✗ .env not found at $REPO/.env"
  exit 2
fi
NEW_TOKEN=$(grep -E '^NEW_MIYA_BOT_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)
SCI_TOKEN=$(grep -E '^SCIENTIST_BOT_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)
if [ -z "$NEW_TOKEN" ]; then
  echo "✗ NEW_MIYA_BOT_TOKEN missing from .env"
  echo "  Get one from @BotFather, then:"
  echo "  echo 'NEW_MIYA_BOT_TOKEN=<paste>' >> $REPO/.env"
  exit 2
fi
if [ -n "$SCI_TOKEN" ] && [ "$NEW_TOKEN" = "$SCI_TOKEN" ]; then
  echo "✗ NEW_MIYA_BOT_TOKEN equals SCIENTIST_BOT_TOKEN"
  echo "  These MUST be different bots. The runner also enforces this at boot."
  exit 2
fi
echo "  ✓ NEW_MIYA_BOT_TOKEN set (distinct from SCIENTIST_BOT_TOKEN)"

ADAPTER_URL=$(grep -E '^OPENCLAW_ADAPTER_URL=' "$REPO/.env" | head -1 | cut -d= -f2-)
ADAPTER_URL="${ADAPTER_URL:-http://127.0.0.1:8766}"
if ! curl -sf "$ADAPTER_URL/healthz" >/dev/null 2>&1; then
  echo "  ⚠ adapter not reachable at $ADAPTER_URL (runner will refuse to boot)"
  echo "    boot it with: ./scripts/weekend_smoke.sh (one-off) or its own launchd job"
else
  echo "  ✓ adapter healthy at $ADAPTER_URL"
fi

mkdir -p "$REPO/vault"
echo "  ✓ vault/ ready for logs"

# ─── render ───────────────────────────────────────────────────────────
echo
echo "=== render plist ==="
sed -e "s|{{RAHAT_HOME}}|$REPO|g" \
    -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
    "$TEMPLATE" > "$RENDERED"
echo "  rendered → $RENDERED"

# ─── install ──────────────────────────────────────────────────────────
echo
echo "=== install ==="
mkdir -p "$HOME/Library/LaunchAgents"

# If already installed, unload first so we can swap cleanly
if launchctl list | grep -q "^[0-9-]* *[0-9-]* *com.rahat.miya.v2$"; then
  echo "  unloading existing com.rahat.miya.v2..."
  launchctl unload "$LAUNCHAGENT" 2>/dev/null || true
fi

cp "$RENDERED" "$LAUNCHAGENT"
launchctl load "$LAUNCHAGENT"

echo "  loaded → $LAUNCHAGENT"
echo
echo "=== verify ==="
sleep 2  # give it a beat to register
launchctl list | grep com.rahat.miya
echo
echo "Log file: $REPO/vault/miya_v2.log"
echo "Watch logs: tail -f $REPO/vault/miya_v2.log"
echo "Restart:    launchctl kickstart -k gui/\$(id -u)/com.rahat.miya.v2"
echo "Uninstall:  launchctl unload $LAUNCHAGENT && rm $LAUNCHAGENT"
