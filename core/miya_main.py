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
# Fraser — CrossFit programming & performance agent. The class is
# importable today (Day-1 scaffold landed 2026-05-14, feature branch
# feat/fraser-day1-scaffold) but registration is intentionally OFF
# until the reasoner is wired on Day 3. Uncomment when handler.route()
# stops returning the low-confidence stub Reply. See DAY1_REPORT.md
# and specs/FRASER_OPEN_QUESTIONS.md item 8.
# from agents.fraser.agent import FraserAgent
# miya.register(FraserAgent())
# miya.register(CoachAgent())            # placeholder — Phase Next
# miya.register(HubermanAgent())         # placeholder — Phase Next (was Bajrangi)
# miya.register(CurriculumAgent())       # placeholder — Phase Next


if __name__ == "__main__":
    miya.run_loop()
