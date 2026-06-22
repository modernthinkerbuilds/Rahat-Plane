#!/usr/bin/env bash
# install_vitals_plist.sh — install + load the vitals listener launchd job.
#
# Why this exists: before 2026-05-11 the vitals listener (Apple Watch →
# raw_vitals ingest at POST :5000/vitals) ran as a free-floating
# `python3 vitals_listener.py &` process. It died on every Mac reboot
# and the user only noticed when the watch data went stale. This script
# supervises it under launchd so the pipe stays up.
#
# Idempotent — safe to re-run. Will:
#   1. Kill any unsupervised vitals_listener.py process (the manual one).
#   2. Copy the rendered plist (must exist — bootstrap.sh renders it) to
#      ~/Library/LaunchAgents/.
#   3. Unload any existing com.rahat.vitals job, then load the new one.
#   4. Print PID + log location so you can verify it's alive.

set -euo pipefail

cd "$(dirname "$0")/.."
RAHAT_HOME="$PWD"
RENDERED="${RAHAT_HOME}/skills/com.rahat.vitals.plist"
INSTALLED="${HOME}/Library/LaunchAgents/com.rahat.vitals.plist"

if [ ! -f "$RENDERED" ]; then
  echo "FAIL: ${RENDERED} not found. Run bootstrap.sh first to render templates."
  exit 1
fi

# 1. Kill any unsupervised vitals_listener.py — launchd would otherwise
#    EADDRINUSE on port 5000 and crash-loop forever. We grep by full
#    command so we don't murder unrelated `python3` processes.
echo "── 1. Killing any unsupervised vitals_listener.py …"
pkill -f "vitals_listener.py" || echo "    (none running)"
sleep 1

# 2. Install the rendered plist.
echo "── 2. Installing ${INSTALLED} …"
mkdir -p "${HOME}/Library/LaunchAgents"
cp "$RENDERED" "$INSTALLED"

# 3. Reload — unload first in case a prior version is loaded.
echo "── 3. Reloading com.rahat.vitals …"
launchctl unload "$INSTALLED" 2>/dev/null || true
launchctl load "$INSTALLED"

# 4. Smoke test.
sleep 2
echo "── 4. Status:"
launchctl list | grep com.rahat.vitals || echo "    NOT RUNNING — check ${RAHAT_HOME}/vault/vitals.log"
echo ""
echo "    Tail logs:  tail -f ${RAHAT_HOME}/vault/vitals.log"
echo "    Endpoint:   curl -s -X POST http://localhost:5000/vitals -H 'Content-Type: application/json' -d '{\"timestamp\":\"\"}'"
echo "    Stop:       launchctl unload ${INSTALLED}"
