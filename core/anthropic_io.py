"""core.anthropic_io — REMOVED 2026-05-08.

Anthropic was removed from the Rahat runtime as part of the strategic
decision documented in `specs/MODEL-FIRST-PIVOT.md` §1 update note. The
primary reasoner is now Gemini 2.5 Flash (with 2.5 Pro as the high-
stakes opt-in); see `core/gemini_reasoner_io.py`.

This file is left as a tombstone rather than deleted so that:
  - any straggler import surfaces as a clear ImportError, not as a
    "module not found" mystery,
  - git blame still points at this comment when someone wonders why
    the model-first pivot once said "Haiku 4.5 default."

If you need to re-introduce Anthropic later, restore from
   git show <pre-2026-05-08-commit>:core/anthropic_io.py > core/anthropic_io.py
and re-add the dep + key. Don't unhide silently — make the change
explicit because the strategic context that motivated the removal
should be re-examined first.
"""
raise ImportError(
    "core.anthropic_io was removed 2026-05-08. The Rahat runtime is now "
    "Gemini-only (2.5 Flash default, 2.5 Pro high-stakes). Use "
    "core.gemini_reasoner_io instead. See specs/MODEL-FIRST-PIVOT.md."
)
