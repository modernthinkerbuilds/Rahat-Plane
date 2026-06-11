"""Python new_miya simulator — pre-OpenClaw baseline.

Mirrors the TS plugin's orchestration loop in pure Python, calling the
same adapter endpoints and signal store. Lets you smoke-test the
orchestration logic BEFORE OpenClaw is wired.

Why this exists:
  1. Validates the adapter + signal contracts end-to-end without TS.
  2. Captures side-by-side comparisons (old Miya vs new orchestration)
     in a stack you control.
  3. Acts as the canonical reference implementation — when the TS plugin
     ships, its behavior should match this on the same inputs.

Run::

    python -m new_plane.miya_sim ask "what's my plan today"
    python -m new_plane.miya_sim ask "when will I hit 196 if I eat 2250 and burn 6000"
    python -m new_plane.miya_sim health   # cross-pollination gauge
"""
