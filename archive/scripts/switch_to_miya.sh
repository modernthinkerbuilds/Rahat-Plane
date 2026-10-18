#!/usr/bin/env bash
# switch_to_miya.sh — drop the legacy Scientist launchd job, bring up Miya.
#
# Why this exists: the Sports Scientist was the v0 single-agent process
# (com.rahat.scientist, running /usr/bin/python3
# agents/the_scientist/main.py). Miya is the v1 orchestrator that owns
# the Telegram chat and routes to one or more agents in the mesh
# (com.rahat.miya, running venv/bin/python core/miya_main.py). They both
# poll the same Telegram bot via long-poll — running both at once means
# duplicate replies, so the cutover MUST unload the old one first.
#
# Idempotent — re-running is safe.
#
# Rollback (in case Miya misbehaves):
#     launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist
#     launchctl load   ~/Library/LaunchAgents/com.rahat.scientist.plist  # if still installed
#     ( or: cd rahat && venv/bin/python agents/the_scientist/main.py & )

set -euo pipefail

cd "$(dirname "$0")/.."
RAHAT_HOME="$PWD"
RENDERED="${RAHAT_HOME}/core/com.rahat.miya.plist"
INSTALLED="${HOME}/Library/LaunchAgents/com.rahat.miya.plist"
LEGACY="${HOME}/Library/LaunchAgents/com.rahat.scientist.plist"

if [ ! -f "$RENDERED" ]; then
  echo "FAIL: ${RENDERED} not found. Run bootstrap.sh first to render templates."
  exit 1
fi

# Belt-and-suspenders: venv must exist and have google-genai, otherwise
# Miya will crash-loop at module load — don't switch over to a broken setup.
if [ ! -x "${RAHAT_HOME}/venv/bin/python" ]; then
  echo "FAIL: ${RAHAT_HOME}/venv/bin/python missing. Run bootstrap.sh first."
  exit 1
fi
"${RAHAT_HOME}/venv/bin/python" -c "import google.genai" 2>/dev/null \
  || { echo "FAIL: google-genai not installed in the venv. Run 'venv/bin/pip install -r requirements.txt'."; exit 1; }

echo "── 1. Unloading legacy com.rahat.scientist (if loaded) …"
if launchctl list | grep -q com.rahat.scientist; then
  launchctl unload "$LEGACY" 2>/dev/null || true
  echo "    com.rahat.scientist unloaded."
else
  echo "    com.rahat.scientist not currently loaded — nothing to do."
fi

# Also kill any free-floating `python … the_scientist/main.py` process
# that may have been started manually — same Telegram-token-conflict
# risk as the launchd one.
echo "── 2. Killing any free-floating Scientist processes …"
pkill -f "the_scientist/main.py" || echo "    (none running)"
sleep 1

echo "── 3. Installing ${INSTALLED} …"
mkdir -p "${HOME}/Library/LaunchAgents"
cp "$RENDERED" "$INSTALLED"

echo "── 4. (Re)loading com.rahat.miya …"
launchctl unload "$INSTALLED" 2>/dev/null || true
launchctl load "$INSTALLED"

sleep 3
echo "── 5. Status:"
if launchctl list | grep -q com.rahat.miya; then
  launchctl list | grep com.rahat.miya
  echo ""
  echo "    Miya is up. Watch the log for the first heartbeat:"
  echo "        tail -f ${RAHAT_HOME}/vault/miya.log"
  echo ""
  echo "    Smoke test from Telegram: send 'today' to the bot. Expect a"
  echo "    daily-burn reply within a few seconds."
else
  echo "    !! com.rahat.miya did NOT come up. Check the log:"
  echo "        tail -50 ${RAHAT_HOME}/vault/miya.log"
  echo "    Roll back with:"
  echo "        launchctl unload ${INSTALLED}"
  echo "        cd ${RAHAT_HOME} && venv/bin/python agents/the_scientist/main.py &"
  exit 1
fi

# Final safety: only delete the legacy plist file if Miya is actually
# alive — otherwise we want to leave the old one around for rollback.
if [ -f "$LEGACY" ]; then
  echo ""
  echo "── 6. (Optional) The legacy ${LEGACY} is still on disk."
  echo "    Leaving it in place for rollback. Delete manually when you"
  echo "    are satisfied Miya is stable:"
  echo "        rm ${LEGACY}"
fi
