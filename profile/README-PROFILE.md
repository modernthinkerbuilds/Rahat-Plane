# Venkat Sadras

> Building **Rahat** — a sovereign habitat for personal AI agents.

**The substrate is the moat.** The model layer of personal AI is commoditizing — Gemini ↔ Claude ↔ open-weight ↔ on-device, swapped in hours. The non-commodity layer is the *habitat*: typed shared memory, a policy chokepoint, a heartbeat. Whoever ships those primitives as first-class objects owns the next decade of agent platforms.

Rahat is my bet on that thesis — running locally on a Mac Mini, daily through one PM's life. I'm at Google by day; Rahat shipped during parental leave because at this point in life — toddler, newborn, day job — manual logging and reactive AI are both genuinely insulting. **Cognitive offload, by design.**

---

## 🛠️ The questions I'm working on

Six open questions. Each one shapes 12+ months of what gets built next.

**1. How does the memory substrate get typed and transactional?** Today's "memory" is a vector store plus a prompt template. Tomorrow's is a schema'd substrate — typed entities (`goal`, `commitment`, `recovery_protocol`), versioned writes, lifecycle decay, cross-agent references — read and written as *agent actions*, not retrieved as always-on infrastructure. The schema is the unsolved part.

**2. What's the right primitive for "agent" so the 11th costs the same as the 1st?** Multiple efforts — MCP, A2A protocols, function-calling standards — are converging from different directions. The shape of the eventual answer determines who owns the agent ecosystem of the next decade.

**3. Where does policy live in a fleet of write-capable agents?** Per-agent enforcement breaks at scale. A separate policy plane — predicates over every write-tool, audit log, deterministic veto path — is the structurally right answer. White space.

**4. What collapses first: cloud-only or model-only?** Apple-Silicon-class inference + capable small models are inverting the capability-vs-sovereignty tradeoff. By 2028, "your data never leaves your machine" reads the way "we're open source" read in 2015.

**5. What's the right surface beyond chat?** Ambient observation — watch, screen, calendar, kitchen — is the next decade's interface area. The product surface, not the model, is where the user actually lives.

**6. What does Agent Quality look like once memory is a primitive?** The next frontier after substrate. Evals as spec, multi-architecture A/B tests, regression-resistance across pivots, deterministic-math guarantees, behavioral SLOs. Quality only becomes meaningful when agents *act* on the world *and* persist state across sessions — which is exactly when this gets unlocked. I'll get to this once Phase 1A agents are reliably composing memory.

I work on all six concretely in Rahat. The repo is the proof, not a plan.

---

## 🚀 Rahat — the thesis, in product form

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

A **Sovereign Habitat for Personal Agents**: four layers (Control / Data / Runtime / Agent Adapter) over a shared intent ledger, a four-tier typed memory substrate, and a heartbeat-driven loop. Every architectural layer maps to a real noun.

### Three architectural commitments

**1. Typed three-plane substrate over in-process calls.** Direct calls produce a working 2-agent product and an unworkable 10-agent one. The right primitive isn't a function call — it's a row.

**2. Memory as a typed substrate, accessed by agents as a tool.** Four tiers, deterministic context assembly, cost-bounded extraction, nightly consolidation. *Detailed below — this is the architectural centerpiece.*

**3. Model-first reasoner over a deterministic tool catalog.** Gemini 2.5 Flash loop over 25 deterministic tools per agent. Tools enforce the math, dates, rate limits, policy; the model orchestrates. Hallucination risk on numbers: zero — numbers come from tools, not from the model. Cost ~$0.001/turn; latency 2–6s.

### Agent memory — the architectural specifics

The single bet I'd most defend, and the one most agent systems get wrong. Four tiers, each with a distinct lifecycle and access pattern:

- **`memory_events`** — append-only firehose. Every meaningful event: messages, tool calls, sensor reads, charter verdicts. ~5K rows/day. Cheap to write, deliberately *not* the primary read path.
- **`memory_entities`** — typed objects with lifecycle and per-agent schemas. **Kobe** owns `goal`, `plan`, `commitment`, `tier_change`. **Huberman** owns `recovery_protocol`, `sleep_concern`, `hrv_window`. Each entity is versioned with `valid_from` / `valid_until` and can link cross-agent (a Kobe commitment ↔ a Huberman recovery_protocol).
- **`memory_preferences`** — sticky k/v with confidence decay. *"preferred_lunch=paneer+jowar"* reinforces per turn, decays 5%/week without reinforcement. Built-in entropy.
- **`memory_archival`** — text + 768-d embeddings (Gemini `text-embedding-004`). Cosine search for *"what did I tell you about Tokyo three months ago."*

Plus `memory_threads` (topic clusters) and `memory_relationships` (entity-to-entity links, cross-agent).

**Per-agent contract — two pure functions per adapter (~120–365 LOC each):**

- `assemble_context() → str` — builds the `[Active goal: …] [Commitments: …] [Plan: …]` block deterministically. No LLM call. ~5ms.
- `extract_state(user_msg, bot_reply) → None` — Gemini Flash JSON-mode parses the turn and writes new entities back to the substrate. ~$0.0001/turn.

**Memory as tools** — operations are first-class entries in the reasoner catalog: `recall_search`, `archival_search`, `archival_insert`, `list_active`, `upsert_pref`. The agent learns *when* to remember, forget, recall — not just *what*.

**Sleep-time consolidation** — nightly 03:00 cron summarizes inactive threads, decays stale preferences, archives expired entities, GCs events older than 365 days. Without it the substrate bloats; with it, it stays sharp.

The "agent forgot what I told it yesterday" failure mode is killed at the architecture level, not the prompt level. That single property is what 80% of "agent memory" startups are about to discover the hard way.

### Where the foundation stands today

- **475 hermetic test cases across 8 suites, 100% green.** The eval suite is the contract between me and the next architectural change.
- **Kobe in production** — full memory adapter, 25-tool reasoner, daily real use through Telegram.
- **Huberman shipped as a stub** — memory adapter ready, Charter enforcement wired.
- **Phase 1A in active build** — Genie (weekend composer), Disney, Santa, Montessori, Ramsay, Mocha. Each ~1 day per agent against the same contract (entity types + adapter + tool registry).

### The roadmap is a hypothesis test

Twenty-one agents total across Now / Next / Later windows. The roadmap tests exactly one hypothesis: **the marginal cost of agent N+1 is bounded by the substrate, not the integration tax.** If commitments 1–3 are right, agent #6 ships in ~1 day. If they're wrong, the build log will say so.

→ **[Read the Rahat architecture →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## 🧠 How I think about agents

Eight bets, three pillars. Each falsifiable.

### Pillar I — Trajectory: where the platform fight is

**1. Chat won the entry point and lost the substrate.** Chat is the universal on-ramp; ambient observation is the next decade's interface area. The agent reads your watch, your screen, your calendar — chat is one channel of many.

**2. The model is not the moat. The habitat is.** The model layer is rapidly becoming commodity. The non-commodity layer is the habitat — shared intent ledger, typed memory substrate, policy chokepoint, heartbeat. Hyperscaler agent platforms are converging on this realization; most of their R&D investment is in substrate, not models.

**3. Local-first will be to 2028 what open-source was to 2015.** Apple-Silicon-class inference + capable small models (Gemma, Llama, Apple Intelligence) are inverting the capability-vs-sovereignty tradeoff. Cloud-exclusive bets will read in 2028 like 2015 enterprise vendors did to GitHub.

### Pillar II — Primitives: what the platform is made of

**4. The memory layer is the agent platform layer.** Every serious multi-agent system converges on the same answer independently — Letta, MemGPT, Devin, Anthropic's memory work, Apple Intelligence personal-context. Memory becomes what 1995 databases were for state: first-class, typed, transactional, schema'd. RAG over conversation logs collapses past ~3 coordinated agents.

**5. Memory is an agent action, not always-on infrastructure.** Cost-per-turn math forces this. Agent decides when to remember, forget, recall; a nightly consolidation worker compacts the substrate. Sub-linear scaling on context cost.

**6. Model-first reasoner over deterministic tools is the floor, not the ceiling.** Tools enforce the math; the model orchestrates. Zero hallucination on numbers. For any agent the user might *act* on, this is table stakes. The open question is whether the model also writes the tools.

### Pillar III — Composition: what scales past the third agent

**7. Policy as code is how multi-agent systems stay safe at scale.** Five write-capable agents can't enforce their own constraints — they drift, disagree, reinvent the same rule three ways. A separate policy plane — Python predicates, audit log, deterministic veto path — is what separates a product from a science-fair demo. Not yet a shipped primitive in any commercial agent platform.

**8. The N+1 problem is the strategic problem in agent platforms.** Most agent companies ship three-to-five and stall. The architectural decisions governing how cheaply you add the next agent are the strategic decisions. Multiple agent-platform standards being drafted across the industry are all attempts at the same question.

---

**Trajectory** tells you where to point. **Primitives** tell you what to build. **Composition** tells you whether what you built will still be standing at agent #20. The strategic work is one floor down from where most public conversation lives.

---

## 🏋️ About me

Bay Area PM. Hyderabad roots. Husband, father of two. The "Huberman" agent exists because there are days when pulling heavy is the wrong call and I needed an agent honest enough to say so. The whole point of Rahat is to build the kind of agents I'd want pointed at my own life.

Outside work and code: CrossFit (currently chasing 155kg deadlift), espresso (Niche Zero + Bambino, Ethiopian naturals), guitar (slowly).

<table>
  <tr>
    <td align="center" width="50%"><img src="./assets/deadlift.jpg" alt="Deadlift lockout" width="100%"><br><sub><i>Pulling heavy. The reason "Huberman" exists — to tell me when not to.</i></sub></td>
    <td align="center" width="50%"><img src="./assets/squat.jpg" alt="Back squat at depth" width="100%"><br><sub><i>Bottom of a back squat. Kobe's reasoner reads videos like this through tool calls and tells me what's drifting.</i></sub></td>
  </tr>
</table>

I write about training, recovery, and the long game at **[Resilient Soul](https://wordpress.com/post/resilientsoulsite.wordpress.com/37)**. Same principle as Rahat: optimize for the next 20 years, not the next 20 minutes.

---

## 🧱 Built on

[OpenClaw](https://openclaw.ai) · Apple Silicon (M4) · SQLite · Gemini 2.5 Flash · `text-embedding-004` · Telegram

---

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

<sub>All opinions my own.</sub>
