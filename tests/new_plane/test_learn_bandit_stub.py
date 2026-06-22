"""ADR-015 Phase-0 stub: the §8a Learner interface is importable and inert.

These tests pin only that the design-only stub exists, conforms to the
Protocol, and changes no behavior. There is no real learner here yet —
when the bandit lands (ADR-015 Phase 1+) these document the inert
baseline it replaces.

Crucially, this test also asserts the stub is NOT wired into the live
cost router (ADR-015 §3: default-off, no live behavior change).
"""
from __future__ import annotations

import pytest

from new_plane.learn import Learner, NoOpLearner


def test_noop_learner_satisfies_protocol():
    learner = NoOpLearner()
    # runtime_checkable Protocol — structural conformance.
    assert isinstance(learner, Learner)


def test_decide_returns_heuristic_choice_when_provided():
    learner = NoOpLearner()
    out = learner.decide(
        endpoint="cost_router",
        context={"heuristic_choice": "gemini-2.5-pro"},
        actions=["gemini-2.5-flash", "gemini-2.5-pro"],
        trace_id="t1",
    )
    assert out == "gemini-2.5-pro"


def test_decide_defaults_to_first_action_when_no_hint():
    learner = NoOpLearner()
    out = learner.decide(
        endpoint="cost_router",
        context={},
        actions=["gemini-2.5-flash", "gemini-2.5-pro"],
        trace_id="t2",
    )
    # actions[0] is the safe/default arm (Flash for the cost router).
    assert out == "gemini-2.5-flash"


def test_decide_ignores_invalid_heuristic_choice():
    learner = NoOpLearner()
    out = learner.decide(
        endpoint="cost_router",
        context={"heuristic_choice": "not-a-real-model"},
        actions=["gemini-2.5-flash", "gemini-2.5-pro"],
        trace_id="t3",
    )
    assert out == "gemini-2.5-flash"


def test_decide_is_deterministic():
    learner = NoOpLearner()
    kwargs = dict(
        endpoint="cost_router",
        context={"heuristic_choice": "gemini-2.5-pro"},
        actions=["gemini-2.5-flash", "gemini-2.5-pro"],
        trace_id="t4",
    )
    assert learner.decide(**kwargs) == learner.decide(**kwargs)


def test_decide_empty_actions_raises():
    learner = NoOpLearner()
    with pytest.raises(ValueError):
        learner.decide(endpoint="cost_router", context={},
                       actions=[], trace_id="t5")


def test_observe_is_inert():
    learner = NoOpLearner()
    # No state, no return, no exception — Phase 0 learns nothing.
    assert learner.observe(
        endpoint="cost_router", trace_id="t6",
        action="gemini-2.5-flash", reward=1.0, context={},
    ) is None


def test_stub_not_wired_into_cost_router():
    """ADR-015 §3: the stub must NOT be referenced by the live router.
    cost_router stays the pure heuristic until RAHAT_BANDIT serving lands.
    """
    import inspect
    from new_plane.miya_runner import cost_router
    src = inspect.getsource(cost_router)
    # Check for actual wiring (imports / references), not prose. The
    # docstring legitimately says "learner" — that's the design intent,
    # not a live import.
    assert "new_plane.learn" not in src
    assert "import bandit" not in src
    assert "NoOpLearner" not in src
    assert "Learner(" not in src and "Learner." not in src
    # And the module genuinely does not import the learn package.
    import sys
    cr_mod = sys.modules[cost_router.__name__]
    assert not hasattr(cr_mod, "NoOpLearner")
    assert not hasattr(cr_mod, "Learner")
