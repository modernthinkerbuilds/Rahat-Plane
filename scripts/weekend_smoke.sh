#!/usr/bin/env bash
# weekend_smoke.sh — Friday-night Stage 0 verify.
#
# Starts the Python adapter in the background, curls every endpoint,
# checks the signal store round-trip, prints PASS/FAIL per item.
#
# Run from ~/developer/agency/rahat AFTER scripts/weekend_setup.sh.
set -u

REPO="${REPO:-$HOME/developer/agency/rahat}"
cd "$REPO"

PORT="${OPENCLAW_ADAPTER_PORT:-8765}"
BASE="http://127.0.0.1:${PORT}"
TOKEN="${OPENCLAW_ADAPTER_TOKEN:-}"

AUTH_ARG=()
if [ -n "$TOKEN" ]; then AUTH_ARG=(-H "Authorization: Bearer $TOKEN"); fi
# bash 3.2 on macOS treats "${arr[@]}" as unbound when empty under set -u.
# The "${arr[@]+"${arr[@]}"}" idiom expands to nothing when empty, elements otherwise.
# Define a shortcut so the curls stay readable.

PASS=0
FAIL=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ✓ $label"
    PASS=$((PASS+1))
  else
    echo "  ✗ $label"
    FAIL=$((FAIL+1))
  fi
}

echo "=== boot adapter on :$PORT ==="
# Use whatever python is on PATH — works inside an activated .venv or
# falls back to system python. Use .venv if it exists, else plain python.
if [ -x "./.venv/bin/python" ]; then
  PYBIN="./.venv/bin/python"
elif [ -x "./venv/bin/python" ]; then
  PYBIN="./venv/bin/python"
else
  PYBIN="python"
fi
$PYBIN -m uvicorn bridges.openclaw_adapters.server:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning &
ADAPTER_PID=$!
trap "kill $ADAPTER_PID 2>/dev/null || true" EXIT

# wait for boot
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.3
  if curl -sf "$BASE/healthz" >/dev/null 2>&1; then break; fi
done

echo "=== endpoint smoke ==="
check "healthz"               curl -sf "$BASE/healthz"
check "version"               curl -sf "$BASE/version"
check "kobe today_target"     curl -sf -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' -d '{}' "$BASE/kobe/today_target"
check "kobe pace"             curl -sf -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' -d '{}' "$BASE/kobe/pace"
check "kobe recalibration"    curl -sf -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' -d '{}' "$BASE/kobe/recalibration"
check "kobe charter_check"    curl -sf -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' -d '{"kind":"notify.user.reply"}' "$BASE/kobe/charter_check"
check "kobe project_eta"      curl -sf -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' -d '{"target_lbs":197,"daily_intake_kcal":2250,"weekly_active_kcal":6000}' "$BASE/kobe/project_eta"

echo "=== signal round-trip ==="
SID=$(curl -s -X POST "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" -H 'content-type: application/json' \
  -d '{"agent":"kobe","type":"plan_delivered","payload":{"day_type":"cf"},"trace_id":"smoke-1"}' \
  "$BASE/signals/publish" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["signal_id"])' 2>/dev/null)
if [ -n "$SID" ] && [ "$SID" -ge 1 ] 2>/dev/null; then
  echo "  ✓ signal published (id=$SID)"
  PASS=$((PASS+1))
else
  echo "  ✗ signal publish"
  FAIL=$((FAIL+1))
fi

RECENT=$(curl -sf "${AUTH_ARG[@]+"${AUTH_ARG[@]}"}" "$BASE/signals/recent?agent=kobe&limit=1" | grep -c "plan_delivered" || true)
if [ "$RECENT" -ge 1 ]; then
  echo "  ✓ signal read-back"
  PASS=$((PASS+1))
else
  echo "  ✗ signal read-back"
  FAIL=$((FAIL+1))
fi

echo
echo "=== summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
  echo "Stage 0 NOT clean — fix above before continuing."
  exit 1
fi
echo "Stage 0 GREEN. Foundation works."
echo "Next: integrate the TS plugin with OpenClaw (see new_plane/openclaw_plugin/README.md)."
