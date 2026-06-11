"""Cross-agent typed signal interface — the load-bearing primitive.

Per the PM thesis v1.1 (`specs/RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md`),
this is the rule most likely to slip under build pressure and the one that,
if it slips, collapses the moat from "mesh + engine compound" to "agents
on a shared substrate" — which is forkable.

The contract:

  Signal {
      id:        int                  (assigned on publish)
      agent:     str                  (publisher: "kobe" | "fraser" | "miya" | ...)
      type:      str                  (e.g. "plan_delivered", "wod_designed",
                                       "user_thumbsup", "outcome_logged")
      payload:   dict                 (JSON-serializable, schema is per-type)
      ts:        datetime             (UTC, microsecond)
      trace_id:  str                  (correlates with OpenClaw session)
      consumed_by: list[str]          (which agents have *used* this signal
                                       in a decision — set via mark_consumed)
  }

Critical contract: per the PM thesis, **a signal is only "consumed" when
the reader uses it in a decision**, not when it's read. Readers MUST call
``mark_consumed(signal_id, consumer)`` after they fold the signal into a
decision. The cross-pollination only counts if signals are consumed; pure
publication without consumption is the failure mode.
"""

from .store import (  # noqa: F401
    Signal,
    publish,
    recent,
    mark_consumed,
    init_db,
)
