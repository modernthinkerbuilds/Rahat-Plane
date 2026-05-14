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
# Fraser — CrossFit programming & performance agent.
#
# Registration is intentionally OFF until BOTH gates clear:
#   (a) All 10 eval cases in tests/evals/test_fraser_conversation.py
#       pass without xfail marks (the strict-mode cadence — each
#       commit drops one mark as the case stabilizes).
#   (b) Owner has reviewed ≥3 real workout cards end-to-end via
#       a controlled session before this line goes live in
#       production.
#
# Prior versions of this comment (Day-3 wiring 2026-05-14) had this
# line uncommented; that was reverted on Day-4 addendum after the
# gate tightened. The class is importable; route() returns a low-
# confidence stub Reply, so a stray import does NOT surface stub
# output to the user — but the safest position is "not in registry"
# until the gate clears.
#
# Charter policies, budget enforcement, fixture mode, and the
# tool-catalog manifests are all wired and tested under the
# scaffold — uncommenting this line is the LAST step, not the
# first one. See DAY4_REPORT_addendum_2.md for the gate spec.
# from agents.fraser.agent import FraserAgent
# miya.register(FraserAgent())
# miya.register(CoachAgent())            # placeholder — Phase Next
# miya.register(HubermanAgent())         # placeholder — Phase Next (was Bajrangi)
# miya.register(CurriculumAgent())       # placeholder — Phase Next


if __name__ == "__main__":
    miya.run_loop()
