"""bandit — the one §8a contextual-bandit learner (ADR-015).

DESIGN-ONLY / DEFAULT-OFF. This module ships the ``Learner`` Protocol and
an inert ``NoOpLearner``. It is intentionally NOT imported by
``new_plane.miya_runner.cost_router`` — wiring is a later phase gated
behind ``RAHAT_BANDIT=1`` (ADR-015 §3). Importing or instantiating
anything here changes no live behavior.

One learner, five endpoints (PM thesis §8a). An ``endpoint`` string
multiplexes the surfaces (``"cost_router"``, ``"memory_rank"``, ...) so we
do not ship five engines that happen to look similar. The §8b
trajectory-level learner is a *different* loop and is not modeled here.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Learner(Protocol):
    """Contextual-bandit interface shared by all five §8a routing surfaces.

    Implementations must keep ``decide`` deterministic given
    (model-state, context, trace_id) so the choice is by_trace-replayable
    (ADR-015 §4).
    """

    def decide(self, *, endpoint: str, context: dict[str, Any],
               actions: Sequence[str], trace_id: str) -> str:
        """Return exactly one action from ``actions`` for this context."""
        ...

    def observe(self, *, endpoint: str, trace_id: str, action: str,
                reward: float, context: dict[str, Any]) -> None:
        """Fold a VALIDATED outcome (reward) back into the policy.

        ``reward`` is produced by the endpoint's outcome *validator*
        (ADR-015 §2), not raw-logged — logged != validated.
        """
        ...


class NoOpLearner:
    """Inert learner: returns the cost-router heuristic's choice, learns
    nothing. This is the Phase-0 placeholder (ADR-015 §5) that lets the
    interface be exercised without any live behavior or model state.

    It deliberately does NOT import cost_router (to avoid any wiring or
    import-cycle); instead it reproduces the heuristic's safe-default
    contract: if the caller passes a precomputed ``heuristic_choice`` in
    the context, return it when it's a valid action; otherwise return the
    first action (the safe/default arm — Flash is actions[0] for the cost
    router, matching the heuristic's default).
    """

    def decide(self, *, endpoint: str, context: dict[str, Any],
               actions: Sequence[str], trace_id: str) -> str:
        if not actions:
            raise ValueError("actions must be non-empty")
        choice = context.get("heuristic_choice")
        if isinstance(choice, str) and choice in actions:
            return choice
        return actions[0]

    def observe(self, *, endpoint: str, trace_id: str, action: str,
                reward: float, context: dict[str, Any]) -> None:
        # Inert by design — Phase 0 learns nothing (ADR-015 §5).
        return None
