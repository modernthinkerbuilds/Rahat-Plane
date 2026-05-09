# Hi, I'm Venkat 👋

I'm a **Product Manager at Google by day, agent builder by night** — and during a recent stretch of parental leave, between feeds and naps with a newborn, the cracks of "by morning" too.

I build agents on **Rahat** — a sovereign, local-first runtime for personal AI agents. It runs on a Mac Mini in my home, syncs with my Apple Watch today, and over the next year will listen to my espresso machine, watch the contents of my fridge and pantry, and quietly handle the hundred small decisions that pile up in a household with two kids under three.

The vision is simple: **offload the cognitive overhead of modern life so humans can focus on what only humans can do.**

If you're thinking about how Product Management evolves in the agentic era — what to build, how to architect, where the moats actually are — you're in the right place.

---

## 🛠️ What I work on

- 🤖 **Multi-agent systems** — orchestration, state machines, agent-to-agent handoffs without context loss
- 🧠 **Memory architectures** — four-tier substrates, agent-specific adapters, sleep-time consolidation
- 🔁 **Model-first reasoners** — LLM + deterministic tool catalogs over a shared state, replacing brittle regex routers
- 🏠 **Local-first AI** — sovereign infrastructure on Apple Silicon (M4), zero cloud dependencies for personal data
- 🧬 **Agent PRDs** — high-fidelity specs that treat agents as products, not features
- ✍️ **Building in public** — documenting the journey from POC to platform, mistakes and pivots included

---

## 🚀 The Flagship: Rahat

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

**Rahat** is a **Sovereign Intent Runtime** — a four-layer architecture (Control / Data / Runtime / Agent Adapter) where specialized agents share a single intent ledger *and* a four-tier memory substrate, and coordinate through a heartbeat-driven loop. Instead of a chatbot you have to prompt, Rahat operates ambiently: it observes your reality, remembers your commitments, measures the delta from your goals, and only surfaces decisions that actually need a human.

### Three pivots, one moat

I started with **one agent**: a sports scientist who could read my training history and tell me what to lift. Useful for about a week. The system has gone through three architectural pivots since, and each one made the next agent cheaper to add:

**Pivot 1: Three planes (Control / Data / Runtime).** Agent #2 couldn't see Agent #1's context. Built a shared SQLite ledger, a Charter for policy enforcement, and Miya as the orchestrator. Now agents share state by reading from one source of truth.

**Pivot 2: Memory substrate.** The Scientist kept forgetting my commitments. I'd say *"I want hammer tier for 2 weeks"* and the next day get a recovery lecture. After ten rounds of reactive patches, the root cause was undeniable: **chat history isn't memory.** Built a four-tier substrate (events / entities / preferences / archival) with per-agent adapters that *actively manage* what each agent knows about me. The model now sees a structured `[Active goal: ...] [Commitments: ...] [Plan: ...]` block before every reply. Costs ~$0.0001/turn extracting state back into the substrate. Sleep-time consolidation runs nightly at 03:00.

**Pivot 3: Model-first reasoner.** Regex routing broke on multi-clause questions and the Hyderabadi-English code-mix I actually use. Pivoted to a Gemini 2.5 Flash loop over a deterministic 25-tool catalog. The model decides what to call; tools execute deterministically; the cost ledger tracks every hop. Legacy regex stays as the fallback.

Today the foundation is real: **475 test cases passing across 8 suites**, the Scientist running in production through the new contract with full memory + reasoner, the Bajrangi recovery agent shipped as a stub to prove the substrate composes for non-Scientist agents, six standalone SVG architecture diagrams, ~12 modules in `core/`. The thesis I'm building toward: **the 11th agent should cost ~1 day to add, not 11 days.** That's where the leverage lives.

### The agents currently in the mesh

| Agent | Role | What it owns |
|---|---|---|
| **The Miya** | Orchestrator + supervisor | Single voice; capability registry; cross-agent broker; Dakhini voice layer (`core/voice.py`) |
| **The Scientist** | Vitality | Trajectory math, 25-tool reasoner, full memory adapter (`goal`, `plan`, `commitment`, `tier_change`) |
| **Bajrangi** | Recovery — *stub shipped* | HRV / sleep / RHR; advises (Charter enforces). Memory adapter ready (`recovery_protocol`, `sleep_concern`) |

Plus one piece of infrastructure that isn't an agent — **The Charter** — the policy plane every outbound write-tool passes through. Quiet hours, HRV-red blocks, family-priority overrides: written once, applied uniformly. Bajrangi advises; The Charter enforces. That separation is what keeps rules consistent as the mesh grows.

**Coming in the Now window (months 1–6, scaling to ~20 agents):** **Curriculum** (toddler + newborn developmental phases), **Coach (Fraser)** (CrossFit programming + load auditing), **Appointments** (calendar + scheduling). Then concierge-class specialists in the Next window: **The Voyager** (travel research, Japan trip recall), **Annapurna** (pantry & grocery state), **The Barista** (espresso ritual + bean inventory), **Ustad** (asynchronous guitar audit). Each one ships against the same agent contract — entity types + adapter + tool registry, ~1 day per agent.

→ **[Read the Rahat build journey →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## 🧠 How I think about agents

A few takes I've earned the hard way:

**1. Chat is a UI, not the architecture.**
Most "AI products" in 2026 are still chatbots in a trench coat. The interesting systems use chat as one of many interfaces — alongside heartbeats, ambient sensors, voice, and vision. Architecting around the conversation is a local maximum.

**2. Agents need shared state *and* shared memory.**
The hardest problem in multi-agent systems isn't reasoning — it's memory. RAG over conversation logs is brittle. A structured intent ledger that every agent reads/writes, *plus* a four-tier memory substrate (events / entities / preferences / archival) where each agent actively assembles its context and extracts new state every turn — that's what makes a fleet feel coherent and trustworthy across days.

**3. Memory is a tool the agent calls, not infrastructure that's always-on.**
The cutting-edge insight from systems like Letta is that the agent should learn *when* to remember, *when* to forget, *when* to recall. A nightly consolidation worker summarizes inactive threads, decays stale preferences, and archives expired entities. Without it, the substrate bloats; with it, it stays sharp.

**4. The real moat is composability.**
Shipping a single specialized agent is easy. Shipping a system where the 11th agent costs the same as the 1st is hard. That's where the moat lives — not in the model, not in the prompt, in the infrastructure.

**5. Separate the advisor from the enforcer.**
A Bajrangi who reads HRV and recommends scaling is a domain expert. A Charter that vetoes any write-tool violating quiet hours is a policy. Conflating them means every new agent re-implements safety rules and every safety rule is brittle in exactly one agent. Decouple them and the rules become consistent across the mesh by construction.

**6. Regex was a phase. Model-first with deterministic tools is the architecture.**
"LLM as fallback for unmatched messages" was an okay 2024 idea. The 2026 version is: every message goes through a model that *decides which deterministic tools to call.* Tools enforce the math. The model orchestrates. Cost per turn: ~$0.001. Latency: 2–6s. Hallucination risk on numbers: zero, because numbers come from tools, not from the model.

**7. Sovereignty is becoming a product feature, not a compliance checkbox.**
The more personal AI gets, the more it matters where the data lives. "Local-first" is going to read in 2028 the way "open source" read in 2015.

---

## 🏋️ About me

I'm a Bay Area PM with Hyderabad roots — a husband, and a father of two (a toddler and a newborn). I shipped the first version of Rahat during parental leave, in the gaps between feeds and naps, because at that point in life **manual logging is genuinely insulting.**

Outside of work and code: CrossFit, currently chasing a 155kg deadlift; espresso (Niche Zero + Bambino, dialing in Ethiopian naturals); and learning the guitar slowly enough that an agent will eventually have to grade my progress because nobody else is watching.

<table>
  <tr>
    <td align="center" width="50%"><img src="./assets/deadlift.jpg" alt="Deadlift lockout" width="100%"><br><sub><i>Pulling heavy. The reason "Bajrangi" exists — to tell me when not to.</i></sub></td>
    <td align="center" width="50%"><img src="./assets/squat.jpg" alt="Back squat at depth" width="100%"><br><sub><i>Bottom of a back squat. The Scientist's reasoner reads videos like this through tool calls and tells me what's drifting.</i></sub></td>
  </tr>
</table>

I write about training, recovery, and the long game on **[Resilient Soul](https://wordpress.com/post/resilientsoulsite.wordpress.com/37)** — my CrossFit blog about building a body that holds up across decades, not just PRs.

I'm interested in the next decade of PMing: when agents are the surface area, when interfaces are ambient, when "the app" disappears into a heartbeat. I write about what I find as I build.

---

## 🧱 Built on

[OpenClaw](https://openclaw.ai) · Apple Silicon (M4) · SQLite · Gemini 2.5 Flash · `text-embedding-004` · Telegram

---

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

<sub>All opinions my own.</sub>
