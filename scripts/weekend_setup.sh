#!/usr/bin/env bash
# weekend_setup.sh — Stage 0 setup steps for the new plane.
#
# Run from ~/developer/agency/rahat. Idempotent — safe to re-run.
# Does NOT install Node — assumes you have node >= 20 already.
set -euo pipefail

REPO="${REPO:-$HOME/developer/agency/rahat}"
cd "$REPO"

echo "=== 1) Python adapter deps ==="
./venv/bin/pip install fastapi 'uvicorn[standard]' httpx --quiet
echo "  installed: fastapi, uvicorn, httpx"

echo "=== 2) Python adapter smoke (import-only) ==="
./venv/bin/python -c "from bridges.openclaw_adapters.server import app; print('  import OK,', app.title)"

echo "=== 3) Signal store init ==="
./venv/bin/python -c "from new_plane.signals.store import init_db, _path; init_db(); print('  signals DB at:', _path())"

echo "=== 4) Adapter test suite (Python) ==="
RAHAT_TEST_MODE=1 GEMINI_API_KEY='' ./venv/bin/python -m pytest tests/new_plane/ -q

echo "=== 5) TS plugin install ==="
if command -v pnpm >/dev/null 2>&1; then PM=pnpm; else PM=npm; fi
echo "  using $PM"
( cd new_plane/openclaw_plugin && $PM install )
( cd new_plane/openclaw_plugin && $PM run typecheck )

echo
echo "=== Setup complete ==="
echo "Next: run scripts/weekend_smoke.sh (will start the adapter + verify a round-trip)."
echo "Make sure .env has OPENCLAW_ADAPTER_TOKEN set if you want auth on."
