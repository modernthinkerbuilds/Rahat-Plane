# Venkat Sadras

> Building **Rahat** — a sovereign habitat for personal AI agents.

**The substrate is the moat.** The model layer is rapidly commoditizing — Gemini ↔ Claude ↔ open-weight ↔ on-device, swapped in hours. The non-commodity layer — and therefore the product moat — is the *habitat* underneath: shared memory, shared state, a policy chokepoint, a heartbeat. Whoever ships those primitives as first-class objects owns the next decade of agent platforms.

Rahat is my bet on that thesis — a four-layer architecture running locally on a Mac Mini, syncing with my Apple Watch today, growing toward ~20 specialized agents over the next 12 months. I'm a PM at Google by day; the first version of Rahat shipped during parental leave with a newborn, between feeds, because at this point in life — toddler, newborn, household, training, day job — manual logging and reactive AI are both genuinely insulting. **Cognitive offload, by design.**

If you're thinking about how product management evolves when the surface goes ambient and the substrate becomes the moat, you're in the right place.

---

## 🛠️ The questions I'm working on

Not a skills list — five open questions in my head. Each one shapes the next 12+ months of what gets built:

**How does the memory substrate get typed and transactional?** Today's "memory" is a vector store plus a prompt template. Tomorrow's is a schema'd substrate with events, entities, preferences, and archival tiers — written and read as *agent actions*, not retrieved as always-on infrastructure. Letta, MemGPT, and the Apple Intelligence personal-context layer all point at the same shape. The schema is the unsolved part.

**What's the right primitive for "agent" so the 11th costs the same as the 1st?** MCP, Vertex Agent Engine, OpenAI GPT Actions, A2A are competing answers. None are finished. The shape of the eventual answer determines who owns the agent ecosystem of the next decade — same way "the right primitive for HTTP services" decided the cloud era.

**Where does policy live in a fleet of write-capable agents?** Per-agent enforcement breaks at scale. A separate policy plane — predicates over every write-tool, audit log, deterministic veto path — is the structurally right answer. It isn't a shipped primitive in any commercial agent platform yet. That's a white space.

**What collapses first: cloud-only or model-only?** Apple-Silicon-class inference plus capable small models (Gemma, Llama, Apple Intelligence) are inverting the capability-vs-sovereignty tradeoff. The trend curve on local inference is steeper than the trend curve on user trust in cloud personal-data. By 2028, "your data never leaves your machine" reads the way "we're open source" read in 2015.

**What's the right surface beyond chat?** Chat won the entry point but lost the substrate. Ambient observation — watch, screen, calendar, kitchen, vision — is the next decade's interface area. The product surface, not the model, is where the user actually lives.

I work on all five concretely in Rahat. The repo is the proof, not a plan.

---

## 🚀 Rahat — the thesis, in product form

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

Rahat is a **Sovereign Habitat for Personal Agents**: a four-layer architecture (Control / Data / Runtime / Agent Adapter) where specialized agents live, remember, decide, and act over a shared intent ledger and a four-tier memory substrate, coordinated by a heartbeat-driven loop. The habitat framing is structurally honest — a habitat needs *governance* (Charter), *land and stored history* (intent ledger + memory substrate), *active life* (Miya orchestrator + reasoner + ticks), and *species-specific niche behavior* (per-agent adapters). Each architectural layer maps to a real noun; the metaphor isn't decoration.

Instead of a chatbot you have to prompt, Rahat operates ambiently: it observes reality through Apple Watch and (soon) ambient sensors, remembers commitments through a typed substrate, measures the delta from goals through a model-first reasoner, and only surfaces decisions that actually need a human.

### Three architectural commitments — this is where the moat lives

Each commitment is a deliberate bet against a path-of-least-resistance alternative that failed first. None of them are clever; all of them compound.

**Commitment 1 — Typed three-plane substrate over in-process calls.** Agent #2 couldn't see Agent #1's context. The easy fix was direct method calls; the structurally correct fix was a shared SQLite intent ledger, a Charter as a policy plane, and Miya as a broker. Direct calls produce a working 2-agent product and an unworkable 10-agent one. The right primitive isn't a function call — it's a row.

**Commitment 2 — Memory as a typed substrate, accessed by agents as a tool.** RAG over conversation logs broke around agent #2. The fix was a four-tier substrate (events / entities / preferences / archival) with per-agent adapters that *actively manage* what each agent knows. The model sees a structured `[Active goal: …] [Commitments: …] [Plan: …]` block in front of every reply — the "agent forgot what I told it yesterday" failure mode is killed at the architecture level, not the prompt level. Cost per turn for substrate ops: ~$0.0001. Sleep-time consolidation runs nightly. Pattern lineage: Letta, MemGPT, Apple Intelligence personal-context.

**Commitment 3 — Model-first reasoner over a deterministic tool catalog.** Regex routing broke on multi-clause questions and Hyderabadi-English code-mix. The reasoner is now a Gemini 2.5 Flash loop over 25 deterministic tools: tools enforce the math, dates, rate limits, policy; the model orchestrates. Cost per turn: ~$0.001. Latency: 2–6s. Hallucination risk on numbers: zero, because numbers come from tools, not from the model. The open question is whether the model also *writes* the tools — that's where I'd point the next year of platform R&D.

### Where the foundation stands today

- **475 hermetic test cases across 8 suites, 100% green.** Evals are the spec; every reported failure becomes a permanent case. The eval suite is the contract between me and the next architectural change.
- **Kobe in production** through the new contract — full memory adapter (`goal`, `plan`, `commitment`, `tier_change`), full 25-tool reasoner, daily real use through Telegram. Named after Kobe Bryant: discipline applied to the body-goal arc.
- **Huberman shipped as a stub** to prove the substrate composes for non-vitality agents — memory adapter ready (`recovery_protocol`, `sleep_concern`), Charter enforcement wired. Named after Andrew Huberman: the recovery counterweight, with veto power when HRV says rest.
- **A parsimony pass removed ~5,200 LOC of pre-pivot drift** and split the reference agent's 2,930-LOC entry point into four cleanly-scoped modules — a 95% reduction at the entry, with 142 hermetic tests staying byte-identical across every step. Refactoring with evals as the contract is *substantially* different from refactoring without.
- **"Frictionless Setup" is a first-class architectural principle** alongside *Local-first sovereignty* and *Deterministic core, LLM at edges*: any fresh clone reaches green tests with one `bash bootstrap.sh`. If the 11th agent costs 11 days because a new collaborator has to edit five files for their machine, the moat is fake.

### The agents in the mesh today

| Agent | Role | What it owns |
|---|---|---|
| **The Miya** | Orchestrator + supervisor | Single voice; capability registry; cross-agent broker; Dakhini voice layer (`core/voice.py`) |
| **Kobe** | Vitality | Trajectory math, 25-tool reasoner, full memory adapter (`goal`, `plan`, `commitment`, `tier_change`) |
| **Huberman** | Recovery — *stub shipped* | HRV / sleep / RHR; advises (Charter enforces). Memory adapter ready (`recovery_protocol`, `sleep_concern`) |

Plus one piece of infrastructure that isn't an agent — **The Charter** — the policy plane every outbound write-tool passes through. Quiet hours, HRV-red blocks, family-priority overrides: written once, applied uniformly. Huberman advises; the Charter enforces. The separation between advisor and enforcer is what keeps governance consistent as the mesh grows past three agents.

### The roadmap is a hypothesis test

Eighteen agents in the Now/Next windows (~6 months to month 18, scaling toward ~21 total), each shipped against the same agent contract: entity types + adapter + tool registry, ~1 day per agent. **Fraser** (CrossFit programming), **Montessori** (toddler + newborn development), **Buffett** (calendar + scheduling), **Ramu Kaka** (pantry + grocery), **Ramsay** (cooking + dietary audit), **La Marzocco** (espresso ritual), **Disney** (kids' weekend), **Genie** (family weekend), **Polo** (pre-trip), **Bourdain** (in-trip), **Santa** (gifts), **Mocha** (coffee shop curator), **Antoinette** (pastry), **Luwak** (beans procurement), **Sherlock** (local treasures), **Ustad** (guitar), **Casanova** (date night, held).

The roadmap exists to test exactly one hypothesis: **that the marginal cost of agent N+1 is bounded by the substrate, not the integration tax.** If commitments 1–3 are right, agent #6 ships in ~1 day. If they're wrong, that's where I'll find out — and the build log will say so.

→ **[Read the Rahat architecture →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## 🧠 How I think about agents

Eight bets about where the agent platform layer goes over the next 24 months, grouped under three strategic pillars. Each is falsifiable. Each shapes what I'm building.

---

### Pillar I — Trajectory: where the platform fight actually is

The surface war is over. The next decade of competitive advantage is decided one layer down — at the substrate, the moat, and the trust model. These three bets define the *strategic terrain* agent platforms compete on.

**1. Chat won the entry point and lost the substrate.**
The interface war is over for the *surface*: chat is the universal on-ramp and isn't going anywhere. But it lost the substrate war the moment users started expecting agents to *act* between messages. The next product surface is ambient — the agent reads your watch, your screen, your calendar, your fridge, and chat is one channel of many. Companies still framing themselves as "the ChatGPT for X" are competing for the smallest box on the screen.

**2. The model is not the moat. The habitat is.**
For any agent platform with more than one agent, the model layer is rapidly becoming commodity — Gemini ↔ Claude ↔ open-weight ↔ on-device, swapped with hours of work. The non-commodity layer — and therefore the actual product moat — is the *habitat*: the shared intent ledger, the typed memory substrate, the policy chokepoint, the heartbeat that ties them together. Swap the model and the product still works. Swap the habitat and you have a different product. This is exactly why Vertex Agent Engine and Bedrock Agents are mostly *substrate* investments, not model investments. Whoever owns the habitat owns the relationship.

**3. Local-first will be to 2028 what open-source was to 2015.**
In 2015, "we're open source" went from being engineering trivia to being a Series-A pitch line. By 2028, "your data never leaves your machine" goes from being a privacy-team checkbox to being the primary purchase reason for personal AI. The substitution curve is already visible — Apple-Silicon-class inference, capable small models (Gemma, Llama, Apple Intelligence), and the macro shift in user trust after a decade of data breaches. The hyperscaler tradeoff (capability vs sovereignty) is being quietly inverted by hardware. Platforms that pick the right side of this curve early will look prescient in 2028; cloud-exclusive bets will read like 2015 enterprise vendors did to GitHub.

---

### Pillar II — Primitives: what the platform is actually made of

Below the chat surface sits a small set of primitives that every serious multi-agent system rediscovers independently. The platforms that ship these as first-class objects will absorb the platforms that don't. These three bets define the *architecture* of the habitat.

**4. The memory layer is the agent platform layer.**
Three years ago "memory" meant a vector store bolted onto a chat loop. Today every serious multi-agent system converges on the same answer independently — Letta, MemGPT, Cognition's Devin, Anthropic's emerging memory work, the Apple Intelligence personal-context layer. The platforms that win the next cycle will treat memory the way 1995 databases treated state: first-class, typed, transactional, schema'd. Rahat's four-tier substrate (events / entities / preferences / archival) is a bet on this shape — high-confidence because the alternative (RAG over conversation logs) collapses past ~3 coordinated agents.

**5. Memory is an agent action, not always-on infrastructure.**
The cost-per-turn math forces this. If every reply triggers an unbounded retrieval, you pay for context the agent didn't need and you bloat the substrate with everything that ever touched it. If the agent decides *when* to remember, forget, and recall — and a nightly consolidation worker compacts the substrate — you get sub-linear cost scaling and a memory layer that stays sharp instead of fattening. Letta named this insight; the field is catching up; most agent products haven't internalized it yet.

**6. Model-first reasoner over deterministic tools is the floor, not the ceiling.**
The 2024 pattern was "LLM as fallback for unmatched regex." The 2026 floor is the inverse: every turn goes through a model that *decides which deterministic tools to call*. Tools enforce the math, the dates, the rate limits, the policy. The model orchestrates. Cost per turn: ~$0.001. Latency: 2–6s. Hallucination risk on numbers: zero, because numbers come from tools. For health, finance, scheduling, or any agent the user might *act* on, this is now table stakes — and the open question is whether the model also writes the tools. That's the question I'd put to any agent platform candidate.

---

### Pillar III — Composition: what makes the platform scale past the third agent

A platform with three agents is a demo. A platform with twenty is a product — but only if the cost of adding the next agent doesn't compound. These two bets define the *operating discipline* that closes that gap. They are also, in my read, the most underclaimed white space in the current commercial agent landscape.

**7. Policy as code is how multi-agent systems stay safe at scale.**
By the time you have five agents that can write to the world (messages, calendar invites, payment intents, health logs), no single agent can be trusted to enforce its own constraints. They drift, they disagree on the user's intent, they reinvent the same safety rule three different ways. A separate policy plane that every write-tool passes through — Python predicates, an audit log, a deterministic veto path — is what separates a product from a science-fair demo. Same shape as IAM policies, same shape as feature-flag governance, applied to agents. That this isn't a solved primitive in any commercial agent platform is one of the most underclaimed product opportunities in the space.

**8. The N+1 problem is the strategic problem in agent platforms.**
Most agent companies will ship three-to-five specialized agents and stall. The reason isn't model quality or interface — it's that the 6th, 11th, 20th agent each demands a custom integration, custom eval, custom memory schema, custom tool catalog. The architectural decisions that govern *how cheaply you add the next agent* are not implementation details; they are the strategic decisions. MCP, Vertex Agent Engine's agent schema, OpenAI's GPT Actions are all attempts at the same question: what's the right primitive for "agent" so the 11th costs the same as the 1st? Whoever answers it owns the agent ecosystem of the next decade. Rahat is my answer; I'm wrong about parts of it; I'll know which parts by agent #6.

---

The three pillars compound. **Trajectory** tells you where to point. **Primitives** tell you what to build. **Composition** tells you whether what you built will still be standing at agent #20. Almost every public conversation about "agents" lives at the *surface* — what they say, what they can do this week. The strategic work is one floor down.

---

## 🏋️ About me

Bay Area PM. Hyderabad roots. Husband, father of two (toddler + newborn). The "Huberman" agent exists because there are days when pulling heavy is the wrong call and I needed an agent honest enough to say so. The whole point of Rahat is to build the kind of agents I actually want pointed at my own life — health, household, training, and (eventually) the espresso bar.

Outside work and code: CrossFit (currently chasing 155kg deadlift), espresso (Niche Zero + Bambino, Ethiopian naturals), guitar (slowly enough that an agent will eventually have to grade me).

<table>
  <tr>
    <td align="center" width="50%"><img src="./assets/deadlift.jpg" alt="Deadlift lockout" width="100%"><br><sub><i>Pulling heavy. The reason "Huberman" exists — to tell me when not to.</i></sub></td>
    <td align="center" width="50%"><img src="./assets/squat.jpg" alt="Back squat at depth" width="100%"><br><sub><i>Bottom of a back squat. Kobe's reasoner reads videos like this through tool calls and tells me what's drifting.</i></sub></td>
  </tr>
</table>

I write about training, recovery, and the long game at **[Resilient Soul](https://wordpress.com/post/resilientsoulsite.wordpress.com/37)** — building a body that holds up across decades, not just PRs. The principle that governs the blog is the same one that governs Rahat: optimize for the next 20 years, not the next 20 minutes.

---

## 🧱 Built on

[OpenClaw](https://openclaw.ai) · Apple Silicon (M4) · SQLite · Gemini 2.5 Flash · `text-embedding-004` · Telegram

---

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

<sub>All opinions my own.</sub>
