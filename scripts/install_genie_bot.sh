#!/usr/bin/env bash
# Install + start the standalone Genie Telegram bot (com.rahat.genie).
#
# Prereqs (one-time, ~2 min):
#   1. In Telegram, talk to @BotFather → /newbot → name it (e.g.
#      "Rahat Genie") → copy the token.
#   2. Put these in .env (gitignored):
#        GENIE_TELEGRAM_TOKEN=<the token>
#        GENIE_PRIMARY_CHAT=<your chat id>       # you, auto-enrolled
#        GENIE_PAIR_CODE=<any shared secret>     # wife sends /join <code>
#        RAHAT_GENIE_LOCATION="City, ST"         # already set if live plans work
#   3. Run this script. It renders the plist and LOADS the service.
#
# Wife onboarding: she opens the bot (t.me/<botname>), sends
#   /join <GENIE_PAIR_CODE>
# and she's in as "spouse" (charter-gated, logged in governance_log).
# Optional shared group chat: add the bot to the group, then someone
# sends /join <code> group.
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
LAUNCHAGENT="$HOME/Library/LaunchAgents/com.rahat.genie.plist"
TEMPLATE="$REPO/scripts/com.rahat.genie.plist.template"

echo "== Genie bot installer =="

[[ -f "$TEMPLATE" ]] || { echo "template missing: $TEMPLATE"; exit 1; }
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

# Preflight the env without leaking secrets.
if ! grep -q '^GENIE_TELEGRAM_TOKEN=..*' "$REPO/.env" 2>/dev/null; then
    echo "✗ GENIE_TELEGRAM_TOKEN not set in $REPO/.env"
    echo "  Create the bot with @BotFather first (see header of this script)."
    exit 1
fi
grep -q '^GENIE_PRIMARY_CHAT=..*' "$REPO/.env" 2>/dev/null \
    || echo "⚠ GENIE_PRIMARY_CHAT not set — you'll need /join <code> yourself."
grep -q '^GENIE_PAIR_CODE=..*' "$REPO/.env" 2>/dev/null \
    || echo "⚠ GENIE_PAIR_CODE not set — nobody else can join."

# Refuse token collision with the other bots (also enforced at boot).
TOK=$(grep '^GENIE_TELEGRAM_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)
for var in NEW_MIYA_BOT_TOKEN SCIENTIST_BOT_TOKEN; do
    OTHER=$(grep "^$var=" "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
    if [[ -n "$OTHER" && "$OTHER" == "$TOK" ]]; then
        echo "✗ GENIE_TELEGRAM_TOKEN equals $var — Genie needs its OWN bot."
        exit 1
    fi
done

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|{{RAHAT_HOME}}|$REPO|g" \
    -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
    "$TEMPLATE" > "$LAUNCHAGENT"
echo "✓ rendered $LAUNCHAGENT"

launchctl unload "$LAUNCHAGENT" 2>/dev/null || true
launchctl load "$LAUNCHAGENT"
echo "✓ loaded com.rahat.genie"

sleep 3
if launchctl list | grep -q com.rahat.genie; then
    echo "✓ service running — tail the log with:"
    echo "    tail -f $REPO/vault/genie_bot.log"
    echo "  Then message your bot on Telegram: /start"
else
    echo "✗ service not visible — check $REPO/vault/genie_bot.log"
    exit 1
fi
