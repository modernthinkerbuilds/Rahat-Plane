"""core.delegation — cross-agent delegation tool (ADR-007).

When an agent's reasoner receives a question outside its domain, it
should call `delegate_to(agent_name, query)` instead of synthesizing
from training-data priors. The delegated agent answers via its own
tools and state; the originating agent receives the response and can
either forward verbatim or wrap in its own voice.

Design notes
------------
* This module is the SINGLE entry point for cross-agent delegation.
  Agent tools register `delegate_to` and pass through. The orchestrator
  inside Miya is consulted to dispatch; we do NOT bypass Miya's
  routing (and its confidence policy from ADR-008).

* Loop prevention is structural: depth cap + caller-chain check.
  Kobe → Fraser is fine. Kobe → Fraser → Kobe returns
  {"error": "delegation_loop"} immediately so the loop-causing
  reasoner falls back to a direct answer.

* Every delegation lands as a `miya.delegate` span in the decisions
  ledger. Cross-agent observability is automatic: ask "how often does
  Kobe defer to Fraser" via a SQL query against decisions, no other
  instrumentation.

* Rollback via env: RAHAT_DELEGATION_ENABLED=0 disables the tool. The
  function returns a structured fallback so callers can degrade
  gracefully (synthesize a best-effort in-domain answer).

Contract — see ADR-007 §"Tool contract" for the full input/output
shape. Stable across the per-agent tool catalogs.
"""
from __future__ import annotations

import os
from typing import Any

from core import decisions
from core import io as cio


# Loop-prevention cap. 2 means: A → B is OK, A → B → C is OK if A ≠ C,
# A → B → A is rejected. Three-hop chains rejected outright. This is a
# floor, not a ceiling — most legitimate delegations are depth=1.
MAX_DELEGATION_DEPTH = 2

# Env override to disable delegation entirely (incident-debug knob).
_ENV_ENABLED = "RAHAT_DELEGATION_ENABLED"


def _enabled() -> bool:
    val = os.getenv(_ENV_ENABLED, "1").lower().strip()
    return val not in ("0", "false", "off", "no")


def delegate_to(
    agent_name: str,
    query: str,
    *,
    context: dict | None = None,
    _caller_chain: tuple[str, ...] = (),
    _depth: int = 0,
    trace_id: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Dispatch `query` to the agent named `agent_name`. Returns:

        {"agent": "<resolved name>",
         "reply": "<reply text>",
         "confidence": <0.0-1.0>,
         "delegation_depth": <int>,
         "trace_id": "<id>"}

    Or on failure:

        {"agent": None,
         "error": "agent_not_registered" | "delegation_loop" |
                  "delegation_disabled" | "depth_exceeded" |
                  "agent_error",
         "fallback_reply": "<best-effort string the caller can return>",
         "trace_id": "<id>"}

    Arguments:
        agent_name: target agent's canonical `name` field. Aliases are
            resolved (e.g. "the_scientist" → "kobe").
        query: the user's question — either the original message or a
            refined sub-question the calling reasoner has shaped.
        context: optional dict for structured handoff (e.g. relevant
            entity_ids). Not consumed by routing today, but logged in
            the decisions span so future agents can read it.
        _caller_chain, _depth: loop-prevention bookkeeping. Internal
            callers (Miya's reasoner-side glue) pass these; user-facing
            tool callers leave them at defaults.
        trace_id: the conversation's trace_id from decisions.new_trace().
            Carried through so the delegation span links to the
            originating message.

    Failure modes are documented codes, never exceptions. Callers
    pattern-match on the `error` field and use `fallback_reply` to
    keep the conversation alive when delegation can't complete.
    """
    tid = trace_id or decisions.new_trace()

    # 1. Disabled by ops — return structured fallback.
    if not _enabled():
        return {
            "agent": None,
            "error": "delegation_disabled",
            "fallback_reply": (
                "Cross-agent handoff is disabled right now. "
                "I'll do my best with my own tools."
            ),
            "trace_id": tid,
        }

    # 2. Depth cap.
    if _depth >= MAX_DELEGATION_DEPTH:
        return {
            "agent": None,
            "error": "depth_exceeded",
            "fallback_reply": (
                "I've already handed off too many times — answering "
                "from here without another delegation."
            ),
            "trace_id": tid,
        }

    # 3. Loop check: target already on the caller chain → loop.
    target = (agent_name or "").lower().strip()
    chain_lower = tuple(c.lower() for c in _caller_chain)
    if target in chain_lower:
        return {
            "agent": None,
            "error": "delegation_loop",
            "fallback_reply": (
                f"Can't delegate back to {target} — that would loop. "
                "Answering from here directly."
            ),
            "trace_id": tid,
        }

    # 4. Resolve agent (canonical name OR alias). Import here to avoid
    #    a circular import at module load (miya imports decisions, this
    #    imports miya; deferring breaks the cycle cleanly).
    from core import miya as _miya

    resolved = None
    for a in _miya.registered():
        names = [a.name.lower()] + [
            x.lower() for x in getattr(a, "aliases", [])
        ]
        if target in names:
            resolved = a
            break

    if resolved is None:
        return {
            "agent": None,
            "error": "agent_not_registered",
            "fallback_reply": (
                f"I don't know an agent named {agent_name!r}. "
                "Try one of: " + ", ".join(
                    a.name for a in _miya.registered()
                ) + "."
            ),
            "trace_id": tid,
        }

    # 5. Dispatch through the resolved agent's route() — bypassing the
    #    capability classifier (we already know who we're calling).
    #    Wrap in a decisions span for observability.
    with decisions.span(
        "miya.delegate",
        trace_id=tid,
        actor=_caller_chain[-1] if _caller_chain else "miya",
        input={
            "to": resolved.name,
            "query": query,
            "depth": _depth + 1,
            "caller_chain": list(chain_lower),
            "context_keys": list(context.keys()) if context else [],
        },
        db_path=db_path,
    ) as s:
        try:
            reply = resolved.route(query)
        except Exception as e:
            s.outcome = "error"
            s.error = f"{type(e).__name__}: {e}"
            return {
                "agent": resolved.name,
                "error": "agent_error",
                "fallback_reply": (
                    f"{resolved.name} couldn't handle the delegated "
                    f"question — answering from here instead."
                ),
                "trace_id": tid,
            }

        text = (reply.text or "") if reply else ""
        confidence = reply.confidence if reply else 0.0
        s.output = {
            "agent": resolved.name,
            "text_len": len(text),
            "confidence": confidence,
        }

    return {
        "agent": resolved.name,
        "reply": text,
        "confidence": confidence,
        "delegation_depth": _depth + 1,
        "trace_id": tid,
    }


__all__ = ["delegate_to", "MAX_DELEGATION_DEPTH"]
