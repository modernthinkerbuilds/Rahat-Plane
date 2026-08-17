#!/usr/bin/env bash
# Install com.rahat.benji.ingest + com.rahat.benji.digest — Benji's
# job-search pipeline (PRD v1.2 S1). OWNER-RUN ONLY (house rule 2:
# architects never launchctl).
#
#   ingest — Tier-1 ATS feeds + NPAG, every 4h: 06/10/14/18/22
#   digest — 07:30 morning queue · 18:05 evening delta (silent when
#            nothing ≥60 landed since morning)
#
# Uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.rahat.benji.ingest.plist
#   launchctl unload ~/Library/LaunchAgents/com.rahat.benji.digest.plist
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"

echo "== benji installer =="
[[ -x "$PYTHON_BIN" ]] || { echo "python missing: $PYTHON_BIN"; exit 1; }

# Preflight: the engine runs without these, but delivery doesn't.
if ! grep -q "^BENJI_DELIVERY_EMAIL=." "$REPO/.env" 2>/dev/null; then
    echo "⚠ BENJI_DELIVERY_EMAIL empty in .env — digests will be VETOED"
    echo "  by the recipient-allowlist policy (fail closed) until set."
fi

install_plist () {
    local LABEL="$1" ARGS="$2" CAL="$3"
    local PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
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
        <string>new_plane.benji_runner.main</string>
        <string>$ARGS</string>
    </array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>StartCalendarInterval</key>
    <array>
$CAL
    </array>
    <key>StandardOutPath</key><string>$REPO/vault/benji_runner.log</string>
    <key>StandardErrorPath</key><string>$REPO/vault/benji_runner.log</string>
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
    echo "✓ loaded $LABEL"
}

INGEST_CAL=""
for H in 6 10 14 18 22; do
    INGEST_CAL+="        <dict><key>Hour</key><integer>$H</integer><key>Minute</key><integer>0</integer></dict>
"
done
DIGEST_CAL="        <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>5</integer></dict>
"

install_plist "com.rahat.benji.ingest" "--ingest" "$INGEST_CAL"
install_plist "com.rahat.benji.digest" "--digest" "$DIGEST_CAL"

echo "Running the first ingest now (Tier-1 feeds; prints the yield table)…"
cd "$REPO" && "$PYTHON_BIN" -m new_plane.benji_runner.main --ingest || {
    echo "✗ first ingest failed — check vault/benji_runner.log"; exit 1; }
echo "Preview the first morning digest without sending:"
echo "  $PYTHON_BIN -m new_plane.benji_runner.main --digest morning --preview"
