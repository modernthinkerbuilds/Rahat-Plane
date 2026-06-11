"""Cost router v0 — Flash by default, Pro on hard prompts.

Per the PM thesis v1.1, this is one of the three hard capabilities that
will eventually become an outcome-validated learner (week 2+). For now,
it's a hard-coded heuristic so we can start collecting outcome data:

  - DEFAULT: gemini-2.5-flash (cheap, fast)
  - ESCALATE TO PRO when:
      • message length > 200 chars (reasoning-heavy queries)
      • contains keywords suggesting multi-step reasoning
        ("project", "compare", "explain why", "plan for the week", etc.)
      • arbitration verdict fires (the loop wants a careful response)

Every decision is logged as a JSONL line in $OPENCLAW_COST_LOG. The
learner upgrade reads those logs to fit a contextual bandit.

This is intentionally NOT pluggable yet — keep the v0 surface tiny,
add the learner abstraction in week 2 once we know the real signal.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default models. Override per .env for cost experiments.
MODEL_FLASH = os.getenv("NEW_MIYA_MODEL_FLASH", "gemini-2.5-flash")
MODEL_PRO = os.getenv("NEW_MIYA_MODEL_PRO", "gemini-2.5-pro")

# Where to log routing decisions. Set to empty string to disable logging.
COST_LOG_PATH = os.getenv("OPENCLAW_COST_LOG",
                          os.path.expanduser("~/.rahat/cost_router.log"))

# Heuristic thresholds — can tune via env without code changes.
PRO_THRESHOLD_CHARS = int(os.getenv("NEW_MIYA_PRO_THRESHOLD_CHARS", "200"))

# Patterns that suggest the user wants reasoning, not just a lookup.
# Word boundaries on either side so "plan" matches but "planning" doesn't
# unless we add it explicitly.
_HARD_PROMPT_PATTERNS = [
    re.compile(r"\bproject\b", re.I),       # "project goal", "project ETA"
    re.compile(r"\bcompare\b", re.I),
    re.compile(r"\bexplain\s+why\b", re.I),
    re.compile(r"\bwhy\s+(am|is|are|did|do)\b", re.I),
    re.compile(r"\bweek\b", re.I),          # weekly planning is multi-step
    re.compile(r"\bnext\s+\d+\s+(weeks?|days?|months?)\b", re.I),
    re.compile(r"\bplan\s+(for|the)\b", re.I),
    re.compile(r"\bcatch\s+up\b", re.I),    # implies recalibration reasoning
    re.compile(r"\bshould\s+i\b", re.I),    # decision-seeking
]


@dataclass
class RoutingDecision:
    model: str
    reason: str
    user_message_len: int
    arbitration_rule: str | None = None
    matched_patterns: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    trace_id: str = ""

    def to_log(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "trace_id": self.trace_id,
            "model": self.model,
            "reason": self.reason,
            "user_message_len": self.user_message_len,
            "arbitration_rule": self.arbitration_rule,
            "matched_patterns": self.matched_patterns,
        }


def decide(user_message: str, *,
           arbitration_rule: str | None = None,
           trace_id: str = "") -> RoutingDecision:
    """Pick a model for this turn.

    Returns the routing decision — caller is responsible for logging
    via `log_decision()` (decoupled so tests can inspect without I/O).
    """
    matched: list[str] = []
    for pat in _HARD_PROMPT_PATTERNS:
        if pat.search(user_message):
            matched.append(pat.pattern)

    if matched:
        return RoutingDecision(
            model=MODEL_PRO,
            reason="hard-prompt-patterns-matched",
            user_message_len=len(user_message),
            arbitration_rule=arbitration_rule,
            matched_patterns=matched,
            trace_id=trace_id,
        )

    if len(user_message) > PRO_THRESHOLD_CHARS:
        return RoutingDecision(
            model=MODEL_PRO,
            reason=f"message-len>{PRO_THRESHOLD_CHARS}",
            user_message_len=len(user_message),
            arbitration_rule=arbitration_rule,
            trace_id=trace_id,
        )

    if arbitration_rule:
        # If the arbitration loop fired (behind_pace, goal_close), spend
        # more on synthesis so the response is careful and honest.
        return RoutingDecision(
            model=MODEL_PRO,
            reason=f"arbitration-fired-{arbitration_rule}",
            user_message_len=len(user_message),
            arbitration_rule=arbitration_rule,
            trace_id=trace_id,
        )

    return RoutingDecision(
        model=MODEL_FLASH,
        reason="default-flash",
        user_message_len=len(user_message),
        arbitration_rule=arbitration_rule,
        trace_id=trace_id,
    )


def log_decision(decision: RoutingDecision, *,
                 path: str | None = None) -> None:
    """Append decision to JSONL cost log. Idempotent + best-effort —
    never raises on disk errors (logging must not break the runner).
    """
    target = path if path is not None else COST_LOG_PATH
    if not target:
        return
    try:
        p = Path(target).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_log(), default=str) + "\n")
    except Exception as e:
        logger.warning("cost-log write failed: %s: %s", type(e).__name__, e)
