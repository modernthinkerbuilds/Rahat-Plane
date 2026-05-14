"""core.miya_main — launchd entry point for the orchestrator.

Replaces `agents/the_scientist/main.py` as the single user-facing
process. Registers every agent in the mesh with Miya, then hands
control to `core.miya.run_loop()`.

Adding a new agent later is exactly two lines: import its class,
`miya.register(NewAgent())`. No other code changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo root on path so package imports resolve when launched by launchd.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core import miya                                       # noqa: E402
# Import via the new brand path — agents.kobe is an alias package that
# transparently re-points at agents.the_scientist (see agents/kobe/
# __init__.py). After the alias retires in one week, this line stays;
# only the underlying file location changes.
from agents.kobe.agent import KobeAgent                     # noqa: E402

# When you ship Coach, Curriculum, Huberman, etc., import + register
# them here. Order is irrelevant — Miya routes by trigger match, not
# registration order.

miya.register(KobeAgent())
# Fraser — CrossFit programming & performance agent. Day-3 wiring
# landed 2026-05-14: Charter policies registered (HRV-red gate on
# fraser.workout.commit, green-required on fraser.1rm.update for
# increases), substitution-condition vocabulary stabilized
# (ADR-004), token-budget ledger in place (ADR-005). The reasoner
# itself is still stubbed (returns low-confidence Reply) — the
# real Gemini 2.5 Flash wiring lands in a follow-up commit on
# this branch. Description-based classification puts Fraser in
# the routing pool; the stub's confidence=0.1 makes Miya's
# tie-breaker prefer Kobe for ambiguous fitness queries.
from agents.fraser.agent import FraserAgent                   # noqa: E402
miya.register(FraserAgent())
# miya.register(CoachAgent())            # placeholder — Phase Next
# miya.register(HubermanAgent())         # placeholder — Phase Next (was Bajrangi)
# miya.register(CurriculumAgent())       # placeholder — Phase Next


if __name__ == "__main__":
    miya.run_loop()
