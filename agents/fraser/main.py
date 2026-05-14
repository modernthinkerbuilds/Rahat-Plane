"""Fraser — Rahat agent: CrossFit programming & performance.

Design (post-ADR-003):
- Storage doctrine: ZERO new tables. Every entity lives in
  `memory_entities` with `agent="fraser"` and `type=fraser_*`.
- Four-file shape mirrors the Scientist→Kobe split — `protocols.py`,
  `state.py`, `handler.py`, this file.
- The reasoner (Day 3) is Gemini 2.5 Flash; today it's stubbed so the
  surface around it is fully testable.

This file is the importlib target for the `fraser` short-name (the
eval-suite contract: `fraser.<symbol>` works just like `sci.<symbol>`
does for the Scientist). The star re-exports below hang every public
symbol off this module so call sites don't care which sub-module owns
which name.

Boot semantics:
- Imported by `agents/fraser/agent.py` via importlib, registered into
  `sys.modules["fraser"]` for the legacy short-name contract.
- Imported as a normal package member by tests and by miya_main.
- `start()` is a stubbed no-op for Day 1 (see handler.py); Fraser
  doesn't own a bot loop — it runs inside Miya.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo root on path so package imports resolve under importlib loading.
# Same idempotent pattern the Scientist main.py uses.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Star-import order matters. The five-file pattern (see
# specs/ADR-005-five-file-agent-pattern.md):
#   protocols → state → tools → handler → main (here)
# protocols defines the types; state owns the substrate wrappers;
# tools owns pure transforms (no DB); handler owns orchestration.
# Every public symbol in any of those modules is reachable as
# `fraser.<name>` after this file is loaded.
from agents.fraser.protocols import *  # noqa: F401, F403, E402
from agents.fraser.state import *      # noqa: F401, F403, E402
from agents.fraser.tools import *      # noqa: F401, F403, E402
from agents.fraser.handler import *    # noqa: F401, F403, E402


if __name__ == "__main__":
    # Symmetric with the Scientist main.py shape. handler.start() is a
    # no-op stub today — Fraser runs under Miya, not as its own process.
    from agents.fraser.handler import start
    start()
