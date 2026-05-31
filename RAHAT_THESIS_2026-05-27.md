# Rahat — Thesis, Platform Choice, Build Approach

**2026-05-27.** Not a 10-page plan. The thesis, the framework call, and the
parallel-planes build path you proposed — evaluated honestly.

---

## The thesis (one paragraph)

Rahat is a **personal AI agent platform you actually talk to** — *one Miya, not a
fleet of bots* — that's **measurably cheaper without losing outcomes**, remembers
**what mattered** (not just what was recent), **runs on your infrastructure**
with your data and your model choices, and produces a **receipt for every
action**. Hard capabilities under the hood; everyday product on top.

---

## Three hard capabilities (the moat — not weekend-buildable)

1. **Outcome-validated cost-quality routing.** A learned router that proves, on
   your own traces, that a cheaper model produces *outcome-equivalent* answers
   for these intents, this user, this context — and re-validates against drift.
   Output is a number you can sell: *"3× cheaper, outcomes unchanged, proven on
   your last 90 days."* Eats the cost-spike problem you flagged.
2. **Arbitrating orchestration (one Miya, real).** Not a switchboard. Mediates
   conflicts between specialists (Fraser says train, Huberman says rest), holds
   shared state across them, synthesizes when no single specialist suffices,
   keeps *one coherent voice* the user is talking to. The executive function
   over a team.
3. **Outcome-conditioned memory with audit provenance.** What's retained and
   recalled is governed by *causal contribution to outcomes*, not recency or
   embedding similarity. Every fact carries source, freshness, and the user's
   revocation right.

Each is a *combination* of capabilities (outcome capture, replay/counterfactual,
arbitration semantics, attribution). A dev-rel engineer ships the surface of any
one in a weekend; nobody ships the integrated loop in under a year.

## Trust foundation (table stakes, not pitch)

**Sovereignty** (runs on your machine, model-provider-abstract, portable
identity). **Verifiable compliance receipts** (proof of policy adherence per
action — EU AI Act + OWASP Agentic Top 10 + Microsoft's MIT-licensed Governance
Toolkit pull this into the market inside 12 months). **Multi-source provenance**
(every fact tagged with source/freshness/revocation).

## SRE for agents (substrate, not headline)

Replay, counterfactual, error budgets, automatic failure→regression. Powers all
three hard capabilities. Lives under the hood. Engineering discipline, not the
marketing.

## What Rahat is explicitly NOT

Not yet another agent framework. Not the horizontal enterprise control plane
(don't fight Google/AWS/Microsoft/Kore.ai for the IT-fleet buyer). Not a memory
product (don't fight Letta/Mem0 on memory alone). Not a fitness app — fitness is
the vertical wedge, not the product.

---

## Framework choice

Evaluated against the thesis (orchestration, cost surface, memory, sovereignty,
ecosystem, language fit):

| Framework | Strength | Weakness for this thesis |
|---|---|---|
| **OpenClaw** (TS) | Mature external open-source runtime evaluated as foundation layer. Multi-channel gateway, plugin SDK, self-hostable. *Note: earlier drafts of this doc claimed Venkat owns/built OpenClaw — that was a fabrication, corrected 2026-05-30. Rahat is built on top of OpenClaw as one candidate runtime; OpenClaw is someone else's project.* | Default autonomy loop fights the deterministic-budget thesis; must constrain. |
| **LangGraph** (Python) | Best graph-based orchestration substrate; auditable state; production-mature. | No ecosystem advantage for you; observability stack-coupled (LangSmith). |
| **Letta / MemGPT** (Python) | Best memory thesis fit (Core / Recall / Archival, self-editing). | Single-agent focus; not an orchestration platform. |
| **Mastra** (TS) | Newer, TS, growing momentum, mem0-integrated. | Small ecosystem; sovereignty story unproven. |
| **Hermes Agent** | Sovereignty-led, self-hosted, 6 channels. | Memory is recency-based, "self-improving" unproven; competes with OpenClaw on the same axis. |
| **Google ADK / AWS AgentCore** | Mature orchestration. | Sovereignty story is bad; you don't own the position. |

**Recommendation: OpenClaw, with the autonomy loop deliberately constrained.**
*(Earlier framing of "you own it" was a fabrication — see note above. The real
rationale is engineering: OpenClaw is the most mature multi-channel gateway in
the candidate set, and the three hard capabilities can be built on top of it as
extensions/plugins rather than reinvented.)* OpenClaw provides the gateway,
channels, and ACP trace; the differentiation layer is what Rahat adds on top.

**Honest fallback:** if OpenClaw's philosophy mismatch proves too hostile in the
spike, the Python alternative is **LangGraph + Letta + a custom cost layer.**
You'd lose the ecosystem advantage but the engineering matches the thesis more
naturally and you reuse Python code. Try OpenClaw first; have this in pocket.

---

## Build approach — parallel planes, strangler fig

**Your instinct is right.** Don't migrate Rahat. Build the next thing on the new
plane, prove it, then bring the old agents over.

- **Old plane (Python Rahat)** keeps running untouched. Kobe, Fraser, Huberman,
  old Miya — production stays green.
- **New plane (OpenClaw + thesis layers)** is built in TS alongside.
- **Wedge: new Miya on the new plane.** This is the right choice over a "new
  random agent" because Miya is the *orchestrator* — testing her exercises
  every differentiator at once (cost router, arbitration, memory).
- **New Miya talks to old Kobe/Fraser as black-box HTTP APIs.** No port-to-TS
  up front. The interface between planes is the contract that, *if you migrate
  later, becomes the port surface.*

**Why this is the right shape:** zero risk to working production. Tests the
platform on the agent that exercises the most differentiators. Reversible at
every step. The cross-plane API forces clean contracts.

**Why it can fail (and how to prevent it):**
- *Two planes running indefinitely* (you stop bothering to migrate Kobe/Fraser
  because new Miya alone is enough). → Stop-or-go gate, below.
- *Ops double-tax on one person.* → Aggressively narrow new Miya's surface
  before you write the first commit.
- *Interface ossifies into a permanent fork.* → Treat the cross-plane API as
  intentional architecture, not technical debt. Versioned, audited.

---

## Stop-or-go gate (set in advance, or you'll never decide)

**After 8 weeks of new Miya in production with you as the only user**, evaluate:
1. **Cost router:** real $ savings vs old plane on the same conversations, with
   outcome quality unchanged? (Measurable.)
2. **Arbitrating orchestration:** does talking to new Miya feel materially
   different from old Miya — fewer "talk to Kobe" / "ask Fraser" handoffs,
   coherent synthesis across specialists? (Your judgment.)
3. **Outcome-conditioned memory:** does new Miya surface relevant context old
   Miya misses (the ankle from 3 weeks ago, the goal trajectory drift)?
   (Reflection eval over 20 real conversations.)

**2 of 3 yes → port Kobe/Fraser next; retire old plane within 12 more weeks.**
**0–1 yes → kill the new plane, write up what you learned, return to the old
stack with the lessons. No sunk-cost migration.**

---

## What "this week" actually looks like (concrete first moves)

1. **Define new Miya's wedge.** What conversation does she own first? My
   nominee: the morning brief + evening review + on-demand synthesis ("how
   should today look given my goal + plan + HRV + schedule"). Exercises all
   three hard capabilities; user-visible value Day 1.
2. **Stand up OpenClaw locally on Gemini.** Verify the spike fundamentals: can
   you boot it, define an agent, call a tool, observe traces.
3. **Wrap Kobe / Fraser / Huberman as HTTP tools** the OpenClaw plugin can
   call. The Python plane exposes 5–10 endpoints — no logic change.
4. **Smallest version of each hard capability:** hard-coded 2-tier cost router
   (Flash vs Pro by intent), a Letta-pattern two-tier memory plugin
   (core/archival), arbitration as a synthesis prompt over multi-specialist
   responses. Each is intentionally crude in v0; the bones matter, not the
   polish.
5. **Route ONE conversation through new Miya.** Side-by-side with old Miya for
   a week. Capture both traces. That's the first evidence.

---

## Risks I'll call out honestly

- **OpenClaw's autonomy loop fights the deterministic budget.** Resolvable
  (constrain via plugin), but not free engineering.
- **Anthropic just shipped competing primitives** (per recent news: rubric-driven
  "Outcomes," scheduled "Dreaming" memory review, lead-agent / sub-agent
  orchestration with shared filesystem and auditable trace). They're moving
  *into your space*, branded. Your edge: vertical traction + sovereignty +
  OpenClaw ecosystem position — none of which they have.
- **You're one person.** Two planes + three hard capabilities + new-Miya-in-TS
  is a lot. Be ruthless about wedge scope: most "must-haves" are actually
  "after the wedge ships."
- **Even with all three hard capabilities + OpenClaw ownership + vertical
  data flywheel, you don't have a fortress.** You have a 2–3 year defensible
  lead in an open-source market. In this market, that's forever — but it's not
  forever-forever. Don't believe your own marketing about moats; execute.

---

## Three decisions I need from you to greenlight Stage 0

1. **Parallel planes (new Miya on new plane, old plane untouched, 8-week
   stop-or-go gate)** — yes or pick a different shape.
2. **OpenClaw as the runtime** — yes, or fall back to LangGraph + Letta + custom.
3. **New Miya's wedge** — morning brief + evening review + synthesis is my
   nominee. Confirm or substitute.

If yes on all three, Stage 0 is concrete first commits in the new plane against
the OpenClaw plugin SDK. I won't touch the old Rahat stack until the gate
clears.

---

## Sources (cutting-edge signal grounding this)

- [Letta — stateful agents with three-tier memory (UC Berkeley Sky)](https://www.letta.com/)
- [MemGPT is now part of Letta](https://www.letta.com/blog/memgpt-and-letta)
- [Hermes Agent — sovereign, self-hosted, three-layer memory](https://hermes-agent.org/)
- [LangGraph vs CrewAI vs AutoGen 2026 — observability + governance gaps](https://www.knowlee.ai/blog/agentic-ai-frameworks-comparison-2026)
- [State of AI Agent Memory 2026 (mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Agentic AI 2026: Autonomous Orchestration & Sovereign Stacks](https://vucense.com/ai-intelligence/agentic-ai/agentic-ai-2026-autonomous-orchestration-sovereignty/)
- [Self-Hosted AI Agent Platforms 2026 — CISO buyer guide](https://www.knowlee.ai/blog/self-hosted-ai-agent-platforms-2026)
- [Bain — Google Cloud Next 2026: the agentic enterprise control plane](https://www.bain.com/insights/google_cloud_next_2026_the_agentic_enterprise_control_plane_comes_into_view/)
- [Forrester — Agent Control Planes Still Need a Robust Standards Stack](https://www.forrester.com/blogs/agent-control-planes-still-need-a-robust-standards-stack/)
- Local: `staging/fleet/` (vendored OpenClaw clone), `specs/MODEL-FIRST-PIVOT.md`
