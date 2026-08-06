"""Genie — Rahat agent: household & weekend planning.

Thin, importlib-loadable entry point mirroring the four-file shape of
agents/the_scientist and agents/fraser:

    protocols.py → state.py → handler.py → main.py (here)

This file does two things:

  1. Star re-exports every public symbol from protocols/state/handler so
     the legacy short-name contract works — `genie.<symbol>` resolves the
     same way `sci.<symbol>` / `fraser.<symbol>` do. ScientistAgent and
     FraserAgent load their main.py via importlib under a short name; the
     GenieAgent below does the same.

  2. Exposes `GenieAgent` (name="genie") — a core.Agent subclass Miya can
     register and route to. The parent wires registration in
     new_plane/miya_runner/__main__.py (register(GenieAgent())); this
     module deliberately does NOT self-register so it stays import-safe
     and test-clean.

Multi-subject doctrine (PM thesis §3 rule #1): Genie reads ROLE-based
family Subjects from vault/family_profile.json (gitignored, PII-free).
See specs/agents/GENIE_AGENT_SPEC.md for the interface contract.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

# Repo root on path so package imports resolve under importlib loading.
# Idempotent — same pattern as the Scientist / Fraser main.py.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.agent import Agent, Reply  # noqa: E402

# Star-import order matters (protocols → state → handler) so every public
# symbol hangs off this module as `genie.<name>`.
from agents.genie.protocols import *  # noqa: F401, F403, E402
from agents.genie.state import *      # noqa: F401, F403, E402
from agents.genie.handler import *    # noqa: F401, F403, E402

from agents.genie import handler as _handler  # noqa: E402


# GenieAgent moved to agents/genie/agent.py (2026-08-06) so the
# agents.<pkg>.agent ABI-guard discovery convention holds (same layout as
# Kobe / Fraser) — required for boot-list membership: the guard's
# defense-in-depth check asserts every PRODUCTION_AGENT_CLASSES entry was
# discovered there. Re-exported here so the 06-15 scaffold pin
# (`from agents.genie.main import GenieAgent`) and any downstream import
# keep working unchanged.
from agents.genie.agent import GenieAgent, _load_genie_module  # noqa: E402, F401

__all__ = ["GenieAgent"]


if __name__ == "__main__":
    # Symmetric with the Scientist / Fraser main.py shape. start() is a
    # no-op stub — Genie runs under Miya, not as its own process.
    from agents.genie.handler import start
    start()
