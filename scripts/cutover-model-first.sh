#!/usr/bin/env bash
# cutover-model-first.sh — one-shot Phase 4 cutover for the model-first pivot.
#
# What it does (idempotent — safe to re-run):
#   1. Verifies GEMINI_API_KEY is set in env or .env
#   2. pip-installs missing deps from requirements.txt (google-genai etc.)
#   3. Runs the four eval suites; aborts on any failure
#   4. Stops the Scientist (and Miya if managed)
#   5. Starts them again so the new code path goes live
#   6. Prints the cost CLI's first read so you can sanity-check telemetry
#
# Run from repo root:
#     cd ~/developer/agency/rahat
#     bash scripts/cutover-model-first.sh
#
# Provider: Gemini 2.5 Flash (default reasoner) + 2.5 Pro (high-stakes
# opt-in). Anthropic was removed from the runtime path on 2026-05-08;
# see specs/MODEL-FIRST-PIVOT.md §1 update note for the rationale.
#
# Rollback: set RAHAT_LEGACY_DISPATCH=1 in the environment that runs the
# Scientist (e.g. its launchd plist or .env) and restart. The reasoner
# code stays in place but is bypassed; revert at leisure.

set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

YELLOW='\033[1;33m'; GREEN='\033[1;32m'; RED='\033[1;31m'; CYAN='\033[1;36m'; NC='\033[0m'
log()  { echo -e "${CYAN}[cutover]${NC} $*"; }
ok()   { echo -e "${GREEN}[ ok ]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[ ❌ ]${NC} $*" >&2; }

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Rahat — Phase 4 cutover to Gemini-primary reasoner"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# ─── 1. Verify GEMINI_API_KEY ───
log "Step 1/6 — verify GEMINI_API_KEY"
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
    if [[ -f .env ]] && grep -q '^GEMINI_API_KEY=' .env; then
        GEMINI_API_KEY=$(grep '^GEMINI_API_KEY=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
        export GEMINI_API_KEY
        ok ".env has GEMINI_API_KEY (last 4: …${GEMINI_API_KEY: -4})"
    else
        err "GEMINI_API_KEY not set. Add it to ~/developer/agency/rahat/.env:"
        err "    echo 'GEMINI_API_KEY=...' >> .env"
        err "Then re-run this script."
        exit 1
    fi
else
    ok "GEMINI_API_KEY in env (last 4: …${GEMINI_API_KEY: -4})"
fi

# Belt-and-suspenders — warn if the user still has ANTHROPIC_API_KEY
# in .env from the previous design. It's harmless but no longer used.
if grep -q '^ANTHROPIC_API_KEY=' .env 2>/dev/null; then
    warn "ANTHROPIC_API_KEY is still in .env but is no longer used by the runtime."
    warn "  (See specs/MODEL-FIRST-PIVOT.md §1 update note.) Safe to remove."
fi

# ─── 2. Install dependencies ───
log "Step 2/6 — install Python deps"
if ! python3 -c "from google import genai" 2>/dev/null; then
    log "  installing google-genai + missing deps from requirements.txt"
    pip3 install --user --quiet -r requirements.txt 2>&1 | tail -5 || {
        warn "pip install via --user failed. Trying --break-system-packages…"
        pip3 install --break-system-packages --quiet -r requirements.txt 2>&1 | tail -5
    }
fi
python3 -c "from google import genai; print('  google-genai imported OK')"
ok "deps satisfied"

# ─── 3. Run all four eval suites ───
log "Step 3/6 — run eval suites (legacy + reasoner)"
fail=0
for suite in eval_suite eval_via_agent eval_extended eval_reasoner eval_reasoner_robust; do
    f="agents/the_scientist/${suite}.py"
    if [[ ! -f "$f" ]]; then
        warn "  skipping $suite — file not found"
        continue
    fi
    # Use the suite's exit code as the source of truth (each main()
    # returns 0 on full pass, 1 on any failure). The previous grep-
    # based check matched '0%' inside '100%' and produced false
    # negatives.
    if python3 "$f" > /tmp/cutover_eval_out 2>&1; then
        summary=$(grep -E 'passed' /tmp/cutover_eval_out | head -1 | sed 's/^[ \t]*//')
        ok "  $suite: $summary"
    else
        err "  $suite FAILED:"
        tail -20 /tmp/cutover_eval_out
        fail=1
    fi
done
if (( fail )); then
    err "Eval suite(s) failing — aborting cutover. Fix tests, then re-run."
    exit 1
fi

# ─── 4. Stop the Scientist ───
log "Step 4/6 — stop Scientist (and Miya if managed)"
if [[ -x scripts/scientist.sh ]]; then
    bash scripts/scientist.sh stop || true
fi
MIYA_LABEL="com.rahat.miya"
if launchctl list 2>/dev/null | grep -q "$MIYA_LABEL"; then
    launchctl bootout "gui/$UID/$MIYA_LABEL" 2>/dev/null || true
    log "  Miya stopped"
fi

# ─── 5. Restart ───
log "Step 5/6 — restart with the new code"
if [[ -x scripts/scientist.sh ]]; then
    bash scripts/scientist.sh start
fi
if [[ -f "$HOME/Library/LaunchAgents/$MIYA_LABEL.plist" ]]; then
    launchctl load -w "$HOME/Library/LaunchAgents/$MIYA_LABEL.plist"
    log "  Miya restarted"
    sleep 2
fi
sleep 2
ok "agents back up"

# ─── 6. Cost CLI sanity check ───
log "Step 6/6 — first cost-CLI read (will be empty until you send a msg)"
if [[ -f scripts/llm_cost_report.py ]]; then
    python3 scripts/llm_cost_report.py --since 1h || true
fi

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ${GREEN}DONE${NC} — cutover complete."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "  Next:"
echo "    1. Send a Telegram test: 'Replan to get 1016 calories per day'"
echo "    2. The reply should explicitly hit your constraint or surface the gap."
echo "    3. After ~10 messages, run:"
echo "         python3 scripts/llm_cost_report.py --since 24h"
echo "       to confirm cost is accruing in the ledger."
echo "    4. Tail the log for any reasoner errors:"
echo "         bash scripts/scientist.sh tail"
echo
echo "  Provider posture (2026-05-08):"
echo "    - Gemini 2.5 Flash — default reasoner"
echo "    - Gemini 2.5 Pro   — high-stakes opt-in (tier/swap/tolerate/log_weight)"
echo "    - Anthropic        — REMOVED from runtime; tombstone at core/anthropic_io.py"
echo "    - Fallback ladder  — Gemini → legacy regex (no third tier)"
echo
echo "  Rollback (instant):"
echo "    Add RAHAT_LEGACY_DISPATCH=1 to your scientist plist's <EnvironmentVariables>"
echo "    (or .env) and run 'bash scripts/scientist.sh restart'. The reasoner stays"
echo "    in place but is bypassed; the regex dispatcher takes over."
echo
echo "  Soak window: 7 days. After a clean week, delete the legacy path."
echo
