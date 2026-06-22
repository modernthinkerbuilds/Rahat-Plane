# ADR-008 — Clarification policy (probabilistic uncertainty)

**Date:** 2026-05-16  
**Status:** Accepted (Chief Architect, owner-approved 2026-05-16 evening)  
**Context:** The 2026-05-16 screenshots showed the bot producing
confidently-wrong answers when intent was ambiguous. Owner: "I want
the probabilistic scenarios to be addressed." Pair to ADR-006
(capability router) and ADR-007 (cross-agent delegation). Together
they replace "always answer confidently" with "calibrate confidence,
defer when uncertain, ask when stuck."

## Decision

When Miya's router (ADR-006) produces low confidence across all
candidates, Miya **asks the user a short clarification question**
instead of dispatching to any agent. When confidence is medium, Miya
**surfaces the uncertainty in the reply** rather than presenting it as
certain. The user's response is treated as a routing signal for the
follow-up turn.

## Confidence policy (canonical thresholds)

These are the policy defaults, owned by Chief Architect. Each is
env-overridable for incident debugging.

| Top classifier score | Behavior | Env var override |
|---|---|---|
| ≥ 0.7 | Single-agent dispatch. No caveat in reply. | `RAHAT_ROUTER_HIGH_CONF` |
| 0.5–0.7, single | Dispatch single agent, prepend caveat: "Treating this as a <domain> question — say more if I got that wrong." | `RAHAT_ROUTER_MED_CONF` |
| Top-2 within 0.2, both ≥ 0.5 | Dispatch both, merge replies via Miya's voice. | `RAHAT_ROUTER_AMBIG_THRESHOLD` |
| Top < 0.5 OR all candidates < 0.4 | **Clarification question** — do NOT dispatch. | `RAHAT_ROUTER_LOW_CONF_FLOOR` |
| All < 0.2 (pure noise) | Helpful generic: list `/help` shortcuts. | `RAHAT_ROUTER_NOISE_FLOOR` |

## Clarification question template

```
I want to make sure I route this to the right specialist before
answering. Are you asking about:

A) <top-1 agent's domain, one-line summary>
B) <top-2 agent's domain, one-line summary>
C) Something else — rephrase?

(Reply A, B, C, or just say it differently and I'll re-route.)
```

The agent-domain summaries come from each agent's `description` field.
**Do not hand-write the summary per message.** Generated on the fly
from the classifier's top candidates.

## State for follow-up turns

Miya stores the last clarification context per chat in
`memory_entities[type='miya_clarification', agent='miya']` with a
60-second TTL. The next user message in that chat is dispatched with
the clarification answer factored in:

* User replies "A" → dispatch to top-1 agent
* User replies "B" → dispatch to top-2
* User replies "C" → re-run classifier on the new message (no use of
  the old context)
* User replies with a rephrase that classifier now scores high → dispatch normally; the 60-second clarification entity is superseded
* No reply within 60 seconds → entity expires; next message is a clean classification

The TTL is short on purpose — clarifications should resolve within the
same conversational beat, not days later.

## When NOT to ask

Three guardrails to prevent the bot from becoming an annoying
clarification machine:

1. **Slash commands skip clarification entirely.** `/pace` is the
   user's explicit deterministic intent. No router involvement.
2. **Nudges (tick-driven, low priority) skip clarification.** Morning
   briefing, pace nudges, recovery checks — these are agent-initiated,
   the agent knows its own intent.
3. **Repeated low-confidence in the same chat within 5 min** falls
   through to: "I keep struggling to route your last few messages.
   Send `/help` for the slash commands, or be more specific about
   whether you mean workout-prescription (Fraser) or weekly-pace
   (Kobe)." Three clarification questions in a row is the bot's fault,
   not the user's.

## Why this matters

The probabilistic-handling shift is the architectural maturation that
moves the system from "chatbot that always answers" to "agent mesh
that knows what it doesn't know." Three concrete wins:

* **Honest failure mode.** Bot says "I'm not sure if you mean X or Y"
  instead of confidently picking the wrong one. Users trust answers
  they DO get more, because the bot has earned the right to be
  confident when it is.
* **Self-tuning agent descriptions.** Every clarification logged is a
  signal that some pair of agent descriptions overlap or are too
  vague. Weekly review: how often is the bot asking to clarify
  Kobe-vs-Fraser? If high, sharpen the descriptions. The
  decisions-ledger spans from ADR-007 + this ADR make this answerable.
* **Composable for the 20-agent mesh.** Adding Huberman, Foodie,
  Bourdain, Ramsay etc. doesn't degrade routing accuracy because the
  classifier rebalances and clarification absorbs residual ambiguity.

## Implementation

1. **`core/miya.py::route()`** consumes classifier output, applies the
   confidence policy. Below floor → returns a clarification reply
   instead of dispatching.
2. **`core/miya.py::ask_clarification(candidates, msg)`** — builds the
   A/B/C question from agent descriptions.
3. **`core/miya.py::resolve_clarification(prev_context, user_reply)`** —
   when the next message arrives and a clarification entity exists,
   apply the user's choice.
4. **New tests** (`tests/test_clarification.py`):
   * Low-confidence message → clarification reply, no agent dispatched
   * User replies "A" → top-1 agent dispatched with original message
   * User replies "C" → classifier re-run, no agent locked to old context
   * 60-second TTL: stale clarification context ignored
   * Slash commands bypass clarification entirely
   * Three consecutive low-conf messages → fall-through helpful message

## What stays the same

* Slash commands — direct deterministic dispatch.
* Nudge generation — agent-initiated, bypasses classifier.
* `decisions` ledger actor strings.
* Charter policies.
* Substrate schema.

## Rollback

Set `RAHAT_CLARIFICATION_ENABLED=0` to disable. Router falls back to
ADR-006 behavior of "dispatch top-1 regardless of confidence" — the
pre-clarification policy. Bot becomes confident again (potentially
confidently-wrong), but functional.
