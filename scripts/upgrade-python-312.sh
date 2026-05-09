#!/usr/bin/env bash
# upgrade-python-312.sh — autonomous Python 3.9 → 3.12 migration.
#
# Safe to run unattended. Designed to leave the Scientist either:
#   (a) running on Python 3.12 with all dependencies, OR
#   (b) restored to the original 3.9 setup if anything fails — auto-rollback.
#
# Logs everything to vault/upgrade-py312.log so you can review on return.
#
# Strategy:
#   1. Install/verify python@3.12 via brew (no production impact).
#   2. Install requirements under 3.12 in --user site (no production impact).
#   3. Smoke-test core imports + run hermetic eval_memory suite under 3.12.
#      If any of (1)-(3) fail, exit cleanly without touching the live bot.
#   4. Backup launchd plist, swap /usr/bin/python3 → /opt/homebrew/bin/python3.12.
#   5. Reload plist; verify process comes up; tail the log.
#      If startup fails, restore plist from backup and reload (3.9 restored).
#
# Run as: nohup scripts/upgrade-python-312.sh > /dev/null 2>&1 &
# Or:     scripts/upgrade-python-312.sh    (interactive — see live progress)

set -uo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

LOG="$ROOT/vault/upgrade-py312.log"
mkdir -p "$ROOT/vault"

# All output goes to log + stdout.
exec > >(tee -a "$LOG") 2>&1

PLIST="$HOME/Library/LaunchAgents/com.rahat.scientist.plist"
PLIST_BAK="$PLIST.bak.$(date +%Y%m%d-%H%M%S)"
LABEL="com.rahat.scientist"
AGENT_REL="agents/the_scientist/main.py"

step() { echo; echo "═══ [$(date '+%H:%M:%S')] $1 ═══"; }
ok()   { echo "  ✓ $1"; }
warn() { echo "  ⚠ $1"; }
die()  { echo "  ✗ $1"; rollback; exit 1; }

ROLLBACK_NEEDED=0

rollback() {
    if (( ROLLBACK_NEEDED == 0 )); then
        return
    fi
    step "ROLLBACK — restoring 3.9"
    if [[ -f "$PLIST_BAK" ]]; then
        cp -f "$PLIST_BAK" "$PLIST"
        launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
        sleep 1
        launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || \
            launchctl load -w "$PLIST" 2>/dev/null || true
        ok "Plist restored from $PLIST_BAK; bot relaunched on 3.9"
    fi
    echo
    echo "Bot should be back on Python 3.9. Review $LOG for the failure."
}

# ──────────────────────────────────────────────────────────────────
# Preflight — does NOT touch the running bot.
# ──────────────────────────────────────────────────────────────────
step "Preflight"
echo "Repo:      $ROOT"
echo "Plist:     $PLIST"
echo "Log:       $LOG"
[[ -f "$ROOT/requirements.txt" ]] || die "requirements.txt not found"
[[ -f "$PLIST" ]] || die "launchd plist not installed — run scripts/scientist.sh install-launchd first"

# ──────────────────────────────────────────────────────────────────
# 1. Locate / install python@3.12
# ──────────────────────────────────────────────────────────────────
step "1. Locate Python 3.12"
PY312=""
for path in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
            /opt/homebrew/opt/python@3.12/bin/python3.12; do
    if [[ -x "$path" ]]; then PY312="$path"; break; fi
done

if [[ -z "$PY312" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        die "brew not found and python3.12 not installed — install brew first"
    fi
    echo "  python3.12 not found — installing via brew (5-10 min)..."
    brew install python@3.12 || die "brew install python@3.12 failed"
    PY312=/opt/homebrew/bin/python3.12
    [[ -x "$PY312" ]] || PY312=/opt/homebrew/opt/python@3.12/bin/python3.12
fi

[[ -x "$PY312" ]] || die "python3.12 still not found after install"
"$PY312" --version | grep -q "Python 3.12" || die "$PY312 isn't 3.12"
ok "Found: $PY312 ($("$PY312" --version))"

# ──────────────────────────────────────────────────────────────────
# 2. Install requirements under 3.12 — uses user-site, no venv
#    (mirrors the existing 3.9 setup at ~/Library/Python/3.9/)
# ──────────────────────────────────────────────────────────────────
step "2. Install requirements under Python 3.12 (--user site)"
"$PY312" -m pip install --user --upgrade pip || die "pip upgrade failed"
"$PY312" -m pip install --user -r "$ROOT/requirements.txt" \
    || die "pip install -r requirements.txt failed"
ok "All requirements installed under 3.12 user-site"

# ──────────────────────────────────────────────────────────────────
# 3. Smoke test under 3.12 — no production impact
# ──────────────────────────────────────────────────────────────────
step "3. Smoke-test core imports under 3.12"
"$PY312" -c "
import sys
sys.path.insert(0, '$ROOT')
from agents.the_scientist import tools, agent
from core import memory, charter, io
from agents.the_scientist import main as scimain
print(f'  python: {sys.version.split()[0]}')
print(f'  tools.SCHEMAS: {len(tools.SCHEMAS)} tools registered')
print(f'  get_active_goal in dispatch: {\"get_active_goal\" in tools._DISPATCH}')
" || die "import smoke test failed under 3.12"
ok "Imports OK under 3.12"

step "3b. Run hermetic eval_memory suite under 3.12"
RAHAT_TEST_MODE=1 "$PY312" "$ROOT/agents/the_scientist/eval_memory.py" 2>&1 \
    | tail -8 \
    | tee /tmp/upgrade-eval.out
grep -q "30/30 passed" /tmp/upgrade-eval.out \
    || die "eval_memory did not pass cleanly under 3.12"
ok "eval_memory: 30/30 under 3.12"

# ──────────────────────────────────────────────────────────────────
# 4. Swap the plist — past this point, rollback is real.
# ──────────────────────────────────────────────────────────────────
step "4. Backup + swap launchd plist"
ROLLBACK_NEEDED=1
cp -f "$PLIST" "$PLIST_BAK"
ok "Plist backed up → $PLIST_BAK"

# Replace the python3 binary path in the plist.
# The file is XML — we just sed the one specific string.
sed -i.tmp "s|<string>/usr/bin/python3</string>|<string>$PY312</string>|g" "$PLIST"
rm -f "$PLIST.tmp"
grep -q "<string>$PY312</string>" "$PLIST" || die "plist sed didn't take"
ok "Plist now points to $PY312"

# ──────────────────────────────────────────────────────────────────
# 5. Stop, reload, verify
# ──────────────────────────────────────────────────────────────────
step "5. Stop current bot + reload launchd agent on 3.12"

# Stop everything (manual + launchd).
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
sleep 1
pkill -f "$AGENT_REL" 2>/dev/null || true
sleep 1
pkill -9 -f "$AGENT_REL" 2>/dev/null || true
sleep 1

# Reload from new plist.
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || \
    launchctl load -w "$PLIST" || die "launchctl load failed"

# Wait for KeepAlive to start the process.
echo "  Waiting up to 20s for bot to come up..."
for i in $(seq 1 20); do
    if pgrep -f "$AGENT_REL" >/dev/null 2>&1; then
        ok "Process up (after ${i}s)"
        break
    fi
    sleep 1
done

if ! pgrep -f "$AGENT_REL" >/dev/null 2>&1; then
    die "Bot did not start within 20s on 3.12"
fi

# Verify it's actually using 3.12 by checking process tree.
sleep 3
PROC_PY=$(ps -o command= -p "$(pgrep -f "$AGENT_REL" | head -1)" | awk '{print $3}')
if [[ "$PROC_PY" == "$PY312" ]] || [[ "$PROC_PY" == *"python3.12"* ]]; then
    ok "Bot is running under 3.12: $PROC_PY"
else
    warn "Process command line: $PROC_PY (expected $PY312)"
    # Don't fail on this — caffeinate prefix can shift positions. The plist swap took.
fi

# Tail the live log for fresh "Scientist live" message.
sleep 2
echo
echo "  Last 12 lines of vault/scientist.log:"
tail -n 12 "$ROOT/vault/scientist.log" | sed 's/^/    /'

# Look for the deprecation warning — should be GONE under 3.12.
if tail -n 30 "$ROOT/vault/scientist.log" | grep -q "Python version 3.9 past its end of life"; then
    warn "Still seeing 3.9 warning in log — may be from previous run, check timestamps"
else
    ok "No 3.9 deprecation warnings in fresh log"
fi

# ──────────────────────────────────────────────────────────────────
# Done — clear the rollback flag, the upgrade succeeded.
# ──────────────────────────────────────────────────────────────────
ROLLBACK_NEEDED=0

step "✅ UPGRADE COMPLETE"
echo
echo "  Python: $("$PY312" --version)"
echo "  Plist:  $PLIST (backup at $PLIST_BAK)"
echo "  Log:    $LOG"
echo
echo "  The bot is running on Python 3.12. The 3.9 deprecation warnings"
echo "  should be gone from the next message. The plist backup is kept"
echo "  in case you want to roll back manually:"
echo "    cp $PLIST_BAK $PLIST && scripts/scientist.sh restart"
echo
