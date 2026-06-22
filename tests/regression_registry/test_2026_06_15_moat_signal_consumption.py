"""Contract: the moat's signal-consumption rule (thesis §4 #6, §8.3).

The differentiator is a TYPED cross-agent signal interface that is actually
CONSUMED in a decision — not merely published and read. Today the store
exposes mark_consumed but nothing in the runtime decision path calls it
(it's invoked only by the HTTP adapter endpoint + tests), so the rule is
decoration. Per the thesis this is "the rule most likely to slip under
build pressure."

These tests pin it:
  - the consumption primitive exists (always passes);
  - SOME decision path consumes a typed signal — xfail until wired
    (PRE_SCALE_PLAN §C-P0 / Lane 6). When arbitration consumes a signal,
    this flips green on its own. Do NOT delete the xfail to "go green";
    wire the consumption.
"""
from __future__ import annotations

import inspect

import pytest


def test_signal_store_exposes_consumption_primitive():
    from new_plane.signals import store
    assert hasattr(store, "mark_consumed"), \
        "signal store lost mark_consumed — the consumption half of the interface"


def test_a_decision_path_consumes_a_typed_signal():  # HARD PIN (moat wired 2026-06-16)
    from new_plane.miya_runner import orchestrator
    src = inspect.getsource(orchestrator)
    assert "mark_consumed" in src, (
        "orchestrator never consumes a typed signal in a decision — "
        "publish+read without consume is not the moat (thesis §4 #6)."
    )
