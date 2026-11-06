"""Python new_miya simulator — the reference orchestration loop.

Miya's orchestration loop in pure Python, calling the same tool
functions and signal store as the runner, so the logic can be
smoke-tested without Telegram or a model.

Why this exists:
  1. Validates the adapter + signal contracts end-to-end.
  2. Captures side-by-side comparisons (old Miya vs new orchestration)
     in a stack you control.
  3. Acts as the canonical reference implementation — the runner's
     orchestrator imports its rules, so the two cannot drift.

Run::

    python -m new_plane.miya_sim ask "what's my plan today"
    python -m new_plane.miya_sim ask "when will I hit 196 if I eat 2250 and burn 6000"
    python -m new_plane.miya_sim health   # cross-pollination gauge
"""
