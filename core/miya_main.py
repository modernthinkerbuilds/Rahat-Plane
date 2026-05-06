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
from agents.the_scientist.agent import ScientistAgent      # noqa: E402

# When you ship Coach, Curriculum, Bajrangi, etc., import + register
# them here. Order is irrelevant — Miya routes by trigger match, not
# registration order.

miya.register(ScientistAgent())
# miya.register(CoachAgent())            # placeholder — Phase Next
# miya.register(BajrangiAgent())         # placeholder — Phase Next
# miya.register(CurriculumAgent())       # placeholder — Phase Next


if __name__ == "__main__":
    miya.run_loop()
