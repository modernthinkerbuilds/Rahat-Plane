# ADR-015 — Outcome-validated learning engine: one contextual-bandit, cost routing first

**Status:** Proposed 2026-06-14 (design-only — no live learner code ships with this ADR)
**Decided by:** Owner + Chief Architect (solo org)
**Pins:** `specs/RAHAT_PM_THESIS_2026-05-27.md` §1 (moat), §3a (engine), §6 (fragility), §8a (one bandit, five routing endpoints), §8b (separate trajectory learner)
**Relationship to prior ADRs:**
- ADR-005 (budget enforcement) and ADR-006 (capability-based router) define the cost/routing surface this learner eventually optimizes; this ADR does not change either — the bandit sits behind the existing `cost_router.decide()` contract.
- ADR-013 made `new_plane/` the active runtime; the learner lives there (`new_plane/learn/`).
- ADR-014 pinned OpenClaw as supporting, not structural. The learner is runtime-agnostic Python with no OpenClaw coupling, consistent with that pin.

---

## Context

The PM thesis (§3a) names the **outcome-validated learning engine** as the engineering core of the moat: `outcome capture + replay/counterfactual over historical traces + learned policy + drift monitor`. Six application surfaces are listed. §8a is explicit that five of them — **cost routing, memory ranking, prompt-variant routing, personalization, nudge timing** — share *one* contextual-bandit over a discrete action space with near-term outcome signal. §8b is equally explicit that conflict-policy learning is a *different* learner (trajectory-level offline-policy learning, sparse delayed signal) on the same substrate.

Two failure modes the thesis warns against, which this ADR exists to prevent:

1. **Shipping five engines that look similar** (§8a). Each surface is a routing decision: features in, discrete action out, near-term outcome observed. They are one learner with five wiring points, not five learners.
2. **Treating the trajectory learner as a sixth routing endpoint** (§8b) — "will fail quietly."

The training data already exists. `core/decisions.py` logs every action with `cost_usd`, `tokens_in/out`, `latency_ms`, and `outcome` per `trace_id`. `new_plane/miya_runner/cost_router.py` logs every routing decision to the `OPENCLAW_COST_LOG` JSONL with `model`, `reason`, `user_message_len`, `arbitration_rule`, `matched_patterns`, `trace_id`, `ts`. `new_plane/signals/store.py` captures outcome signals with a consumption contract. We have the substrate; we do not yet have the learner. The current router is a hard-coded heuristic whose own docstring marks where the learner goes.

This ADR records the decision **not to relitigate the §8a/§8b sequence** and to build it in the order the thesis specifies.

## Decision

**Build the §8a contextual-bandit first, as the trophy demo, on the cost-routing surface (Flash vs Pro), using the existing cost-log and decisions data. §8b (the trajectory/offline-policy learner) is a separate, later loop and is explicitly out of scope for this ADR.**

Specifically:

1. **One learner, five endpoints — cost routing is endpoint #1.** We define a single `Learner` Protocol and a single bandit implementation. Cost routing is the first surface wired to it because it has the cleanest, fastest-validating outcome signal (§6: "Cost says 'did Flash match Pro' — small claim, easy to validate"). Memory ranking, prompt-variant routing, personalization, and nudge timing are later wiring points behind the *same* learner — not new engines.
2. **The trajectory learner (§8b) is not built here.** Conflict-policy learning gets its own ADR when enough multi-day trace accumulates. It shares this substrate (outcome capture, replay, drift monitor) but is a distinct loop with distinct stability concerns.
3. **The cost-routing learner is the trophy demo for the moat claim** — "Flash matches Pro on stable mornings; route Flash → 3× cheaper, outcomes proven" (§3a). It is the public proof that the engine is real, validated on the hardest single-user case before it graduates.

This is a design pin plus a phased build plan. The only code that may land alongside this ADR is an **additive, default-off, behavior-neutral stub** (see §Stub, below). No live behavior changes until the phased plan's flag flips.

## 1. Learner Protocol

One Protocol, used by all five §8a surfaces. The contract is deliberately minimal: `decide()` maps a context to a discrete action; `observe()` folds a validated outcome back in.

```python
# new_plane/learn/bandit.py  (design sketch — see stub for the inert version)

from typing import Protocol, Sequence, Any

class Learner(Protocol):
    """One contextual bandit, shared across the five §8a routing surfaces.

    A surface is identified by `endpoint` (e.g. "cost_router"). Each
    endpoint declares its own discrete `actions` and supplies a context
    feature dict. The SAME learner serves all of them — we do not ship
    five engines (PM thesis §8a).
    """

    def decide(self, *, endpoint: str, context: dict[str, Any],
               actions: Sequence[str], trace_id: str) -> str:
        """Return one action from `actions` for this context.
        Must be deterministic given (model state, context, trace_id) so
        the choice is by_trace-replayable."""
        ...

    def observe(self, *, endpoint: str, trace_id: str, action: str,
                reward: float, context: dict[str, Any]) -> None:
        """Fold a VALIDATED outcome (reward) for a prior `decide` back
        into the policy. `reward` is computed by the endpoint's outcome
        validator (§3), not raw-logged."""
        ...
```

**Why this shape:**
- `endpoint` is the multiplexer that lets one learner serve five surfaces. Cost routing passes `endpoint="cost_router"`, `actions=[MODEL_FLASH, MODEL_PRO]`. Memory ranking later passes `endpoint="memory_rank"` with its own action set. Same object, same Protocol.
- `decide` returns a member of `actions` — for cost routing that is exactly the string the heuristic returns today, so it drops into `RoutingDecision.model` with no contract change.
- `observe` takes a **reward**, not a raw log row. The reward is the *validated* outcome (§3) — this is the load-bearing distinction between "logged" and "validated."
- Determinism requirement on `decide` is what makes the choice `by_trace()`-replayable (§5).

The contextual-bandit family fits the constraints: discrete action space, near-term reward, small sample sizes (one user). A LinUCB / Thompson-sampling-over-features bandit with conservative exploration is the intended first implementation; the Protocol does not commit to one — it commits to the interface so the implementation can be swapped under replay without changing callers.

## 2. Training data — what feeds the bandit and what "outcome" means

### Context features (the `X`)
Drawn from data already captured at decision time, so the bandit can be trained offline on history and served online with the same feature extractor:

| Feature | Source | Notes |
|---|---|---|
| `user_message_len` | cost-log JSONL / live arg | already logged |
| `arbitration_rule` (categorical) | cost-log JSONL / live arg | `behind_pace`, `goal_close`, none |
| `matched_patterns` (multi-hot) | cost-log JSONL / heuristic | the heuristic's own signals become features |
| hour-of-day / day-of-week | `decisions.ts` | routine-sensitive (nudge timing reuses this later) |
| recent-trace token volume | `decisions.tokens_in/out` by `trace_id` | conversation heaviness |
| prior-turn outcome in trace | `decisions.outcome` via `by_trace()` | did the last turn go well |

### Action (the arm)
For cost routing: `{MODEL_FLASH, MODEL_PRO}` — exactly the heuristic's choice space, so offline replay against the historical cost-log is apples-to-apples.

### Outcome — and how it is VALIDATED (not merely logged)
This is the crux. §6 of the thesis: the cost claim is the *easy* one to validate precisely because it is a counterfactual-quality claim, not a satisfaction claim.

The reward for a cost-routing decision is **"did the cheaper action match the expensive action's quality at lower cost?"** It is computed by an **outcome validator**, not pulled raw:

1. **Cost component** — directly measured. `cost_usd` and `tokens_in/out` from `decisions.py` for the chosen model on that turn. Flash that matched Pro at 3× lower cost is the win condition.
2. **Quality component — validated, not assumed.** A Flash response only earns reward if its quality is corroborated, via one or more of:
   - **Held-out gold replay (offline):** replay historical Pro turns with Flash; score the Flash answer against the logged Pro answer with the existing meta-eval / judge harness (the thesis' "meta-eval discipline calibrating the engine against held-out gold sets," §6). Reward = cost saved *iff* judged-equivalent.
   - **Corroborating outcome signals (online):** `new_plane/signals/store.py` outcome signals tied to the same `trace_id` — e.g. `user_thumbsup`, `plan_delivered` then `outcome_logged` (workout actually logged), absence of a follow-up correction/retry. **Multiple corroborating signals per decision, never single-signal** (§6 anti-Goodhart mitigation). A signal only counts toward reward when it was *consumed* (`mark_consumed`), matching the existing consumption contract.
   - **Negative validation:** a downstream retry, an error `outcome` on the next turn in `by_trace()`, or an explicit correction signal *subtracts* reward from a cheap choice.

"Validated" means: **a cheap action's reward is gated behind corroborating evidence that quality held, drawn from independent signals — not behind the mere fact that we logged a Flash call.** Logged ≠ validated. The validator is a pure function over (`decisions` rows for the trace, `signals` for the trace, optional judge score) → `reward`, which makes it deterministic and replayable.

## 3. Serving — heuristic stays the safe fallback

`cost_router.decide()` is the single serving entry point. The learner is introduced behind a flag with the heuristic as the always-available fallback:

```
decide(user_message, *, arbitration_rule=None, trace_id=""):
    if os.getenv("RAHAT_BANDIT") == "1" and learner is not None:
        try:
            ctx = extract_context(user_message, arbitration_rule, trace_id)
            model = learner.decide(endpoint="cost_router",
                                   context=ctx,
                                   actions=[MODEL_FLASH, MODEL_PRO],
                                   trace_id=trace_id)
            return RoutingDecision(model=model, reason="bandit",
                                   ..., trace_id=trace_id)
        except Exception:
            pass  # fall through to heuristic — never break the runner
    return _heuristic_decide(...)   # the current code, unchanged
```

- **Flag `RAHAT_BANDIT=1`** gates all learner serving. Default-off; absent the flag, behavior is byte-for-byte today's heuristic.
- **Heuristic is the safe fallback** on any learner error, missing model, or unloaded state — same best-effort discipline as `log_decision` (§ the runner must never crash on the learning path).
- **The `RoutingDecision` contract is unchanged.** The learner returns a model string; `reason="bandit"` records that the learner chose, so the cost-log distinguishes bandit vs heuristic vs fallback rows for analysis.

**Rollout staging (each gate must pass before the next):**
1. **Offline replay** — fit the bandit on historical cost-log + decisions; replay every historical turn; measure counterfactual cost saved at no quality loss against held-out gold. No live traffic. This is also the trophy-demo artifact.
2. **Shadow** — `decide()` computes the bandit choice *and* the heuristic choice, serves the heuristic, logs both. Measures live agreement and would-be savings with zero risk.
3. **Live** — `RAHAT_BANDIT=1` serves the bandit; heuristic remains the fallback. Reversible by clearing the flag.

## 4. Safety

- **Charter-governed.** The learner's choice is an input to the runtime, not an authority over it. Charter rules (ADR-005 budget enforcement, quiet hours, HRV-red gates) evaluate *after* routing as they do today — a cheaper model choice cannot bypass a charter veto. The bandit selects an arm; the charter still owns the chokepoint.
- **`by_trace()`-replayable.** `decide()` is deterministic given (model-state-snapshot, context, trace_id), and every serve logs `reason="bandit"`, the chosen model, and the context features into the cost-log keyed by `trace_id`. With the model-state snapshot pinned, any historical decision is reconstructable via `decisions.by_trace()` + the cost-log row. Counterfactual replay ("what would the bandit have chosen at threshold X on the last 90 days") is the §3b deterministic-replay primitive applied to the learner.
- **Reversible.** Three independent kill switches: clear `RAHAT_BANDIT` (instant revert to heuristic); the per-call try/except fallback (automatic revert on any error); and the staged rollout (never reach live without passing offline + shadow). No state migration is required to roll back — the heuristic code is never removed.
- **Anti-Goodhart, conservative-by-default** (§6): multi-signal validated reward (never single-signal), explicit drift monitor that auto-escalates to the heuristic when the live reward signal weakens or the bandit's shadow-agreement with validated outcomes degrades, and conservative exploration so a cold/low-confidence bandit defaults toward the safe (Pro) action rather than gambling quality on Flash.
- **Hermetic + live-DB-safe.** The learner reads the same DBs under `RAHAT_TEST_MODE=1` redirection; training/replay never touches `vault/rahat.db` (the 2026-05-08 guard).

## 5. Phased plan and the metric that proves it

| Phase | Deliverable | Gate / proof | Live behavior change |
|---|---|---|---|
| **0 (this ADR)** | Design pin + inert `Learner` Protocol stub + importability test | Stub imports, returns heuristic choice, test green | None (default-off) |
| **1 — Offline replay** | Feature extractor + outcome validator + bandit fit on historical cost-log/decisions; counterfactual replay harness | **Trophy metric** (below) computed on ≥90 days of history | None |
| **2 — Shadow** | `decide()` computes bandit + heuristic, serves heuristic, logs both | Live agreement rate + would-be savings logged ≥2 weeks; drift monitor wired | None (heuristic still served) |
| **3 — Live** | `RAHAT_BANDIT=1` serves bandit, heuristic fallback | Trophy metric holds live; reversible | Gated behind flag |
| **Later (separate ADR)** | §8a endpoints #2–5 (memory ranking, prompt-variant, personalization, nudge timing) reuse the *same* learner | each reuses the Protocol, not a new engine | per-endpoint flags |
| **Later (separate ADR)** | §8b trajectory/offline-policy learner for conflict policy | own loop, own data-maturity gate | out of scope here |

**The metric that proves it (the trophy):**
> **% of LLM spend saved by routing Flash instead of Pro, at no validated quality loss, measured by counterfactual replay over the historical trace set — and held live in shadow before going live.**

Concretely: of turns the heuristic sent to Pro, the fraction the bandit would send to Flash *and* where the outcome validator confirms quality held (gold-replay equivalence and/or corroborating consumed outcome signals, no downstream retry/correction). The win condition from §3a is "Flash → 3× cheaper, outcomes proven." The metric is exactly that claim, made falsifiable by replay. A negative or zero result is a valid outcome — it tells us the heuristic is already near-optimal and we keep it.

## Stub (additive, default-off, behavior-neutral)

Per this ADR, the only code landing now is `new_plane/learn/bandit.py`: the `Learner` Protocol plus a `NoOpLearner` that returns the heuristic's choice (Pro iff the heuristic would escalate, else Flash) and a no-op `observe`. It is **not wired into `cost_router.decide()`** — it changes no live behavior and exists only to pin the interface. A hermetic test asserts the interface is importable and inert.

## Consequences

**Positive:** the moat's engineering core gets a concrete, falsifiable first deliverable on the cleanest signal; one learner is built once and reused five times per §8a; the §8a/§8b split is pinned so the trajectory learner is not silently mis-built as a sixth endpoint; rollback is trivial and the heuristic is never lost.

**Negative / risks:** single-user sample sizes make any learned policy fragile (§6) — mitigated by conservative exploration, drift auto-escalation, and accepting "heuristic wins" as a valid result; outcome validation is engineering-heavy (the validator, gold-replay, multi-signal corroboration) — but it is the work that makes "outcomes proven" true rather than asserted, which is the entire point of the engine.

**Out of scope:** the §8b trajectory learner; endpoints #2–5; any RBAC/multi-tenant concerns (deferred per thesis §3b/§6).
