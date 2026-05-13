# PRD: The Rahat Plane

*A Sovereign Habitat for Personal AI Agents*

v3 · Modern Builder · May 2026 · Living Document

## 1. The Vision

The **Post-Chat era** will not be won by smarter chatbots. It will be won by the system that *remembers*, *governs*, and *acts* on your behalf in the background — without prompting, without leaking, without forgetting.

**Rahat** (Urdu: *رہات* — *relief, ease, the lifting of a burden*) is that system. Not an app. Not a workflow. A **Sovereign Habitat** — the complete environment where personal agents live, remember, decide, and act. Twenty specialists. One memory. One voice. One chokepoint. Built on a Mac Mini you own.

The thesis is short. As inference trends toward zero marginal cost, the binding constraint on personal AI shifts from reasoning to **memory, governance, and ambient access**. Whoever owns the substrate — the place agents live and coordinate — owns the relationship with the user. Rahat is a bet that this substrate must be *local, intimate, and sovereign*. The cloud cannot meet you in your kitchen at 6:47am with a toddler on your hip and a 38-hour fast in your bloodstream. The habitat can.

We are building Rahat to systematically eliminate decision fatigue across **health, family, performance, and ritual** — and to do it without surrendering biometric data, family logistics, or the operating system of one’s life to a third party. The goal is *Rahat* in the original sense: relief.

### The Lifestyle Wedge (v3 addition)

After scoring twenty-one candidate agents under a VP-PM-at-Anthropic lens, a sharper market thesis emerged. Apple owns the device. Google owns the calendar. OpenAI owns the chat. **Nobody owns your taste, remembered.** Yelp has reviews but no memory of you. TripAdvisor is anonymous. Trade Coffee knows your beans but nothing else. The substrate makes Rahat the only system that joins coffee, pastry, travel, gifts, kids, and family into a single taste graph that compounds over years. The first ten agents we ship are picked on exactly that bet — that *the curated-lifestyle market* is the wedge no incumbent can clone in twelve months.

## 2. Key Principles

Five non-negotiables. Every roadmap call, every architectural fork, every agent onboarding is adjudicated against these.

1.  **Sovereignty as Infrastructure.** All state — vitals, commitments, archival memory — lives in vault/rahat.db on local silicon. No cloud sync of biometric data. Trust is silicon-deep, not policy-deep.

2.  **Shared Memory, Not Shared Chat.** RAG-over-conversation-logs is brittle and leaky. Rahat agents read and write a structured substrate with six first-class primitives. When Kobe commits a goal, Ramsay sees it on the next turn — without re-reading a transcript.

3.  **One Voice, Many Minds.** Miya owns the inbox. Agents never speak directly to the user. This is the difference between a team and a roomful of bots.

4.  **Single Chokepoint Governance.** Every write tool across every agent passes through the Charter. Quiet hours, HRV-red blocks, family-priority overrides — written once, applied uniformly, audited to governance\_log.

5.  **Zero-Input Constraint.** If the user has to type their weight, log their food, or remind the system of yesterday, the feature has failed. Ingestion is ambient (Watch), vision-based (photo), or conversational — never form-based.

## 3. What Makes Rahat Special

> *Memory is the compounding asset. The agents are the interest payment.*

Three durable moats. They compound.

### The Memory Moat

Most personal AI tools forget you between sessions. Rahat doesn’t. A four-tier substrate — **events, entities, preferences, archival** — sits beneath every agent. Sleep-time consolidation runs at 03:00 to summarize threads, decay un-reinforced preferences, and archive expired entities. The 11th agent onboards in \~1 day because the substrate is already universal; each adapter is \~120–280 LOC. The substrate compounds with every turn the user takes — and it is not portable to a competitor.

### The Governance Moat

The Charter is the single chokepoint. Every write tool — commit\_picks, log\_weight, propose\_replan — calls charter.check() before executing. Policies are data, not code. Adding the 20th agent does not require re-litigating ethics or rate limits. As the mesh grows, the governance surface stays flat. That is rare.

### The Voice Moat

Miya wraps every outbound message through core/voice.py — a deterministic Hyderabadi Dakhini phrasebook. Idempotent. Numbers and structure preserved verbatim. Zero per-message LLM cost. The user has *one relationship*, not twenty. Personality is an architectural property, not an agent’s responsibility.

### The Economics Back the Bet

**$0.001 per turn. 2–6s latency. 100% local. 475 hermetic eval cases at 100% green.** Every decision logged to decisions with trace\_id, latency, tokens, cost. If a competitor wanted to clone Rahat’s surface, they could ship the agents. They could not, in twelve months, replicate the *substrate* and the *trace*. That is the moat.

## 4. The Why Behind the Four Layers

The architecture evolved from three planes to four layers because the wall we hit was a product wall, not an engineering wall. Each layer exists to make a specific class of failure impossible to repeat. This page is not about how it is built. It is about why each layer earns its keep.

### Control Plane — Charter + Capability Registry

Without it, every agent invents its own ethics. Kobe pushes intensity even when Huberman just observed an HRV crash. The Charter is the only place where *“no”* lives. As a PM: governance must be a *plane*, not a per-agent feature, or it will rot the moment the third agent ships.

### Data Plane — Intent Ledger + Memory Substrate

Without it, agents argue. Ramsay suggests biryani while Kobe holds a hammer-tier commitment. The substrate is the **single source of truth** — events, entities, preferences, archival, threads, relationships. The PM bet: the data model *is* the product. Get this layer wrong and no amount of model intelligence rescues you.

### Runtime Plane — Miya, Reasoner, Voice, Tool Dispatch

Without it, the user gets twenty parallel chat windows. Miya owns the inbox; the model-first reasoner picks tools deterministically; voice dresses the output. The runtime layer is where **latency and trust** are felt. It is the perception surface — the place where the habitat earns *Sukoon* (peace) or breaks it.

### Agent Adapter Plane — Per-Agent Memory Adapters

Without it, every new agent rewrites its own state schema and we drown in incidental complexity. Each agent ships a thin adapter (assemble\_context + extract\_state) over the universal substrate. The PM win: the 11th agent costs **one day**, not one quarter. That is the difference between a multi-agent demo and a habitat that compounds.

### The Habitat at a Glance

![Four-layer architecture: Control (Miya, Charter, Capability Registry), Runtime (Agent Mesh), Adapter (per-agent memory.py), Data (four-tier memory substrate with intent ledger and decisions trace).](media/b8b985314b4188e02a9461f7e253fde973a61f3d.png "Rahat architecture overview")

*Fig. 1 — One page, four layers. Miya owns the inbox; the Charter is the only chokepoint; every agent reads and writes the same substrate.*

## 5. The Agent Mesh — Twenty-One Named Specialists

v3 names every agent in the planned mesh. Each one is a specialist with a one-line job and a place in the build order. Status is one of: Live, Stub, Phase 1A (Months 1–2), Phase 1B (Months 2–3, gated on Mocha’s palate data), Soon (trigger-dated to the Tokyo trip at T+120 days), Later.

> *Naming note: this PRD reflects the May 2026 rebrand — the agent formerly called The Scientist is now Kobe; the recovery agent formerly called Bajrangi is now Huberman. Miya’s Dakhini conversational openers may still address Huberman as “Bajrangi bhai” — that is a nickname inside the relationship, not the brand.*

### Infrastructure (Live)

**The Miya** (Orchestrator). The Foreman. Owns the Telegram inbox, runs the capability registry, brokers cross-agent calls, wraps every reply in Dakhini wit. The user’s only conversational surface.

**The Charter** (Governance Plane). Not an agent — a policy chokepoint. Every write tool across the mesh passes through charter.check(); verdicts log to governance\_log. The reason the system can be trusted at twenty agents.

### Body & Recovery (Live + Rebrand)

**Fraser** (CrossFit programming · Live). Named after Matt Fraser. Niche but loud — keep as-is, do not expand until adjacent fitness pain demands it.

**Kobe** (Vitality / training trajectory · Live, rebrand from The Scientist). Named after Kobe Bryant. The clinical engine: trajectory math for the 80 kg / 155 kg deadlift targets, weekly recalibration from Apple Watch telemetry, the 25-tool model-first reasoner. Originally a 2,930-LOC monolith; split 2026-05-11 into protocols / state / handler / main — the four-file shape every future agent copies.

**Huberman** (Recovery / HRV / safety · Stub → Phase 1A rebrand). Named after Andrew Huberman. Reads HRV, sleep, RHR; advises tier downgrades. Holds the moral authority to mute performance nudges when the body says no. Promoted from stub to first-class agent in the v3 launch.

### Phase 1A — Months 1–2 (ship together with the rebrands)

**Genie** (Weekend family composer). Highest-scoring agent in the deck. Universal family rhythm — meals, chores, together-time, downtime. The agent your spouse asks you to build first.

**Disney** (Weekend kids composer). One-tap Saturday-morning itinerary tuned to weather, ages, museum memberships, the park you love, and the 1pm nap window. The parent-AI screenshot moment LinkedIn has been waiting for.

**Montessori** (Toddler + newborn). Multi-subject. Developmental cues, daily routine, milestones. The first agent whose subject is not the user — the substrate’s subject\_id columns earn their keep here.

**Ramsay** (Cooking + dietary audit). Vision-based meal audit + recipe agent. Suppresses inflammatory options when Huberman flags recovery. The agent that makes Rahat feel like a chef.

**Mocha** (Coffee shop curator). Anywhere you go, surfaces the one specialty roaster within ten minutes that matches your taste. Cross-city memory is the moat — Yelp has no taste. Starts the palate-graph clock for Phase 1B.

**Santa** (Gift conductor). Birthdays, anniversaries, weddings, condolences — picks, orders, ships, drafts the card. The single most LinkedIn-screenshot-able agent in the lineup.

### Phase 1B — Months 2–3 (gated on Mocha’s palate data)

**Luwak** (Coffee beans orderer). Trade ships beans; Luwak ships a palate. Learns across roasters and over years, ordering the next bag to arrive four days before you run out.

**Antoinette** (Pastry / sweet-spot curator). Anywhere you are, surfaces the one pastry shop worth seeking. Smaller TAM than coffee but ferocious repeat-share rate.

**Sherlock** (Local treasure finder). Whenever you’re in a new neighborhood, surfaces the one specialty thing this place is famous for. Gets richer with every other agent shipped.

### Soon — Trigger-dated to the Tokyo trip at T+120 days

**Polo** (Pre-trip planner · ship T-30 days). Itinerary, hotels matched to last hotels you loved, restaurants by neighborhood, kid-friendly thresholds, weather-aware packing list, OOO drafts. TripIt is a folder; Polo is a concierge with memory.

**Bourdain** (In-trip concierge · ship T-14 days). Replans on the fly when flights delay, kids melt down, restaurants close. Surfaces the right tip at the right moment. The agent that earns lifetime loyalty by saving one vacation.

**Casanova** (Date night composer · data-gated · unlocks month 5). Held until Mocha + Ramsay + Bourdain have \~3 months of taste data. Once ready: restaurant matched to last twelve places you loved, follow-on activity, books, texts the partner. Pure substrate payoff.

### Later

**Ramu Kaka** (Pantry + grocery). Universal but currently handled at the household. Promote when re-surfaces.

**Buffett** (Calendar + scheduling). Commodity surface — Reclaim/Clockwise/Calendly own this. Build only as a Charter-aware household layer.

**La Marzocco** (Espresso ritual). Narrow but high-LTV. Keep as a feature of Mocha until dedicated users justify a standalone.

**Ustad** (Guitar practice). Personal-passion agent. Build after the mesh is dense.

## 6. Success Metrics

The metrics are deliberately personal — Rahat is built for one user before it is built for many. They are also irreducible: if the body and the family thrive, the system works.

  - **Intent Realization.** 80 kg by July 1, 2026. 155 kg deadlift in the same window.

  - **Cognitive Load.** Manual health and logistics app interactions per week — trending to zero.

  - **Sukoon (Peace).** Zero hustle-culture friction during low recovery or high family priority. Measured by Charter veto rate × Huberman tier-downgrades honored.

  - **Agent Velocity.** Time-to-onboard a new agent. Target ≤ 1 day. Current: 1 day (Huberman stub validated).

  - **System Health.** 475 hermetic eval cases, 100% green. \<60s suite runtime. Cost per turn ≤ $0.005.

## 7. Critical Path — Phase 1A / 1B / Soon / Later

The build order is set by two forcing functions: (a) ship daily-utility agents first so the dogfood loop is tight, (b) gate dependent agents on the data their upstream produces. The Tokyo family trip at T+120 days anchors the travel pair.

**Phase 1A — Months 1–2.** Kobe rebrand. Huberman rebrand + promote stub to first-class. Six new agents: Genie, Disney, Montessori, Ramsay, Mocha, Santa. Ten total moving pieces; six new agents to ship; two rebrands as one 2-day architect ticket.

**Phase 1B — Months 2–3.** Luwak, Antoinette, Sherlock. Gated on Mocha accumulating \~4 weeks of palate signal so the cluster ships with substrate context rather than thin.

**Soon — trigger-dated.** Polo at T-30 days before Tokyo. Bourdain at T-14 days. Casanova unlocks \~month 5 when Mocha + Ramsay + Bourdain have \~3 months of taste data. With Tokyo at T+120 today and a newborn at home, this window is intentionally roomy.

**Later.** Ramu Kaka, Buffett, La Marzocco, Ustad — re-evaluate quarterly. Plus the 12-month frontiers: FastAPI gateway over Tailscale, mobile cockpit (SwiftUI, APNs), multi-tenant (partner, toddler), skill manifests for third-party agents.

> *By month six the LinkedIn pitch writes itself: ten agents, one substrate, one voice, one family. Built on a Mac Mini. Owned by me. That post will out-perform any single feature launch.*

## 8. Reserved — Beyond the Twenty-One

> *v3 named twenty-one agents and placed each in a build phase. This section holds what comes after the mesh is dense — the surfaces, frontiers, and second-generation agents that earn inclusion only when triggered by lived friction.*

### Agents \#22+ — held until use cases prove out

  - **The Sick-Day Composer.** One intent — kid is sick — fires a cascade across calendar, inbox, daycare, pharmacy, partner. Ship when Montessori + Huberman + Saathi-pattern routing is mature.

  - **The Doctor’s Brief.** Pre-appointment 1-page brief composed from substrate (symptoms, vitals, prior visits). Ship when the family-health archive is dense.

  - **The Tax Composer.** Year-round receipt/mileage/deduction scrape; December folder to the CPA. Ship after first tax cycle dogfooded.

  - **The Honest Mirror.** Weekly behavioral check from your own data — message tone, recovery, message-volume patterns. Held until users opt into a “hard truths” tier.

  - **The Friend Keeper.** When a person you care about goes quiet 6+ weeks, drafts a one-line nudge. Ship after Santa has a year of relationship signal.

  - **Ancestor.** Family-history recall corpus — names, dates, photos, voice notes.

  - **Guest.** Third-party agent slot — the first non-first-party citizen of the habitat. Charter applies in full.

### Cross-Surface Frontiers

Mobile cockpit (SwiftUI, Sign-in-with-Apple, APNs). Watch face. Ambient display (always-on glanceable status). Voice-out (Miya’s TTS with Dakhini prosody). CarPlay surface for the commute. Each surface is a thin projection over the same substrate — never a new system of record.

### Substrate Frontiers

Event-projection rebuild (replay last week through a new routing strategy). Episodic-to-semantic distillation. Federated multi-user (partner-aware Charter). Trust delegation (time-bounded permissions for guest agents). A queryable Public Charter export for the family.

### Governance Frontiers

Family-shared veto policies. Audit-grade decision replay. Public Charter version-control. Time-bounded delegations for caregivers. A “Sabbath mode” that mutes every performance agent simultaneously — Huberman’s veto, generalized.

## 9. Reserved — Open Questions & Bets

Placeholders held open for the next quarter. Filled in only as the system tells us what to ask.

### Open Questions

  - **When does the substrate need a vector index?** Current archival uses cosine over 768-d Gemini embeddings; works at today’s scale. Re-evaluate at \~50k archival rows.

  - **When does Miya need a planner, not just a router?** The threshold is multi-step intents that cross three or more agents. Not yet.

  - **When does the family become a first-class subject?** When the Curriculum agent ships and the partner asks for their own view. Likely month 4.

  - **When does the cloud earn a role?** Only for: (a) outbound voice synthesis at the edge of the home network, (b) emergency offsite encrypted backup of the substrate. Never for inference on biometric data.

### Bets

  - **Bet 1: The substrate is the moat.** Agents are commodity by 2027. Memory and governance are not.

  - **Bet 2: Local-first wins the home.** The Mac Mini in the closet is the right form factor for the next decade of personal AI.

  - **Bet 3: One voice beats twenty.** Users will not maintain twenty parallel relationships. Miya as orchestrator is non-negotiable.

  - **Bet 4: Sovereignty is a feature, not a posture.** The biometric trail and family logistics belong to the household. Everything else follows.

## 10. Reserved — Roadmap Scratchpad

> *This page is intentionally light. It is where the next planning cycle lives. As of May 2026 it holds only the next forcing functions.*

### The Next Three Forcing Functions

6.  **Miya-as-entrypoint cutover.** Kobe must stop being the launchd entrypoint. Until Miya owns the inbox by default, the “single voice” principle is only a promise.

7.  **Huberman’s first real veto.** The substrate is proven only when an HRV-red morning causes Kobe to silently drop intensity — without the user noticing the absence. Target: month 1.

8.  **Montessori’s first non-self subject.** The first time a row in the substrate has subject\_id = toddler, Rahat becomes a household OS rather than a self-tracking tool. Target: month 3.

### Closing

**Rahat’s deepest claim** is not technological. It is anthropological. The next decade of personal AI will not be decided by who has the biggest model. It will be decided by who builds the habitat — the substrate where the user’s memory, body, family, and time can finally live in one trusted place. The cloud cannot do this. The household can. ***Rahat is the household’s operating system.***

*— Building Rahat in public.*
