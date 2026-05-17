# ADR-007 — Cross-agent delegation tool

**Date:** 2026-05-16  
**Status:** Accepted (Chief Architect, owner-approved 2026-05-16 evening)  
**Context:** Even after ADR-006 (capability router) lands, ambiguous
messages can still reach the "wrong" agent — e.g., Kobe is asked
about today's WOD when the user really wants Fraser's adapted card.
Currently Kobe's reasoner generates a plausible-looking workout from
training-data priors instead of deferring. Pair to ADR-006 (routing)
and ADR-008 (clarification).

## Decision

Every agent's reasoner gets a tool: **`delegate_to(agent_name: str, query: str, context: dict | None = None) -> dict`**. When an agent receives a question outside its domain, the reasoner calls this tool instead of hallucinating. The delegated agent answers in its own voice; the originating agent forwards the response with attribution (or wraps it in its own framing).

## Tool contract

```python
def delegate_to(
    agent_name: str,                    # "kobe" | "fraser" | "huberman" | ...
    query: str,                          # the user's original message OR a refined sub-question
    context: dict | None = None,         # optional structured handoff context
) -> dict:
    """Returns:
        {"agent": "fraser",
         "reply": "Today — Zone-2 10K. Actual: 243 kcal / Expected: 700 kcal …",
         "confidence": 0.91,
         "delegation_depth": 1,
         "trace_id": "…"}
    Or on failure:
        {"agent": None, "error": "agent_not_registered" | "delegation_loop" | "agent_error",
         "fallback_reply": "<best-effort string the caller can return>"}
    """
```

## Loop prevention

* **Max delegation depth: 2.** Kobe → Fraser is OK. Kobe → Fraser → Kobe is rejected.
* The `delegation_depth` field is passed through the call chain (Miya enforces).
* Loop detection: if an agent tries to delegate back to a name already in its caller chain, the tool returns `{"error": "delegation_loop"}` and the calling reasoner is told to answer directly.

## Observability — decisions ledger

Every delegation emits a span:

```python
with decisions.span("miya.delegate",
                    trace_id=tid,
                    actor=caller_agent_name,
                    input={"to": agent_name, "query": query, "depth": depth}):
    s.output = {"agent": resolved_name, "confidence": conf}
```

This lets you query post-hoc: "how often does Kobe delegate to Fraser this week?" (a real metric for whether agent descriptions are well-tuned).

## System prompt updates

Each agent's system prompt gets a section:

```
DELEGATION POLICY

For ANY question outside your domain, call delegate_to(agent_name, query).
Do NOT synthesize from training-data priors. The other agents have
specialized tools and state you don't have access to.

Domain boundaries:
- kobe owns: weight, HRV, weekly burn targets, weight-loss timeline,
  recovery tier, breathing/cooldown/pre-fuel protocols.
- fraser owns: today's WOD prescription, movement substitutions,
  weight calculations from 1RMs, predicted burn for a specific session.
- huberman owns: sleep quality, recovery prescription, HRV trend.

Decision rule:
- If you can answer with your own tools + state, do so.
- If the question needs another agent's specialized state, call delegate_to.
- If the question is genuinely cross-domain (e.g. "should I push hard
  today given my HRV?"), call delegate_to for the OTHER agent's view,
  then synthesize a unified answer in your own voice.
```

## Voice on delegated replies

Two patterns; each agent picks per-call:

* **Forward verbatim** with attribution: `"Bole to — Fraser says: <Fraser's card>"`. Used when the delegated answer is structured (a Workout Card, a timeline table).
* **Wrap in own voice** with framing: `"Hau bhai, here's the WOD: [Fraser's structured card]. Aaj phodne ka, this one needs energy."` Used when the delegated answer is short and benefits from contextualization.

The decision is the reasoner's. Don't prescribe via system prompt.

## What this prevents

* Kobe hallucinating a fake WOD when asked "what's my WOD" (the bug captured in screenshots 2026-05-16)
* Kobe inventing 1RMs / scale targets / movement substitutions (out-of-domain confabulation)
* Fraser narrating weight-loss timeline math (Kobe's job)
* Agents drifting into each other's territory over time as their system prompts grow

## Implementation

1. **`core/delegation.py`** (new module): the `delegate_to()` tool function. Wraps Miya's classifier + dispatch, enforces depth cap, writes ledger spans.
2. **Each agent's tool catalog** (`agents/X/tools.py` or equivalent): expose `delegate_to` as a tool with manifest.
3. **Each agent's system prompt** (`agents/X/coach_system.py` or equivalent): add the DELEGATION POLICY section above.
4. **Tests** (`tests/test_delegation.py`):
   * Kobe asked "what's my WOD" delegates to Fraser; Fraser's card surfaces.
   * Loop detection: Kobe → Fraser → Kobe returns `delegation_loop` error.
   * Depth cap: depth=3 attempt rejected.
   * Ledger span recorded with correct actor + input + output.
   * Cross-domain "should I push hard given my HRV?" delegates to Huberman, synthesizes answer.

## Non-goals

* Auto-routing replies to a specific user channel. Miya stays the single Telegram-facing surface.
* Replacing Miya's classifier (ADR-006). Delegation is the reasoner's escape hatch for in-conversation domain shifts; classifier is the upfront routing decision.
* Streaming partial delegated replies. The first version is request/response. Streaming is a follow-on.

## Rollback

Set `RAHAT_DELEGATION_ENABLED=0` to disable. The `delegate_to` tool returns `{"error": "delegation_disabled", "fallback_reply": "I can't ask another agent right now — try /pace, /plan, or rephrase."}`. Agents fall back to synthesizing their best in-domain answer.
