# ADR-001: Rahat Control Plane — Now / Next / Later

**Status:** Proposed (awaiting owner approval)
**Date:** 2026-05-05 (rev 2 — Charter rename, timeline corrected, episodic promoted)
**Reviewer hat:** L8 Principal Engineer / Agent Architect
**Deciders:** Alex (owner). All implementation work pauses until this ADR is Accepted.

---

## 1. Context

Rahat today is one production agent (Kobe), one bridge (SugarWOD), one ambient ingest (Apple Watch → `vitals_listener`), a SQLite ledger with the right shape, and an eval suite. The PRD describes a mesh; the runtime in code is a 2,400-LOC monolith doing routing, math, I/O, scheduling, and Telegram in one process.

The owner's corrected timeline:

- **Now (months 1–6):** scale the mesh from 1 → ~20 agents. Sports scientist class (coach, curriculum for toddler/newborn, appointments, HRV/sleep recommender, etc.). All deterministic or rule-based.
- **Next (months 6–12):** profile store, semantic search, replay/grading, smarter routing.
- **Later (12+ months):** mobile gateway + thin client, multi-tenant for partner/family, marketplace shape.

This ADR specifies the architecture for that trajectory. It is deliberately tight: most of "Now" is shared infrastructure that has to exist before agent #2 ships, because the alternative is rebuilding it after agent #5.

---

## 2. What's working today (do not regress)

| Working today | Why it's right |
|---|---|
| Local-first sovereignty | The product thesis. Keep. |
| Intent-ledger philosophy | Right primitive, under-implemented. |
| Deterministic core, LLM at edges | Right cost/reliability posture. |
| Heartbeat-driven (not prompt-driven) | The minute-tick + nudge throttling is the seed of a real scheduler. |
| Eval suite on isolated DB copy | Foundational. Generalize, don't replace. |
| Bridge pattern (typed Pydantic, archive raw + parsed, swappable source) | Best-shaped piece in the repo. Template for future bridges. |
| launchd + caffeinate + KeepAlive + ThrottleInterval | Production-grade daemonization. |

---

## 3. The three planes (plain English)

Same as a hospital: policies (control), patient records (data), the doctors actually working (runtime).

- **Control plane** — what's allowed, who does what, what tools each agent may use. The rule book.
- **Data plane** — what the system knows: vitals, plans, episodes, profile facts. The records.
- **Runtime plane** — what's happening right now: agents on a tick, tools being called, decisions being logged. The kitchen on a Friday night.

For Rahat, this means:

```
Control                       Data                          Runtime
- Agent registry              - Event-grade tables          - Miya (orchestrator)
- Tool helpers (core/io)      - Episodic memory             - Agent host (lifecycle)
- Charter (policy)            - Profile (Next)              - Scheduler (heartbeat)
- Identity / scopes           - Semantic / vss (Next)       - Decisions log (trace)
                              - Procedural library          - Eval harness
```

The same principle as today, just with explicit chokepoints so 20 agents share what they should share and nothing more.

**Naming.** The policy engine is **The Charter** — the written rules, applied uniformly. Huberman remains an *agent* (the HRV / sleep recommender). Charter is a *plane*: it isn't a persona, it's the gate every work-order passes through before execution.

---

## 4. Now — months 1–6, scaling to ~20 agents

The forcing function is agent count. Anything you have to do 20 times has to be cheap; anything that's a chokepoint has to exist; ambient features (profile, mobile) wait their turn.

Total Now budget: **~12–14 focused hours**, ideally 2–3 sittings. After that, each new agent is ~3–5 hours.

| # | Piece | Path | Effort | Why it earns its place in Now |
|---|---|---|---|---|
| 1 | **Tool helpers** | `core/io.py` | 30 min | Pull `send()`, Gemini client, `_db()` out of `main.py`. 20 agents share these; without extraction they'll be re-imported and re-initialized 20×. |
| 2 | **Agent contract** | `core/agent.py` | 30 min | 30-line base class: `name`, `triggers`, `route(msg) → Reply\|None`, `tick(now)`. Every agent subclasses this. No manifest TOML — Python is enough at this scale. |
| 3 | **Miya — orchestrator + hybrid router** | `core/miya.py` | 3 hr | Owns the Telegram poll loop. Routing: walk all agents' `triggers` (cheap regex first); if zero or multiple fire, Gemini Flash classifies (~$0.0001/msg). Synthesizes when >1 agent replies. Without Miya you ship 20 Telegram bots — exactly what Rahat rejects. |
| 4 | **Charter — policy chokepoint** | `core/charter.py` | 2 hr | Registry of Python predicates: `@policy("kind=coach.push_intensity")`. Every work-order passes through `charter.review(wo)` → `approved \| modified \| vetoed` with reason. Writes to existing `governance_log` table — *something actually writes to it now*. Huberman-the-agent advises; Charter-the-plane enforces. |
| 5 | **Decisions / trace log** | `core/decisions.py` | 1 hr | One table: `decisions(trace_id, ts, actor, op, latency_ms, tokens, cost_usd, outcome)`. With 20 agents this is the difference between "I can debug Tuesday 9pm" and "no idea." Enables `rahat tail` and `rahat replay <trace>`. |
| 6 | **Eval framework** | `core/eval.py` | 2 hr | Generalize Kobe's pattern: each agent ships `evals/cases.yaml`, one runner, isolated DB copy, CI gates regressions. Mandatory before agent #5 — without it, new agents silently break old ones. |
| 7 | **Episodic memory (lite)** | `core/episodes.py` | 1.5 hr | `episodes(id, kind, subject, started_at, ended_at, entities_json)` + `episode_notes`. 6-line Python API: `open()`, `close()`, `note()`. Skip the CLI for now. The Curriculum agent (toddler phases), Coach (training blocks), and Kobe (weight cycles) all consume it. Without it, each builds its own `phases` table — fragmentation by agent #5. |
| 8 | **Kobe refactor to the new contract** | `agents/the_scientist/{handler,protocols}.py` | 2 hr | Mostly mechanical: split `main.py` into `protocols.py` (~600 LOC pure math) and `handler.py` (~1500 LOC route + handlers). Eval suite must pass unchanged — visible-no-op. Without this, every Now piece forks: the new architecture vs. the old monolith. |

**Explicitly NOT in Now:** profile store, semantic/vss, mobile gateway, manifest TOML files, event-log refactor under the projections, skill rate-limits, multi-tenant `subject_id` columns (see §6.3 for the one possible exception).

**Discipline that holds the design together (just three rules):**

1. Telegram only goes through Miya.
2. All outbound I/O goes through `core/io.py`. (Tool Broker in spirit, just one file.)
3. Every agent is a class implementing the same three methods.

Three rules, ~13 hours, then 20 agents at 3–5 hours each fits cleanly in the 6-month budget.

---

## 5. Next — months 6–12

Each item below is added *only when its triggering use case lands*. None blocks the Now.

| Piece | Effort | Promote to Now when… |
|---|---|---|
| **Profile store** — `profile_facts(subject, key, value, confidence, source, valid_from, valid_to)` + Python API | 3–4 hr | The first agent that needs durable beliefs across sessions ships (likely Foodie or curriculum-class). |
| **Semantic memory — sqlite-vss** wrapper + ingest | ½ day | The first agent that needs free-text retrieval over a saved corpus is in active build. (Don't pre-ingest.) |
| **Event log + projection rebuild** under the existing tables | ½ day | You want to replay the last 7 days against a new Miya routing strategy — i.e., when grading routing decisions becomes a real ask. |
| **Embedding-based agent retrieval** (replaces Flash classifier in Miya) | ½ day | The classifier starts misrouting because >15 agents have overlapping descriptions. |
| **Cost & latency dashboard** (daily Telegram digest off `decisions`) | 1 hr | First time monthly LLM cost surprises you. |
| **CLI surface** — `rahat status / agents / tail / replay / eval` | 2–3 hr | When you find yourself sshing into the Mac Mini to grep logs. |

---

## 6. Later — 12+ months

| Piece | Effort | Trigger |
|---|---|---|
| **FastAPI gateway** — read-only projections over Tailscale | 1 day | Mobile horizon enters the next 90 days. |
| **Thin mobile client** — SwiftUI/RN, Sign-in-with-Apple, APNs, offline read cache | 1–2 weeks | The gateway has been live and stable for ~30 days. |
| **Skill manifests + true registry** (TOML + scope checks) | 2–3 days | Letting external/third-party agents plug in (marketplace ambition). For your own 20 agents, in-process Python imports stay simpler. |
| **Multi-tenant — `subject_id` everywhere, scope-checked in Charter** | 1–2 days | Partner/toddler get their own subjects. |
| **Encrypted off-machine ledger mirror** to a tiny VPS | 1 day | First "Mac Mini died" near-miss. Resilience without sacrificing sovereignty. |

### 6.3 The one Now-vs-Later judgment call

If there's any chance the partner or toddler become subjects of agents within ~12 months, add `subject_id` columns to *new* projections in Now (cost: 5 minutes per table). Cheapest insurance in the document; the alternative is a migration nightmare across 20 agents.

---

## 7. Decision rules (so you can re-apply this without me)

For any future "should this be Now or Next?":

1. **Does at least one Now-window agent actually consume it?** If no → defer.
2. **If you don't build it, will multiple agents reinvent it?** If yes → pull forward.
3. **Is the surface contract clear and the cost <1 day?** If no → defer regardless.

Applied: episodic clears all three (consumer = Curriculum, dup-tax = high, cost = 1.5 hr), so it's in Now. Semantic clears only #3, so it stays in Next. Profile clears none in the Now window per the corrected timeline.

---

## 8. Trade-offs

The single hard one: **build the Now scaffolding before agent #2, or after.**

- **Before** (recommended): ~13 hours up front, then 20 agents at 3–5 hours each = ~80 hours of agent-building. Mobile in Later is additive, not a rewrite.
- **After**: agent #2 ships in 4 hours, agent #3 hurts, by #5 you're rebuilding from scratch with twice the code to migrate. Eval discipline erodes. Charter never lands.

The PRD already names ~6 agents and the corrected timeline names ~20. Discovering their common abstraction by hand-rolling four monoliths first is the most expensive way to find an abstraction we can already see today.

---

## 9. Consequences

**Becomes easier:** adding the next agent (manifest-free class + triggers + cases.yaml). Auditing what an agent did. Replaying a turn. Generalizing the eval gate. Mobile in Later (swap one tool function, not 20). Letting external agents plug in via the Agent contract.

**Becomes harder:** one-shot scripts gain a tiny ceremony (subclass `Agent`). Mitigation: a 50-LOC template + a `make agent name=foo` script.

**Will need to be revisited:** semantic store choice (sqlite-vss vs. lancedb) at ~50k items. Routing strategy (Flash vs. embeddings) at ~15 agents. The cloud question (encrypted mirror) after the first hardware scare.

---

## 10. Approval gate

This ADR proposes the Now-phase scaffolding *before* agent #2. Per owner instruction, **no code changes will be made until this ADR is marked Accepted.** Specifically:

- Approve **the three-plane direction** (Control / Data / Runtime).
- Approve **the Now list (§4) — 8 items, ~13 hours**.
- Approve **the Charter naming** for the policy plane.
- Approve **promoting episodic memory to Now**.
- Confirm **no Next/Later items move forward** without a re-trigger conversation.

Open questions before Phase Now begins:

1. **Kobe refactor** — confirm a one-sitting visible-no-op refactor is OK. (Recommended; without it every Now piece forks.)
2. **`subject_id` insurance** — yes/no on the 5-minutes-per-table multi-tenant insurance in §6.3?
3. **Telegram cutover** — Miya owns the inbox the moment it ships. Confirm the user-visible bot handle stays the same (we just swap what's behind it).
4. **Eval gate strictness** — fail the build on any regression, or warn-only for the first month?
5. **Charter starting policies** — port the existing `check_external_veto` first, or start clean? (Recommended: port + extend.)

— End of ADR-001 (rev 2) —
