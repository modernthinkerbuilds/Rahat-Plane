#!/usr/bin/env bash
# Scientist control script — start/stop/restart/status/logs/tail.
#
# Two modes auto-detected:
#   • Manual: nohup'd Python process tracked via vault/scientist.pid
#   • launchd: managed by ~/Library/LaunchAgents/com.rahat.scientist.plist
#     (after running `scripts/scientist.sh install-launchd` once)
#
# Daily use after install: `scripts/scientist.sh restart` — works in either mode.
set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

AGENT_REL="agents/the_scientist/main.py"
AGENT_ABS="$ROOT/$AGENT_REL"
LOG="$ROOT/vault/scientist.log"
PID_FILE="$ROOT/vault/scientist.pid"
PLIST_SRC="$ROOT/agents/the_scientist/com.rahat.scientist.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.rahat.scientist.plist"
LABEL="com.rahat.scientist"

mkdir -p "$(dirname "$LOG")"

is_launchd() {
    launchctl list 2>/dev/null | grep -q "$LABEL"
}

is_running_manual() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

cmd_start() {
    if is_launchd; then
        launchctl kickstart "gui/$UID/$LABEL"
        echo "Starting via launchd…"
        sleep 2 && tail -n 10 "$LOG"
        return
    fi
    if is_running_manual; then
        echo "Already running (pid $(cat "$PID_FILE"))"
        return 0
    fi
    nohup python3 -u "$AGENT_ABS" > "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if is_running_manual; then
        echo "Started (pid $(cat "$PID_FILE"))"
        tail -n 10 "$LOG"
    else
        echo "❌ Failed to start. Last log:"
        tail -n 30 "$LOG"
        return 1
    fi
}

cmd_stop() {
    # Belt-and-suspenders: kill EVERY instance regardless of how it was
    # started. Without this, a manual 'nohup' process started before
    # install-launchd survives the launchctl unload and you end up with
    # two agents both replying to Telegram.
    local killed_any=0
    if is_launchd; then
        launchctl bootout "gui/$UID/$LABEL" 2>/dev/null && killed_any=1 || true
    fi
    if pgrep -f "$AGENT_REL" >/dev/null 2>&1; then
        pkill -f "$AGENT_REL" 2>/dev/null || true
        sleep 1
        # Force-kill any survivors
        if pgrep -f "$AGENT_REL" >/dev/null 2>&1; then
            pkill -9 -f "$AGENT_REL" 2>/dev/null || true
            sleep 1
        fi
        killed_any=1
    fi
    rm -f "$PID_FILE"
    if (( killed_any )); then
        echo "Stopped (all instances killed)"
    else
        echo "Not running"
    fi
    # Final verification
    if pgrep -f "$AGENT_REL" >/dev/null 2>&1; then
        echo "⚠️  Some processes still running — try 'sudo pkill -9 -f $AGENT_REL'"
        pgrep -f "$AGENT_REL"
    fi
}

cmd_restart() {
    if is_launchd; then
        # launchctl kickstart -k cleanly stops then starts under launchd's
        # KeepAlive, so it picks up code changes without fighting the agent.
        launchctl kickstart -k "gui/$UID/$LABEL"
        echo "Restarted via launchd"
        sleep 2
        tail -n 10 "$LOG"
        return
    fi
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if is_launchd; then
        echo "Mode: launchd"
        launchctl list | grep "$LABEL" || echo "  (loaded but not running)"
        local pid
        pid=$(launchctl list | awk -v lbl="$LABEL" '$3==lbl {print $1}')
        if [[ -n "${pid:-}" && "$pid" != "-" ]]; then
            ps -o pid,etime,command -p "$pid" 2>/dev/null || true
        fi
        return 0
    fi
    if is_running_manual; then
        echo "Mode: manual"
        local pid; pid=$(cat "$PID_FILE")
        echo "Running (pid $pid)"
        ps -o pid,etime,command -p "$pid"
        return 0
    fi
    echo "Not running"
    return 1
}

cmd_logs() { tail -n 50 "$LOG"; }
cmd_tail() { tail -f "$LOG"; }

cmd_install_launchd() {
    if [[ ! -f "$PLIST_SRC" ]]; then
        echo "❌ Plist source not found: $PLIST_SRC"
        return 1
    fi
    # Stop any manual process first
    if is_running_manual; then
        cmd_stop
    fi
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl load -w "$PLIST_DST"
    echo "✓ launchd agent installed at $PLIST_DST"
    echo "  Auto-starts at login. Survives Mac restart. Restarts on crash."
    echo "  Daily use: scripts/scientist.sh restart"
    sleep 2 && tail -n 10 "$LOG"
}

cmd_uninstall_launchd() {
    if [[ -f "$PLIST_DST" ]]; then
        launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || \
            launchctl unload -w "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "✓ launchd agent removed"
    else
        echo "No launchd agent installed"
    fi
}

case "${1:-}" in
    start)              cmd_start ;;
    stop)               cmd_stop ;;
    restart|reload)     cmd_restart ;;
    status)             cmd_status ;;
    logs)               cmd_logs ;;
    tail|follow)        cmd_tail ;;
    install-launchd)    cmd_install_launchd ;;
    uninstall-launchd)  cmd_uninstall_launchd ;;
    *)
        cat <<EOF
Usage: scripts/scientist.sh <command>

Daily:
  start              Start the agent (manual or via launchd if installed)
  stop               Stop the agent
  restart            Stop + start (kicks launchd if managing)
  status             Show whether running, mode, pid, uptime
  logs               Show last 50 lines of vault/scientist.log
  tail               Follow the log live (Ctrl-C to exit)

One-time:
  install-launchd    Install launchd agent → auto-start at login,
                     restart on crash, survive Mac reboot
  uninstall-launchd  Remove launchd agent

Tip: alias sci='$ROOT/scripts/scientist.sh'   then 'sci restart'
EOF
        exit 1
        ;;
esac
