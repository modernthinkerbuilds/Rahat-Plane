# PRD: The Rahat Plane

**A Sovereign Habitat for Personal AI Agents**

v2 · Modern Builder · May 2026 · Living Document

---

## 1. The Vision

The **Post-Chat era** will not be won by smarter chatbots. It will be won by the system that *remembers*, *governs*, and *acts* on your behalf in the background — without prompting, without leaking, without forgetting.

**Rahat** (Urdu: *رہات* — *relief, ease, the lifting of a burden*) is that system. Not an app. Not a workflow. A **Sovereign Habitat** — the complete environment where personal agents live, remember, decide, and act. Twenty specialists. One memory. One voice. One chokepoint. Built on a Mac Mini you own.

The thesis is short. As inference trends toward zero marginal cost, the binding constraint on personal AI shifts from reasoning to **memory, governance, and ambient access**. Whoever owns the substrate — the place agents live and coordinate — owns the relationship with the user. Rahat is a bet that this substrate must be *local, intimate, and sovereign*. The cloud cannot meet you in your kitchen at 6:47am with a toddler on your hip and a 38-hour fast in your bloodstream. The habitat can.

We are building Rahat to systematically eliminate decision fatigue across **health, family, performance, and ritual** — and to do it without surrendering biometric data, family logistics, or the operating system of one's life to a third party. The goal is *Rahat* in the original sense: relief.

---

## 2. Key Principles

Five non-negotiables. Every roadmap call, every architectural fork, every agent onboarding is adjudicated against these.

1. **Sovereignty as Infrastructure.** All state — vitals, commitments, archival memory — lives in `vault/rahat.db` on local silicon. No cloud sync of biometric data. Trust is silicon-deep, not policy-deep.
2. **Shared Memory, Not Shared Chat.** RAG-over-conversation-logs is brittle and leaky. Rahat agents read and write a structured substrate with six first-class primitives. When the Kobe commits a goal, the Foodie sees it on the next turn — without re-reading a transcript.
3. **One Voice, Many Minds.** Miya owns the inbox. Agents never speak directly to the user. This is the difference between a team and a roomful of bots.
4. **Single Chokepoint Governance.** Every write tool across every agent passes through the Charter. Quiet hours, HRV-red blocks, family-priority overrides — written once, applied uniformly, audited to `governance_log`.
5. **Zero-Input Constraint.** If the user has to type their weight, log their food, or remind the system of yesterday, the feature has failed. Ingestion is ambient (Watch), vision-based (photo), or conversational — never form-based.

---

## 3. What Makes Rahat Special

> *Memory is the compounding asset. The agents are the interest payment.*

Three durable moats. They compound.

### The Memory Moat

Most personal AI tools forget you between sessions. Rahat doesn't. A four-tier substrate — **events, entities, preferences, archival** — sits beneath every agent. Sleep-time consolidation runs at 03:00 to summarize threads, decay un-reinforced preferences, and archive expired entities. The 11th agent onboards in ~1 day because the substrate is already universal; each adapter is ~120–280 LOC. The substrate compounds with every turn the user takes — and it is not portable to a competitor.

### The Governance Moat

The Charter is the single chokepoint. Every write tool — `commit_picks`, `log_weight`, `propose_replan` — calls `charter.check()` before executing. Policies are data, not code. Adding the 20th agent does not require re-litigating ethics or rate limits. As the mesh grows, the governance surface stays flat. That is rare.

### The Voice Moat

Miya wraps every outbound message through `core/voice.py` — a deterministic Hyderabadi Dakhini phrasebook. Idempotent. Numbers and structure preserved verbatim. Zero per-message LLM cost. The user has *one relationship*, not twenty. Personality is an architectural property, not an agent's responsibility.

### The Economics Back the Bet

**$0.001 per turn. 2–6s latency. 100% local. 475 hermetic eval cases at 100% green.** Every decision logged to `decisions` with `trace_id`, latency, tokens, cost. If a competitor wanted to clone Rahat's surface, they could ship the agents. They could not, in twelve months, replicate the *substrate* and the *trace*. That is the moat.

---

## 4. The Why Behind the Four Layers

The architecture evolved from three planes to four layers because the wall we hit was a product wall, not an engineering wall. Each layer exists to make a specific class of failure impossible to repeat. This page is not about how it is built. It is about why each layer earns its keep.

### Control Plane — Charter + Capability Registry

Without it, every agent invents its own ethics. Kobe pushes intensity even when Huberman just observed an HRV crash. The Charter is the only place where *"no"* lives. As a PM: governance must be a *plane*, not a per-agent feature, or it will rot the moment the third agent ships.

### Data Plane — Intent Ledger + Memory Substrate

Without it, agents argue. The Foodie suggests biryani while the Kobe holds a hammer-tier commitment. The substrate is the **single source of truth** — events, entities, preferences, archival, threads, relationships. The PM bet: the data model *is* the product. Get this layer wrong and no amount of model intelligence rescues you.

### Runtime Plane — Miya, Reasoner, Voice, Tool Dispatch

Without it, the user gets twenty parallel chat windows. Miya owns the inbox; the model-first reasoner picks tools deterministically; voice dresses the output. The runtime layer is where **latency and trust** are felt. It is the perception surface — the place where the habitat earns *Sukoon* (peace) or breaks it.

### Agent Adapter Plane — Per-Agent Memory Adapters

Without it, every new agent rewrites its own state schema and we drown in incidental complexity. Each agent ships a thin adapter (`assemble_context` + `extract_state`) over the universal substrate. The PM win: the 11th agent costs **one day**, not one quarter. That is the difference between a multi-agent demo and a habitat that compounds.

### The Habitat at a Glance — One-Page Architecture

```
   The User (Telegram · Watch · Photo)                           Ambient Sensors
            │                                                            │
            ▼                                                            │
  ┌─────────────────────────── CONTROL PLANE ───────────────────────┐    │
  │   ┌──────────────┐      ┌─────────────────┐    ┌─────────────┐  │    │
  │   │   The Miya   │ ───► │   The Charter   │    │ Capability  │  │    │
  │   │ Orchestrator │      │ approve·modify  │    │  Registry   │  │    │
  │   │ Single voice │      │      ·veto      │    └─────────────┘  │    │
  │   └──────────────┘      └─────────────────┘                     │    │
  └─────────────────────────────────────────────────────────────────┘    │
                                                                         │
  ┌──────────────────────────── RUNTIME PLANE ──────────────────────┐    │
  │  model-first reasoner · tool dispatch · voice · ~$0.001/turn    │    │
  │                                                                  │    │
  │  [Kobe*]  [Huberman◌]  [Coach]  [Curriculum]  [Foodie]      │    │
  │  [Voyager]     [+12 future agents — ~1 day each onboarding]      │    │
  └─────────────────────────────────────────────────────────────────┘    │
                                                                         │
  ┌────────────────────── AGENT ADAPTER PLANE ──────────────────────┐    │
  │  agents/<name>/memory.py  →  assemble_context() · extract_state()│    │
  └─────────────────────────────────────────────────────────────────┘    │
                                                                         │
  ┌───── DATA PLANE — THE MEMORY SUBSTRATE (vault/rahat.db) ────────┐ ◄──┘
  │  [Working: events]  [Core: entities]  [Semantic: prefs]  [Archival]│
  │  Intent Ledger ─ observations · intents · work orders · governance │
  │  Decisions Trace ─ trace_id · latency · tokens · cost              │
  │  Sleep-time consolidation · 03:00 cron · decay · archive · GC      │
  └─────────────────────────────────────────────────────────────────┘

  * live   ◌ stub
```

*Fig. 1 — Four layers. Miya owns the inbox; the Charter is the only chokepoint; every agent reads and writes the same substrate. A rendered SVG/PNG version lives in `specs/Rahat_PRD_v2.docx`.*

---

## 5. The Agent Mesh

Eight named agents — three live, five in flight. Each one owns a thin lens onto the same substrate.

**The Miya** (Orchestrator · Live). The Foreman. He owns the Telegram inbox, runs the capability registry, brokers cross-agent calls, and wraps every reply in Dakhini wit. He is the user's only conversational surface — and the only agent permitted to speak in the first person.

**Kobe** (Vitality Lead · Live). The clinical engine. He runs the trajectory math for the 80kg July 2026 goal, recalibrates weekly targets from Apple Watch telemetry, and holds the 25-tool model-first reasoner over a deterministic catalog. Originally a 2,930-LOC monolith; split on 2026-05-11 into protocols / state / handler / main — the four-file shape that future agents will copy.

**The Charter** (Governance Plane · Live). Not an agent — a policy chokepoint. Quiet hours, HRV-red blocks, family-priority overrides. Every write tool across the mesh passes through `charter.check()`. Writes verdicts to `governance_log` for audit. The reason the system can be trusted at 20 agents.

**Huberman** (Safety Veto · Stub shipped). The recovery agent. Reads HRV, sleep, RHR; advises tier downgrades. Holds the moral authority to mute performance nudges when the body says no. The stub already proves substrate reuse — ~110 LOC adapter — which is the load-bearing claim of the whole architecture.

**Matt Fraser / Coach** (Performance · Next). The elite programmer. Audits CrossFit volume, books loading for the 155kg deadlift baseline, owns `training_block` and `lift_history` entities. Onboarded against the same substrate the Kobe already taught.

**Curriculum** (Family · Next, Months 1–3). The developmental tracker for the toddler and the newborn. Owns `lesson`, `milestone`, `behavior_log`. The first agent whose subject is *not the user* — the substrate's `subject_id` columns are why this onboards in days, not months.

**The Foodie** (Compliance · Later). The vision-based meal auditor. Photo in, dietary verdict out. Enforces Vegetarian / No-Red-Meat constraints, scores hole-in-the-wall authenticity, and suppresses inflammatory options when the Kobe flags recovery. Zero-Input Constraint embodied.

**The Voyager** (Logistics · Later). The deep-cut travel concierge. Manages international itineraries (India / Japan), visa state, family-aware pacing. Asynchronously builds a per-trip recall corpus so a question three months later still resolves.

---

## 6. Success Metrics

The metrics are deliberately personal — Rahat is built for one user before it is built for many. They are also irreducible: if the body and the family thrive, the system works.

- **Intent Realization.** 80 kg by July 1, 2026. 155 kg deadlift in the same window.
- **Cognitive Load.** Manual health and logistics app interactions per week — trending to zero.
- **Sukoon (Peace).** Zero hustle-culture friction during low recovery or high family priority. Measured by Charter veto rate × Huberman tier-downgrades honored.
- **Agent Velocity.** Time-to-onboard a new agent. Target ≤ 1 day. Current: 1 day (Huberman stub validated).
- **System Health.** 475 hermetic eval cases, 100% green. <60s suite runtime. Cost per turn ≤ $0.005.

---

## 7. Critical Path — Now / Next / Later

The Now bar is set by a single forcing function: anything done twenty times must be cheap.

**Now (months 1–6).** Miya-as-entrypoint cutover. Huberman as full agent. Curriculum agent for toddler + newborn. Single-voice enforcement across the mesh.

**Next (months 6–12).** Profile store. Embedding-based agent retrieval (when 20 agents start to overlap). Foodie + Voyager onboarded against the proven contract.

**Later (12+ months).** FastAPI gateway over Tailscale. Mobile cockpit (SwiftUI, APNs). Multi-tenant (partner, toddler). Skill manifests and a true registry — but only when third-party agents plug in.

---

## 8. Reserved — Future Agents & Frontiers

> *Held intentionally open. As the mesh scales past ten agents and the substrate proves out, this section will hold the next decade of agents, surfaces, and frontiers. Each earns inclusion only when triggered by lived friction — not by speculation.*

### Agents #9 – #20 (Backlog)

- **Sleep.** Sleep-architecture coach; reads Watch sleep stages, advises wind-down rituals, holds wake-window entities.
- **Finance.** Sovereign budgeting agent; never executes trades; observes spend, categorizes, flags drift from monthly intent.
- **Ritual.** The generalized coffee/tea/music/morning-walk optimizer — the original "Ritual Optimization Agent" from v1.
- **Music (Guitar).** Practice tracker; logs sessions; remembers chord progressions and pieces in flight.
- **Reading.** Book and longform tracker; archives highlights; recalls what was learned six months ago.
- **Inbox.** Email triage with the Charter applied; never sends; only summarizes and queues.
- **Toddler-Companion.** Daily-routine and developmental-cue agent for the toddler subject.
- **Newborn-Companion.** Feed / nap / wake-window tracker for the newborn subject.
- **Ancestor.** Family-history recall corpus — names, dates, photos, voice notes — a memory inheritance layer.
- **Travel-Companion.** In-trip Voyager — knows where you are, what's next, what you forgot.
- **Reflection.** Weekly journal agent; reads the events table; surfaces patterns the user did not see.
- **Guest.** Third-party agent slot — the first non-first-party citizen of the habitat. Charter applies in full.

### Cross-Surface Frontiers

Mobile cockpit (SwiftUI, Sign-in-with-Apple, APNs). Watch face. Ambient display (always-on glanceable status). Voice-out (Miya's TTS with Dakhini prosody). CarPlay surface for the commute. Each surface is a thin projection over the same substrate — never a new system of record.

### Substrate Frontiers

Event-projection rebuild (replay last week through a new routing strategy). Episodic-to-semantic distillation. Federated multi-user (partner-aware Charter). Trust delegation (time-bounded permissions for guest agents). A queryable Public Charter export for the family.

### Governance Frontiers

Family-shared veto policies. Audit-grade decision replay. Public Charter version-control. Time-bounded delegations for caregivers. A "Sabbath mode" that mutes every performance agent simultaneously — Huberman's veto, generalized.

---

## 9. Reserved — Open Questions & Bets

Placeholders held open for the next quarter. Filled in only as the system tells us what to ask.

### Open Questions

- **When does the substrate need a vector index?** Current archival uses cosine over 768-d Gemini embeddings; works at today's scale. Re-evaluate at ~50k archival rows.
- **When does Miya need a planner, not just a router?** The threshold is multi-step intents that cross three or more agents. Not yet.
- **When does the family become a first-class subject?** When the Curriculum agent ships and the partner asks for their own view. Likely month 4.
- **When does the cloud earn a role?** Only for: (a) outbound voice synthesis at the edge of the home network, (b) emergency offsite encrypted backup of the substrate. Never for inference on biometric data.

### Bets

- **Bet 1: The substrate is the moat.** Agents are commodity by 2027. Memory and governance are not.
- **Bet 2: Local-first wins the home.** The Mac Mini in the closet is the right form factor for the next decade of personal AI.
- **Bet 3: One voice beats twenty.** Users will not maintain twenty parallel relationships. Miya as orchestrator is non-negotiable.
- **Bet 4: Sovereignty is a feature, not a posture.** The biometric trail and family logistics belong to the household. Everything else follows.

---

## 10. Reserved — Roadmap Scratchpad

> *This page is intentionally light. It is where the next planning cycle lives. As of May 2026 it holds only the next forcing functions.*

### The Next Three Forcing Functions

1. **Miya-as-entrypoint cutover.** Kobe must stop being the launchd entrypoint. Until Miya owns the inbox by default, the "single voice" principle is only a promise.
2. **Huberman's first real veto.** The substrate is proven only when an HRV-red morning causes the Kobe to silently drop intensity — without the user noticing the absence. Target: month 1.
3. **Curriculum's first non-self subject.** The first time a row in the substrate has `subject_id = toddler`, Rahat becomes a household OS rather than a self-tracking tool. Target: month 3.

### Closing

**Rahat's deepest claim** is not technological. It is anthropological. The next decade of personal AI will not be decided by who has the biggest model. It will be decided by who builds the habitat — the substrate where the user's memory, body, family, and time can finally live in one trusted place. The cloud cannot do this. The household can. ***Rahat is the household's operating system.***

— *Building Rahat in public.*
