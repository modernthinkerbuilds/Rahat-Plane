# Venkat Sadras

> Building **Rahat** — a sovereign habitat for personal AI agents.

**Models will keep getting smarter. The interesting product question is everything around them.** A typed memory substrate, a policy plane, a heartbeat, an opinionated set of agent contracts — that's the *habitat*, and it's where personal AI compounds into something coherent across your life. Models advance; the habitat persists.

Rahat is the product form of several complementary bets — **typed memory, governance-as-code, ambient surface, sovereign data, and (next) evaluable agent quality.** Each stands on its own; together they compose. Running locally on a Mac Mini, daily through one PM's life. I'm at Google by day; the first version shipped during parental leave because manual logging and reactive AI are both genuinely insulting when you have a newborn. **Cognitive offload, by design.**

---

## 🛠️ The questions I'm working on

Not a roadmap — five open questions I want to make progress on this year.

**1. How does memory become typed and transactional?** Today's "memory" is a vector store plus a prompt template. Tomorrow's is a schema'd substrate — events, entities (with versioning and cross-agent references), preferences (with decay), archival recall. Letta, MemGPT, and the Apple Intelligence personal-context layer are converging on this shape. The schema is the unsolved part.

**2. What's the right primitive for "agent" so the next one ships in a day?** Multiple efforts — MCP, A2A, function-calling standards — are converging from different directions. The eventual primitive shapes who owns the agent ecosystem of the next decade, the same way "the right primitive for HTTP services" decided the cloud era.

**3. Where does policy live in a fleet of write-capable agents?** Per-agent enforcement breaks at scale. A separate policy plane — predicates on every write-tool, an audit log, a deterministic veto path — is the structurally right answer. Not yet a shipped primitive in any commercial agent platform. White space.

**4. Where does the interesting work happen — cloud, edge, or the intersection?** Frontier cloud models keep getting smarter; on-device inference is catching up at the privacy-sensitive end. The personal-agent space lives at the intersection: frontier reasoning in the cloud, always-on observation and personal-state work on-device, your data staying yours. *Concrete example:* Kobe routes a complex programming question to a cloud model, but the HRV-gated decision and the typed `commitment` entity stay local. Enterprise agent platforms are converging on the same intersection shape, just at different scale. That intersection design is what excites me — for personal AI *and* enterprise.

**5. What does the ambient surface actually look like?** Chat is one channel; ambient observation is the next decade's interface area. The agent hears your fumbles during a guitar practice session and notes which chord you're avoiding. It reads grind setting and pull timing during your espresso pull and learns your taste curve. It watches a homework session with your toddler and shapes the next one. The product surface, not the model, is where the user actually lives.

The repo is the proof, not a plan.

---

## 🚀 Rahat — the concept, in product form

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

A **Sovereign Habitat for Personal Agents**: four layers (Control / Data / Runtime / Agent Adapter) over a shared intent ledger, a typed memory substrate, and a heartbeat-driven loop. Each architectural layer maps to a real noun.

### Three architectural commitments

1. **Typed substrate over in-process calls.** Direct method calls produce a working 2-agent product and an unworkable 10-agent one. The right primitive isn't a function call — it's a row.
2. **Memory as a typed substrate, accessed by agents as a tool.** Four conceptual tiers — events, entities, preferences, archival — with cross-agent references and per-agent adapters. *Centerpiece detail below.*
3. **Model-first reasoner over a deterministic tool catalog.** Tools enforce the math, dates, rate limits, policy; the model orchestrates. Hallucination risk on numbers: zero — numbers come from tools.

### Agent memory — the centerpiece

The bet I'd most defend. Memory isn't a vector store you bolt onto chat; it's a *typed substrate* the agent reads and writes through. Four conceptual tiers — **events** (the firehose of what happened), **entities** (typed objects with versioning + cross-agent references), **preferences** (sticky-but-decaying signal), **archival** (semantic recall) — accessed as *agent actions*, not always-on infrastructure.

Each agent assembles its context deterministically before every reply, writes new state back at turn-end, and a nightly worker compacts the substrate so it stays sharp instead of bloating. The *"agent forgot what I told it yesterday"* failure mode is killed at the architecture level, not the prompt level.

### What's shipped

**475 hermetic test cases / 8 suites, 100% green.** **Kobe** in production — full memory adapter, 25-tool reasoner, daily real use through Telegram. **Huberman** shipped as a stub with memory adapter ready and Charter enforcement wired. **Phase 1A in active build** — Genie, Disney, Santa, Montessori, Ramsay, Mocha. Each ~1 day per agent against the same contract.

### The compounding roadmap

The architectural bet that drives the build: **agent N+1 is cheaper and faster than agent N, because the substrate compounds.** Each new agent inherits the memory schema, the Charter, the tool catalog, the eval harness — not all of which existed when agent #1 shipped. If the commitments are right, agent #6 ships in a day and agent #11 in hours. If they're wrong, the build log will say so.

→ **[Read the Rahat architecture →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## 🧠 How I think about agents

Eight observations across three pillars. Numbering resets per pillar.

### Pillar I — Trajectory: where the platform fight is

**1. Chat won the entry point; ambient won the surface.** Chat is the universal on-ramp and isn't going anywhere. But the next product surface is ambient — the agent reads your watch, your screen, your kitchen, your kid's homework. Chat is one channel of many.

**2. Models keep advancing; the habitat is the lasting moat.** Frontier models will keep getting smarter, and they're still expensive to differentiate on. The lasting product moat — for any agent platform with more than one agent — is the substrate underneath: shared ledger, typed memory, policy plane, heartbeat. Models swap; habitats compound. Hyperscaler agent platforms are converging on the same realization — most of their R&D investment is in substrate.

**3. Local + cloud, not local vs cloud.** Apple-Silicon-class inference and capable small models open a viable on-device path for ambient and privacy-sensitive work. Frontier cloud models keep their lead on novel reasoning. The interesting design space is the *intersection* — and it shows up the same way in personal AI and enterprise. "Your data never leaves your machine" becomes a meaningful product claim *alongside* cloud-powered features, not a replacement.

### Pillar II — Primitives: what the platform is made of

**1. The memory layer is the agent platform layer.** Every serious multi-agent system converges on this answer independently — Letta, MemGPT, Devin, Anthropic's memory work, the Apple Intelligence personal-context layer. Memory becomes what 1995 databases were for state: first-class, typed, transactional, schema'd.

**2. Model-first reasoner over deterministic tools is the floor.** Tools enforce the math; the model orchestrates. For any agent the user might *act* on, this is now table stakes. The open question — and where I'd put the next year of platform R&D — is whether the model also *writes* the tools.

### Pillar III — Composition: what scales past the third agent

**1. Policy as code is how multi-agent systems stay safe at scale.** Five write-capable agents can't enforce their own constraints — they drift, disagree, reinvent the same rule three ways. A separate policy plane is what separates a product from a science-fair demo. Not yet shipped as a primitive in any commercial agent platform.

**2. The compounding question is the strategic question.** Most agent companies ship three-to-five specialized agents and stall. The architectural decisions governing *how each new agent costs less than the last* are the strategic decisions. Same question is being approached from multiple directions across the industry.

**3. Quality becomes a real product attribute once memory is a primitive.** When agents *act* on the world and persist state across sessions, "did the chat feel okay today" stops being a useful metric. Evals as spec, behavioral SLOs, regression-resistance, deterministic-math guarantees — the next frontier after substrate. Building toward this once Phase 1A agents reliably compose memory.

---

## 🏋️ About me

Bay Area PM. Hyderabad roots. Husband, father of two. The "Huberman" agent exists because there are days when pulling heavy is the wrong call and I needed an agent honest enough to say so. CrossFit (rebuilding the deadlift back to 155kg — documented at Resilient Soul; a newborn intervened), espresso (Niche Zero + Bambino, Ethiopian naturals), guitar (slowly enough that an agent will eventually have to grade me).

<table>
  <tr>
    <td align="center" width="50%"><img src="./assets/deadlift.jpg" alt="Deadlift lockout" width="100%"><br><sub><i>Pulling heavy. The reason "Huberman" exists — to tell me when not to.</i></sub></td>
    <td align="center" width="50%"><img src="./assets/squat.jpg" alt="Back squat at depth" width="100%"><br><sub><i>Kobe's reasoner reads videos like this and tells me what's drifting.</i></sub></td>
  </tr>
</table>

I write at **[Resilient Soul](https://wordpress.com/post/resilientsoulsite.wordpress.com/37)** — same principle as Rahat: optimize for the next 20 years, not the next 20 minutes.

---

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

<sub>Apple Silicon (M4) · SQLite · Gemini 2.5 Flash · Telegram · [OpenClaw](https://openclaw.ai). All opinions my own.</sub>
