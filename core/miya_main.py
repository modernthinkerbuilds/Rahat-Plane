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
# Enabled 2026-05-14 (Day-7) per owner directive:
#   "Cards look good. Proceed to xfail cleanup. Cassettes can be
#    recorded later — don't block on them for the FraserAgent flip.
#    Goal: green eval suite, flip FraserAgent on, merge."
#
# Gate cleared:
#   ✓ All 10 eval cases in tests/evals/test_fraser_conversation.py
#     pass without xfail marks (Day-7).
#   ✓ Real Workout Card produced from real SugarWOD archive (Day-6
#     DAY5_DEMO_CARD.md — Lava Plume adapted: burn 720-984 kcal,
#     cool-down rendered, BW-scaling rationale, Kobe-target line).
#   ✓ Deterministic adapter handles: rest day, stale source,
#     injury mute, equipment swap, user dislike, BW scaling,
#     HRV-red intensity cap + overhead drop, sleep-debt cap,
#     recent-volume awareness, Kobe-target ±20% scaling.
#
# Cassettes deferred per directive — LLM enrichment is overlay-only
# (NOTES voice), so the structural adapter contract holds without
# real Gemini calls. When GEMINI_API_KEY lands in the runtime env,
# enrichment fires automatically via tests/cassettes/fraser/.
#
# route() now returns Reply(confidence=0.5) since the adapter
# produces real cards (not a stub). Miya's tie-breaker logic
# applies normally.
from agents.fraser.agent import FraserAgent                   # noqa: E402
miya.register(FraserAgent())
# miya.register(CoachAgent())            # placeholder — Phase Next
# miya.register(HubermanAgent())         # placeholder — Phase Next (was Bajrangi)
# miya.register(CurriculumAgent())       # placeholder — Phase Next


if __name__ == "__main__":
    miya.run_loop()
