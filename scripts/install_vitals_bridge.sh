#!/usr/bin/env bash
# Install the HealthKit bridge (com.rahat.vitals.v2) and retire the old
# staging Flask listener (com.rahat.vitals). Same port 5000 — the
# existing iPhone Shortcut keeps working unchanged.
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
LABEL="com.rahat.vitals.v2"
OLD_LABEL="com.rahat.vitals"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "== HealthKit bridge installer =="
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

if ! grep -q '^HAE_API_KEY=..*' "$REPO/.env" 2>/dev/null; then
    KEY=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-20)
    printf '\n# HealthKit bridge (Health Auto Export) API key\nHAE_API_KEY=%s\n' "$KEY" >> "$REPO/.env"
    echo "✓ generated HAE_API_KEY (in .env — you'll paste it into the app)"
fi

# Retire the old Flask listener if loaded (it owns port 5000).
launchctl unload "$HOME/Library/LaunchAgents/$OLD_LABEL.plist" 2>/dev/null \
    && echo "✓ unloaded old $OLD_LABEL" || echo "· old $OLD_LABEL not loaded"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>bridges.healthkit.server:app</string>
        <string>--host</string><string>0.0.0.0</string>
        <string>--port</string><string>5000</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$REPO/vault/vitals.log</string>
    <key>StandardErrorPath</key><string>$REPO/vault/vitals.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
echo "✓ rendered $PLIST"

# dotenv isn't loaded by uvicorn; inject HAE_API_KEY into the plist env.
KEY=$(grep '^HAE_API_KEY=' "$REPO/.env" | head -1 | cut -d= -f2-)
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:HAE_API_KEY string $KEY" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:HAE_API_KEY $KEY" "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 3
if curl -s http://127.0.0.1:5000/health | grep -q '"ok"'; then
    echo "✓ bridge is up on :5000 — API key for the app:"
    echo "    $KEY"
    IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<mac-ip>")
    echo "  Endpoint URL for Health Auto Export:  http://$IP:5000/hae"
else
    echo "✗ bridge not answering — check $REPO/vault/vitals.log"
    exit 1
fi
