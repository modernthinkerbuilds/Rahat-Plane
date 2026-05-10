#!/usr/bin/env bash
# _phase4d_step1a_state.sh
# Phase 4d (R1) Step 1a: extract DB helpers from main.py into state.py.
#
# WHAT MOVES (10 functions, ~240 LOC):
#   _db, state_get, state_set,
#   burn_for_date, burn_for_range, burn_this_week, burn_last_week,
#   weekly_target, get_active_intent, check_external_veto.
#
# IMPORT CONTRACT PRESERVED:
#   - Function bodies move byte-identical (Python sliced the section
#     out of main.py and inserted it into state.py — no edits).
#   - main.py adds `from agents.the_scientist.state import *` so legacy
#     sci.<name> patterns still resolve via the new module.
#   - state.py's __all__ explicitly lists every name (including _db,
#     which star-imports skip without an __all__ entry).
#
# REGRESSION GATES:
#   - Pre-baseline test (must be green BEFORE any change)
#   - Syntax check on both state.py and main.py post-edit
#   - Explicit sci.* attribute resolution check for all 10 moved names
#   - Post-change full test stack
#   - eval_suite smoke-test (must still report 148 cases)

set -euo pipefail

# ── Sanity ──────────────────────────────────────────────────────────────────
[ -d ".git" ] || { echo "ERROR: not a git repo"; exit 1; }
[ -f agents/the_scientist/main.py ] || { echo "ERROR: main.py missing"; exit 1; }
[ ! -f agents/the_scientist/state.py ] || {
    echo "ERROR: agents/the_scientist/state.py already exists — aborting."; exit 1; }

# ── 1. Baseline ─────────────────────────────────────────────────────────────
echo "── 1. baseline test run (must be green BEFORE we change anything) ──"
RAHAT_TEST_MODE=1 ./venv/bin/python -m tests.run_all || {
    echo "FAIL: baseline tests failed; refusing to refactor a red repo."; exit 1; }

# ── 2. Surgery ──────────────────────────────────────────────────────────────
echo ""
echo "── 2. extracting Section 3 (DB helpers) into state.py ──"
RAHAT_TEST_MODE=1 ./venv/bin/python <<'PYEOF'
import re
from pathlib import Path

ROOT = Path('.')
MAIN  = ROOT / 'agents/the_scientist/main.py'
STATE = ROOT / 'agents/the_scientist/state.py'

src = MAIN.read_text()

# Locate Section 3 by its em-dash header (variable dash count, so regex).
m_start = re.search(r"^# ─+ DB helpers ─+\s*$", src, re.MULTILINE)
m_end   = re.search(r"^# ─+ Weekly plan", src, re.MULTILINE)
if not m_start or not m_end:
    raise SystemExit(
        f"could not locate section markers (start={m_start}, end={m_end})")

start = m_start.start()
end = m_end.start()
section_body = src[start:end]

# Sanity: at least 8 top-level defs in this slice.
n_defs = len(re.findall(r"^def \w", section_body, re.MULTILINE))
print(f"  Section 3 slice: {len(section_body)} bytes, {n_defs} top-level defs")
if n_defs < 8:
    raise SystemExit(f"expected ~10 functions, found {n_defs}")

# Sanity: must use cio.DB_PATH (proves Step 0a was applied).
if "cio.DB_PATH" not in section_body:
    raise SystemExit("section_body doesn't use cio.DB_PATH — Step 0a missing?")

# Strip the section header line; keep only function definitions.
first_def_match = re.search(r"^def \w", section_body, re.MULTILINE)
if not first_def_match:
    raise SystemExit("no top-level def in slice")
fn_bodies = section_body[first_def_match.start():]

# Build state.py.
state_header = '''"""state — Scientist's stateful (DB-backed) data layer.

Extracted from main.py per Phase 4d (R1) Step 1a. These functions own
the I/O boundary: every call here opens/closes a sqlite3 connection
against the Scientist's intent ledger.

DB path comes from `core.io.DB_PATH` (centralized in Step 0a, commit
11317c9). RAHAT_TEST_MODE=1 redirects writes to a per-process tempfile,
RAHAT_DB_PATH lets ops point at a custom DB, and tests patch
`cio.DB_PATH = X` to sandbox individual cases.

What's in here:
    • `_db()` — connection factory + auto-migration of every owned table
    • `state_get` / `state_set` — user_state KV
    • `burn_for_date` / `burn_for_range` / `burn_this_week` / `burn_last_week`
    • `weekly_target` — layered: memory commitment > active tier > legacy
    • `get_active_intent` / `check_external_veto` — cross-agent signals

What's NOT here (stays in main.py for now):
    • Per-week preferences + weight/HRV log helpers — Phase 4d Step 1b
    • Handlers, router, nudges, loop — Phase 4d Step 2
    • Pure math + constants — protocols.py

Importing rule:
    from agents.the_scientist.state import _db, state_get, ...

The legacy `sci._db()` / `sci.state_get()` patterns still resolve because
main.py does `from agents.the_scientist.state import *` and state.py's
__all__ exports every public + underscored name explicitly.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from core import io as cio

from agents.the_scientist.protocols import (
    DEFAULT_TIER,
    INTENT_INTERMEDIATE_DATE, INTENT_INTERMEDIATE_KG,
    INTENT_TARGET_DATE, INTENT_TARGET_KG,
    TIERS,
    WEEKLY_ACTIVE_TARGET_KCAL,
    week_bounds,
)

__all__ = [
    "_db",
    "burn_for_date",
    "burn_for_range",
    "burn_last_week",
    "burn_this_week",
    "check_external_veto",
    "get_active_intent",
    "state_get",
    "state_set",
    "weekly_target",
]


'''

STATE.write_text(state_header + fn_bodies)
print(f"  wrote agents/the_scientist/state.py ({len(STATE.read_text())} bytes)")

# Replace Section 3 in main.py with a re-export.
replacement = '''# ── DB helpers extracted to agents/the_scientist/state.py ──
# Phase 4d (R1) Step 1a: the connection factory, KV state get/set,
# burn-window aggregations, weekly-target resolution, and intent-ledger
# readers all moved into state.py. This re-export preserves the legacy
# `sci.<name>` import contract used by ScientistAgent's importlib loader
# and by every eval file.
from agents.the_scientist.state import *  # noqa: F401, F403, E402


'''

new_src = src[:start] + replacement + src[end:]
MAIN.write_text(new_src)
print(f"  rewrote agents/the_scientist/main.py ({len(new_src)} bytes, was {len(src)})")
PYEOF

# ── 3. Syntax checks ────────────────────────────────────────────────────────
echo ""
echo "── 3. syntax checks ──"
./venv/bin/python -c "import agents.the_scientist.state; print('state.py imports OK')" || {
    echo "FAIL: state.py has a syntax/import error."; exit 1; }
./venv/bin/python -c "import agents.the_scientist.main;  print('main.py imports OK')" || {
    echo "FAIL: main.py has a syntax/import error."; exit 1; }

# ── 4. sci.* import contract check ──────────────────────────────────────────
echo ""
echo "── 4. sci.* import contract check ──"
RAHAT_TEST_MODE=1 ./venv/bin/python <<'PYEOF'
import importlib.util
spec = importlib.util.spec_from_file_location("sci", "agents/the_scientist/main.py")
sci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sci)

required = ["_db", "state_get", "state_set",
            "burn_for_date", "burn_for_range",
            "burn_this_week", "burn_last_week",
            "weekly_target",
            "get_active_intent", "check_external_veto"]
missing = [n for n in required if not hasattr(sci, n)]
if missing:
    raise SystemExit(f"FAIL: sci is missing these names: {missing}")
print(f"  all {len(required)} sci.* names resolve correctly")
PYEOF

# ── 5. Full test stack ──────────────────────────────────────────────────────
echo ""
echo "── 5. post-change test run ──"
RAHAT_TEST_MODE=1 ./venv/bin/python -m tests.run_all || {
    echo "FAIL: tests went red after the change."
    echo "      Revert with:"
    echo "        git checkout -- agents/the_scientist/main.py"
    echo "        rm  agents/the_scientist/state.py"
    exit 1
}

# ── 6. eval_suite smoke ─────────────────────────────────────────────────────
echo ""
echo "── 6. smoke-test: eval_suite still loads with 148 cases ──"
RAHAT_TEST_MODE=1 ./venv/bin/python -c "
import tests.scientist.eval_suite as es
n = len(getattr(es, 'TESTS', []))
print(f'  eval_suite loaded: {n} cases')
assert n == 148, f'expected 148 cases, got {n}'
"

# ── 7. Preview ──────────────────────────────────────────────────────────────
echo ""
echo "── 7. staged delta ──"
git diff --stat agents/the_scientist/
git status --short agents/the_scientist/

# ── 8. Commit instructions ──────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────────────────────"
echo "If everything green: commit:"
echo ""
cat <<'COMMIT'
    git add agents/the_scientist/main.py agents/the_scientist/state.py
    git commit -m "refactor(scientist): extract DB helpers into state.py (Phase 4d R1 Step 1a)

10 functions moved from agents/the_scientist/main.py into a new
agents/the_scientist/state.py:

    _db, state_get, state_set,
    burn_for_date, burn_for_range, burn_this_week, burn_last_week,
    weekly_target, get_active_intent, check_external_veto.

Function bodies are byte-identical — only their location changed. DB
path resolution still goes through cio.DB_PATH at call time (centralized
in Step 0a, commit 11317c9), so RAHAT_TEST_MODE=1 / RAHAT_DB_PATH /
per-test cio.DB_PATH = X patches all keep working without any further
wiring.

main.py now does 'from agents.the_scientist.state import *' so the
legacy sci.<name> import contract (used by ScientistAgent's importlib
loader and by 10+ eval files) keeps working byte-identical. state.py's
__all__ explicitly lists every public + underscored name (including
_db, which Python's star-import otherwise skips).

Verified post-change:
  - 142 passed / 1 skipped (identical to pre-change)
  - All 10 sci.* attribute lookups resolve correctly
  - tests.scientist.eval_suite still loads with 148 cases

This is the first concrete step of the main.py god-file split per
specs/PHASE_4D_R1_PLAN.md. main.py shrinks from 2,930 LOC to ~2,690 LOC."
COMMIT
echo ""
echo "If anything went wrong:"
echo "    git checkout -- agents/the_scientist/main.py"
echo "    rm agents/the_scientist/state.py"
echo "─────────────────────────────────────────────────────────────────────"
