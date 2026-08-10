#!/usr/bin/env bash
# Install com.rahat.events — the event-inventory refresh job (PRD §6.3
# "ingest 2-3×/day"). Runs bridges.events at 7:00, 12:30 and 18:00.
# Owner-run. Uninstall: launchctl unload ~/Library/LaunchAgents/com.rahat.events.plist
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
LABEL="com.rahat.events"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "== events-ingest installer =="
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

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
        <string>bridges.events</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key><string>$REPO/vault/events_ingest.log</string>
    <key>StandardErrorPath</key><string>$REPO/vault/events_ingest.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "✓ loaded $LABEL (07:00 / 12:30 / 18:00 daily)"

echo "Running the first refresh now (takes ~1 min, one search per source)…"
cd "$REPO" && "$PYTHON_BIN" -m bridges.events || {
    echo "✗ first refresh failed — check vault/events_ingest.log"; exit 1; }
echo
"$PYTHON_BIN" -m bridges.events --stats
echo "✓ done — Genie now reads this inventory first. Yield view: "
echo "    $PYTHON_BIN -m bridges.events --stats"
