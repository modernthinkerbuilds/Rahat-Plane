#!/usr/bin/env bash
# scripts/jobs/regression.sh — daily regression + eval run.
#
# Triggered by ~/Library/LaunchAgents/com.rahat.regression.plist at 23:00.
# This is the canary: if anything regresses overnight, this fails LOUD
# (non-zero exit, log line that hygiene.sh greps for in its alert path).
#
# Hermetic guarantees match tests/nightly.sh:
#   - RAHAT_TEST_MODE=1 (no live DB writes)
#   - GEMINI_API_KEY unset (no Gemini calls)
#   - RAHAT_RUN_JUDGE unset (no LLM-as-judge)
#
# Output:
#   - tests/last_run_report.md     (human)
#   - tests/last_run_status.json   (machine — read by greenstreak.sh)
#   - tests/last_run.json          (per-layer detail)
#   - tests/last_run_stdout.log    (full pytest output for triage)
#   - vault/jobs/regression.log    (this script's own stdout)
#
# Exit codes:
#   0 — every layer passed
#   1 — at least one layer failed
#   2 — preconditions missing (no python, no repo)
#
# This script ONLY runs the suite. It does NOT commit or push.
# greenstreak.sh handles the auto-commit-on-green logic separately.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/vault/jobs"
mkdir -p "$LOG_DIR"
JOB_LOG="$LOG_DIR/regression.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$JOB_LOG" >&2; }

log "=== regression.sh start ==="

# Pick Python — prefer the venv that requirements-dev.txt was installed
# into; fall back to system python3.
PY="$REPO_ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
    log "FATAL: no python interpreter found"
    exit 2
fi
log "using python: $PY"

# Hermetic env. Match tests/nightly.sh exactly.
export RAHAT_TEST_MODE=1
export RAHAT_VOICE=neutral
export RAHAT_LEGACY_DISPATCH=1
unset GEMINI_API_KEY
unset RAHAT_RUN_JUDGE

REPORT="$REPO_ROOT/tests/last_run_report.md"
STATUS="$REPO_ROOT/tests/last_run_status.json"
JSON="$REPO_ROOT/tests/last_run.json"
STDOUT="$REPO_ROOT/tests/last_run_stdout.log"

# Run the five-layer suite.
set +e
"$PY" -m tests.run_all --json "$JSON" --report "$REPORT" > "$STDOUT" 2>&1
RC=$?
set -e
log "tests.run_all returned $RC"

# Materialize the machine-readable status JSON. This is what greenstreak
# reads to decide whether to auto-commit.
"$PY" - <<PYEOF
import json, pathlib
from datetime import datetime
report = pathlib.Path("$JSON")
status = pathlib.Path("$STATUS")
data = json.loads(report.read_text()) if report.exists() else []
status.write_text(json.dumps({
    "pass": all(d.get("passed") for d in data) if data else False,
    "rc": $RC,
    "ts": datetime.utcnow().isoformat() + "Z",
    "layers": [
        {"name": d["name"], "passed": d["passed"],
         "n_passed": d.get("n_passed", 0),
         "n_failed": d.get("n_failed", 0),
         "n_skipped": d.get("n_skipped", 0)}
        for d in data
    ],
    "failed_layers": [d["name"] for d in data if not d.get("passed")],
}, indent=2))
PYEOF

if [ "$RC" -eq 0 ]; then
    log "PASS — all five layers green"
else
    log "FAIL — see $STDOUT for triage"
fi
log "=== regression.sh done (rc=$RC) ==="
exit $RC
