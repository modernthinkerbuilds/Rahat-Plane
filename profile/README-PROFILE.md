# Hi, I'm Venkat 👋

I'm a **Product Manager at Google by day, agent builder by night** — and during a recent stretch of parental leave, between feeds and naps with a newborn, the cracks of "by morning" too.

I build agents on **Rahat** — a sovereign, local-first runtime for personal AI agents. It runs on a Mac Mini in my home, syncs with my Apple Watch today, and over the next year will listen to my espresso machine, watch the contents of my fridge and pantry, and quietly handle the hundred small decisions that pile up in a household with two kids under three.

The vision is simple: **offload the cognitive overhead of modern life so humans can focus on what only humans can do.**

If you're thinking about how Product Management evolves in the agentic era — what to build, how to architect, where the moats actually are — you're in the right place.

---

## 🛠️ What I work on

- 🤖 **Multi-agent systems** — orchestration, state machines, agent-to-agent handoffs without context loss
- 🏠 **Local-first AI** — sovereign infrastructure on Apple Silicon (M4), zero cloud dependencies for personal data
- 🧬 **Agent PRDs** — high-fidelity specs that treat agents as products, not features
- 📐 **Frameworks for personal autonomy** — turning passive tracking into proactive, ambient intelligence
- ✍️ **Building in public** — documenting the journey from POC to platform, mistakes included

---

## 🚀 The Flagship: Rahat

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

**Rahat** is a **Sovereign Intent Runtime** — an architecture where specialized agents share a single intent ledger and coordinate through a heartbeat-driven loop. Instead of a chatbot you have to prompt, Rahat operates ambiently: it observes your reality, measures the delta from your goals, and only surfaces decisions that actually need a human.

### How it started — and why I rebuilt it

I started with **one agent**: a sports scientist who could read my training history and tell me what to lift. It was useful for about a week. The moment I tried to add a second agent, I hit the wall every multi-agent project hits: **the agents couldn't see each other's context, and they were each going to ship their own Telegram bot, their own Gemini client, their own send-message helper.**

That's when I realized the actual product wasn't any single agent. It was the runtime underneath. I rebuilt Rahat around three planes — a control plane (registry + The Charter for policy), a data plane (one SQLite ledger every agent reads and writes), and a runtime plane (an orchestrator named **The Miya** that owns the inbox and routes to specialists). Adding a new agent is now a Python class with three methods plus a `cases.yaml` for the eval suite.

Today the foundation is real: **316 test cases passing across three independent paths**, the Scientist running in production through the new contract, The Charter writing every governance decision to an audit log, an episodic memory layer ready for the Curriculum agent to track newborn developmental phases, and a Hyderabadi Dakhini voice layer (`core/voice.py`) that dresses every outbound reply at the orchestrator — so future agents inherit the persona for free.

### The agents currently in the mesh

| Agent | Role | What it owns |
|---|---|---|
| **The Miya** | Orchestrator | The only voice you hear — hybrid router + Dakhini-Hyderabadi voice layer (`core/voice.py`) |
| **The Scientist** | Vitality | Trajectory math against goal-bound deadlines |
| **Coach (Fraser)** | Performance | CrossFit programming, load auditing, technique cues |
| **Bajrangi** | Recovery | Reads HRV / sleep / RHR; recommends scaling — *advises, doesn't enforce* |
| **The Foodie** | Nutrition | Vision-based meal audits, dietary compliance |

And one piece of infrastructure that isn't an agent — **The Charter**, the policy plane every outbound work order passes through. Quiet hours, HRV-red blocks, family-priority overrides: written once, applied uniformly. Bajrangi advises; The Charter enforces. That separation is what keeps the rules consistent as the mesh grows.

**Coming in the Now window (months 1–6, scaling to ~20 agents):** **Curriculum** (toddler + newborn developmental phases), **Appointments** (calendar + scheduling), and a sequence of concierge-class specialists — **The Voyager** (travel research, Japan trip recall), **Annapurna** (pantry & grocery state), **The Barista** (espresso ritual + bean inventory), **Ustad** (asynchronous guitar audit). Each one ships against the same agent contract — 3–5 hours of work, not 3–5 weeks.

> The thesis I'm building toward: **the 11th agent should cost the same to add as the 1st.** That's where the moat lives.

→ **[Read the Rahat build journey →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## 🧠 How I think about agents

A few takes I've earned the hard way:

**1. Chat is a UI, not the architecture.**
Most "AI products" in 2026 are still chatbots in a trench coat. The interesting systems use chat as one of many interfaces — alongside heartbeats, ambient sensors, voice, and vision. Architecting around the conversation is a local maximum.

**2. Agents need shared state, not shared chat history.**
The hardest problem in multi-agent systems isn't reasoning — it's memory. RAG over conversation logs is a brittle hack. A structured intent ledger every agent reads from and writes to — plus an episodic memory layer that gives every life-phase a beginning and end — is what makes a fleet feel coherent.

**3. The real moat is composability.**
Shipping a single specialized agent is easy. Shipping a system where the 11th agent costs the same as the 1st is hard. That's where the moat lives — not in the model, not in the prompt, in the infrastructure.

**4. Separate the advisor from the enforcer.**
A Bajrangi who reads HRV and recommends scaling is a domain expert. A Charter that vetoes any work order violating quiet hours is a policy. Conflating the two means every new agent re-implements safety rules, and every safety rule is brittle in exactly one agent. Decouple them and the rules become consistent across the mesh by construction.

**5. Sovereignty is becoming a product feature, not a compliance checkbox.**
The more personal AI gets, the more it matters where the data lives. "Local-first" is going to read in 2028 the way "open source" read in 2015.

---

## 🏋️ About me

I'm a Bay Area PM with Hyderabad roots — a husband, and a father of two (a toddler and a newborn). I shipped the first version of Rahat during parental leave, in the gaps between feeds and naps, because at that point in life **manual logging is genuinely insulting.**

Outside of work and code: CrossFit, currently chasing a 155kg deadlift; espresso (Niche Zero + Bambino, dialing in Ethiopian naturals); and learning the guitar slowly enough that an agent will eventually have to grade my progress because nobody else is watching.

<table>
  <tr>
    <td align="center" width="50%"><img src="./assets/deadlift.jpg" alt="Deadlift lockout" width="100%"><br><sub><i>Pulling heavy. The reason "Bajrangi" exists — to tell me when not to.</i></sub></td>
    <td align="center" width="50%"><img src="./assets/squat.jpg" alt="Back squat at depth" width="100%"><br><sub><i>Bottom of a back squat. The Coach agent reads videos like this and tells me what's drifting.</i></sub></td>
  </tr>
</table>

I write about training, recovery, and the long game on **[Resilient Soul](https://wordpress.com/post/resilientsoulsite.wordpress.com/37)** — my CrossFit blog about building a body that holds up across decades, not just PRs.

I'm interested in the next decade of PMing: when agents are the surface area, when interfaces are ambient, when "the app" disappears into a heartbeat. I write about what I find as I build.

---

## 🧱 Built on

[OpenClaw](https://openclaw.ai) · Apple Silicon (M4) · SQLite · Claude Code · Telegram

---

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

<sub>All opinions my own.</sub>
