"""new_plane.learn — the outcome-validated learning engine (ADR-015).

Per the PM thesis §8a, the five immediate-decision surfaces (cost routing,
memory ranking, prompt-variant routing, personalization, nudge timing)
share ONE contextual-bandit learner over a discrete action space. This
package houses that single learner.

ADR-015 status: design-only. The only thing shipped here is the
``Learner`` Protocol plus an inert ``NoOpLearner`` that returns the
existing cost-router heuristic's choice. It is NOT wired into
``cost_router.decide()`` — it changes no live behavior and exists to pin
the interface. The real bandit, the outcome validator, and the
``RAHAT_BANDIT=1`` serving path are later phases (see ADR-015 §5).

The §8b trajectory/offline-policy learner (conflict policy) is a separate
loop and does NOT live behind this Protocol.
"""

from .bandit import Learner, NoOpLearner  # noqa: F401
