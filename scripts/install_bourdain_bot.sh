#!/usr/bin/env bash
# Install + start the standalone Bourdain Telegram bot (com.rahat.bourdain).
#
# Prereqs (one-time):
#   1. @BotFather → /newbot → "Rahat's Bourdain" → copy the token.
#   2. In .env (gitignored):
#        BOURDAIN_TELEGRAM_TOKEN=<the token>
#        BOURDAIN_PRIMARY_CHAT=<your chat id>      # you, auto-enrolled
#        BOURDAIN_PAIR_CODE=<any shared secret>    # others send /join <code>
#        RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN=0.05
#      (Anyone already in Genie's household is accepted without a code.)
#   3. Load the seed:  .venv/bin/python scripts/bourdain_seed.py private/prds/bourdain/seed_nyc_boston.json
#   4. Run this script. It renders the plist and LOADS the service.
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
LAUNCHAGENT="$HOME/Library/LaunchAgents/com.rahat.bourdain.plist"
TEMPLATE="$REPO/scripts/com.rahat.bourdain.plist.template"

echo "== Bourdain bot installer =="
[[ -f "$TEMPLATE" ]] || { echo "template missing: $TEMPLATE"; exit 1; }
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

if ! grep -q '^BOURDAIN_TELEGRAM_TOKEN=..*' "$REPO/.env" 2>/dev/null; then
    echo "✗ BOURDAIN_TELEGRAM_TOKEN not set in $REPO/.env (see header)."; exit 1
fi
grep -q '^BOURDAIN_PRIMARY_CHAT=..*' "$REPO/.env" 2>/dev/null \
    || echo "⚠ BOURDAIN_PRIMARY_CHAT not set — you'll need /join <code> yourself."
grep -q '^BOURDAIN_PAIR_CODE=..*' "$REPO/.env" 2>/dev/null \
    || echo "⚠ BOURDAIN_PAIR_CODE not set — nobody outside Genie's household can join."
grep -q '^RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN=' "$REPO/.env" 2>/dev/null \
    || echo "⚠ RAHAT_TOKEN_BUDGET_DAILY_USD_BOURDAIN not set — the global cap applies (PRD asks for 0.05)."

TOK=$(grep '^BOURDAIN_TELEGRAM_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)
for var in NEW_MIYA_BOT_TOKEN SCIENTIST_BOT_TOKEN GENIE_TELEGRAM_TOKEN; do
    OTHER=$(grep "^$var=" "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
    if [[ -n "$OTHER" && "$OTHER" == "$TOK" ]]; then
        echo "✗ BOURDAIN_TELEGRAM_TOKEN equals $var — Bourdain needs its OWN bot."; exit 1
    fi
done

if [[ ! -f "$REPO/vault/bourdain_store.json" ]]; then
    echo "⚠ no trip loaded yet — run: $PYTHON_BIN scripts/bourdain_seed.py private/prds/bourdain/seed_nyc_boston.json"
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|{{RAHAT_HOME}}|$REPO|g" -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
    "$TEMPLATE" > "$LAUNCHAGENT"
echo "✓ rendered $LAUNCHAGENT"
launchctl unload "$LAUNCHAGENT" 2>/dev/null || true
launchctl load "$LAUNCHAGENT"
echo "✓ loaded com.rahat.bourdain"
sleep 3
if launchctl list | grep -q com.rahat.bourdain; then
    echo "✓ service running — tail -f $REPO/vault/bourdain_bot.log ; then /start the bot on Telegram"
else
    echo "✗ service not visible — check $REPO/vault/bourdain_bot.log"; exit 1
fi
