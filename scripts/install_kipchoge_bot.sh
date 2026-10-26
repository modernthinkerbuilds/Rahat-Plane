#!/usr/bin/env bash
# Install + start the standalone Kipchoge Telegram bot (com.rahat.kipchoge),
# the running coach (PRD v0.3 §15e).
#
# Prereqs (one-time):
#   1. @BotFather → /newbot → "Rahat's Kipchoge" → copy the token.
#   2. In .env (gitignored):
#        KIPCHOGE_BOT_TOKEN=<the token>
#        KIPCHOGE_PRIMARY_CHAT=<your chat id>      # the only chat it talks to; where the card and the analyses go
#        RAHAT_KIPCHOGE_HARNESS=serve              # serve: the coach's own answers when you ask; off: templates only
#        (KIPCHOGE_PROACTIVE=1 adds the evening card, the Sunday review and the landing analysis, templates only)
#        RAHAT_TOKEN_BUDGET_DAILY_USD_KIPCHOGE=0.50
#   3. The private files in vault/kipchoge/: athlete.json, plan.json, goals.json, routes.json, rules.json.
#   4. Run this script. It renders the plist and LOADS the service.
#
# The bridge (com.rahat.vitals.v2) keeps filling the store; this bot only reads it and writes its own tables.
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
LAUNCHAGENT="$HOME/Library/LaunchAgents/com.rahat.kipchoge.plist"
TEMPLATE="$REPO/scripts/com.rahat.kipchoge.plist.template"

echo "== Kipchoge bot installer =="
[[ -f "$TEMPLATE" ]] || { echo "template missing: $TEMPLATE"; exit 1; }
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

if ! grep -q '^KIPCHOGE_BOT_TOKEN=..*' "$REPO/.env" 2>/dev/null; then
    echo "✗ KIPCHOGE_BOT_TOKEN not set in $REPO/.env (see header)."; exit 1
fi
grep -q '^KIPCHOGE_PRIMARY_CHAT=..*' "$REPO/.env" 2>/dev/null \
    || echo "⚠ KIPCHOGE_PRIMARY_CHAT not set — the bot will answer nobody and the pipelines will drop their messages."
grep -q '^RAHAT_KIPCHOGE_HARNESS=' "$REPO/.env" 2>/dev/null \
    || echo "⚠ RAHAT_KIPCHOGE_HARNESS not set — off applies (templates only, no model calls)."
grep -q '^RAHAT_TOKEN_BUDGET_DAILY_USD_KIPCHOGE=' "$REPO/.env" 2>/dev/null \
    || echo "⚠ RAHAT_TOKEN_BUDGET_DAILY_USD_KIPCHOGE not set — the global cap applies."

TOK=$(grep '^KIPCHOGE_BOT_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)
for var in NEW_MIYA_BOT_TOKEN SCIENTIST_BOT_TOKEN GENIE_TELEGRAM_TOKEN BOURDAIN_TELEGRAM_TOKEN; do
    OTHER=$(grep "^$var=" "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
    if [[ -n "$OTHER" && "$OTHER" == "$TOK" ]]; then
        echo "✗ KIPCHOGE_BOT_TOKEN equals $var — Kipchoge needs its OWN bot."; exit 1
    fi
done

for f in athlete.json plan.json goals.json routes.json; do
    [[ -f "$REPO/vault/kipchoge/$f" ]] || echo "⚠ vault/kipchoge/$f missing — the coach answers from what is on file."
done

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|{{RAHAT_HOME}}|$REPO|g" -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
    "$TEMPLATE" > "$LAUNCHAGENT"
echo "✓ rendered $LAUNCHAGENT"
launchctl unload "$LAUNCHAGENT" 2>/dev/null || true
launchctl load "$LAUNCHAGENT"
echo "✓ loaded com.rahat.kipchoge"
sleep 3
if launchctl list | grep -q com.rahat.kipchoge; then
    echo "✓ service running — tail -f $REPO/vault/kipchoge_bot.log ; then /start the bot on Telegram"
else
    echo "✗ service not visible — check $REPO/vault/kipchoge_bot.log"; exit 1
fi
