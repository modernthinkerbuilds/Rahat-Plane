#!/usr/bin/env bash
# bootstrap.sh — clone-to-green-tests in five steps.
#
# Anyone with a fresh clone of rahat should be able to run this once
# and have a working venv + rendered launchd plists + green test suite,
# without editing any code or doc paths. That's the "Frictionless Setup"
# architectural principle in specs/ARCHITECTURE.md.
#
# Idempotent — safe to re-run.

set -euo pipefail

# Always operate on the repo this script lives in.
cd "$(dirname "$0")"
RAHAT_HOME="$PWD"

echo "════════════════════════════════════════════════════════════════════"
echo "  Rahat bootstrap — RAHAT_HOME=${RAHAT_HOME}"
echo "════════════════════════════════════════════════════════════════════"

# ── 1. Detect or create venv with Python 3.12+ ──────────────────────────
echo ""
if [ -d venv ] && [ -x venv/bin/python ]; then
  # Already-built venv wins — system python3 may be older (e.g. macOS
  # ships 3.9), but the venv has its own Python and that's what we use.
  echo "── 1. venv/ exists — using ./venv/bin/python ──"
  ./venv/bin/python --version
  ./venv/bin/python -c "import sys; assert sys.version_info >= (3, 12), \
      f'venv Python is {sys.version_info.major}.{sys.version_info.minor} — need 3.12+ ' \
      f'(rebuild: rm -rf venv && bash bootstrap.sh)'" \
    || exit 1
else
  echo "── 1. no venv — checking system Python (need 3.12+) ──"
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN=python3.12
  elif python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
    PYTHON_BIN=python3
  else
    echo "FAIL: need Python 3.12+ to create the venv."
    echo "      brew install python@3.12   (then re-run this script)"
    exit 1
  fi
  $PYTHON_BIN --version
  echo "── 2. creating venv/ with $PYTHON_BIN ──"
  $PYTHON_BIN -m venv venv
fi

# ── 3. Dependencies ─────────────────────────────────────────────────────
echo ""
echo "── 3. installing dependencies ──"
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements-dev.txt
echo "    deps installed (runtime + dev)."

# ── 4. Render launchd plists from templates ─────────────────────────────
echo ""
echo "── 4. rendering launchd plists from templates ──"
for tmpl in core/com.rahat.miya.plist.template \
            bridges/sugarwod/com.rahat.sugar.bridge.plist.template; do
  if [ ! -f "$tmpl" ]; then
    echo "    skipped (no template): $tmpl"
    continue
  fi
  out="${tmpl%.template}"
  sed "s|{{RAHAT_HOME}}|${RAHAT_HOME}|g" "$tmpl" > "$out"
  echo "    rendered: $out"
done

# ── 5. .env scaffolding ─────────────────────────────────────────────────
echo ""
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "── 5. created .env from .env.example — fill in API keys before running with live LLM ──"
  else
    echo "── 5. WARN: .env.example missing, cannot scaffold .env ──"
  fi
else
  echo "── 5. .env already exists — leaving alone ──"
fi

# ── 6. Validate with the hermetic test stack ────────────────────────────
echo ""
echo "── 6. running hermetic test stack (RAHAT_TEST_MODE=1, no live LLM) ──"
RAHAT_TEST_MODE=1 ./venv/bin/python -m tests.run_all

# ── Done ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Bootstrap complete."
echo ""
echo "  To install the launchd services on macOS:"
echo "      cp core/com.rahat.miya.plist ~/Library/LaunchAgents/"
echo "      launchctl load ~/Library/LaunchAgents/com.rahat.miya.plist"
echo ""
echo "  To run tests anytime:"
echo "      RAHAT_TEST_MODE=1 ./venv/bin/python -m tests.run_all"
echo "════════════════════════════════════════════════════════════════════"
