# Venkat Sadras

> **I build the habitat AI agents live in — the control plane, the shared memory, the runtime, the orchestration. Personal scale today; the patterns the enterprise is converging on.**

I don't build individual agents — I build **Rahat**, the *habitat* they all live in: one shared memory, one rulebook, one runtime, one orchestrator. A **control plane for the agent era**, run daily at human scale in my own life. I write about what building the whole environment — not the apps on top of it — teaches for personal *and* enterprise AI.

My bet, in one line: **AI gets genuinely useful when the environment around the model remembers your life and coordinates — not when the model alone gets smarter.** The habitat is the product; the model is a component. Same lesson whether it's one household or a company running a fleet of agents.

→ **[See the architecture: Rahat →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## How I think about agents

Not a manifesto — the questions I work on concretely in Rahat, and where the field stands in 2026. Positions I can defend with a running system, not just opinions.

**1. "Agent control plane" became the category — and that's the tell.** The 2026 enterprise fight isn't about one clever agent; it's the control plane — orchestration, governance, observability, identity ("Kubernetes for agents"). The striking part: a one-household system and a thousand-seat platform converge on the *same* primitives — memory, governance, orchestration, evaluation. When the small case and the huge case agree, personal-scale work transfers straight to the enterprise problem.

**2. Memory went from a feature to a category — and "typed" beat "retrieval."** "Agent memory" used to mean a vector store bolted onto a chat log; by 2026 the field moved to typed, structured memory the agent updates deliberately. The live problems now: multi-scope memory (per-user / per-agent / per-run) and *restraint* — not letting an agent keep every passing fact forever.

**3. The standards settled faster than the patterns.** MCP (model-to-tool) is effectively default now; A2A is consolidating the agent-to-agent half. I built Rahat's tool layer *before* the standards landed — so my real question is concrete: what migrates to the open protocols, and what stays bespoke?

**4. Governance, not capability, is the gate.** What decides whether an agent ships to production isn't model quality or cost — it's whether you can state and enforce what an agent may do on its own. Rahat has had one policy layer every action passes through from day one. Least glamorous primitive; the one that matters most.

**5. The runtime is becoming the product.** Providers now package permissions, checkpointing, and memory into the runtime — 2026's "agent harness." That's Rahat's next milestone too, and it turns on one question: *what makes adding agent #11 as cheap as agent #1?*

**6. Local and cloud, not local versus cloud.** Frontier reasoning in the cloud; personal state and policy on the device. Rahat routes the hard thinking out and keeps memory and vetoes local — the same split enterprise platforms are landing on, for the same reasons: cost, latency, privacy.

---

## Rahat — what it actually is

> *Rahat (Urdu: رہات) — relief, ease, the lifting of a burden.*

A small fleet of specialized agents that share one memory, follow one rulebook (a central policy layer), and are coordinated by one orchestrator — so they can help with real, messy moments instead of answering one-off prompts. It runs locally on a Mac Mini and is used daily through my own life.

**The governing principle:** *deterministic shell, LLM core.* The model proposes; tested, deterministic code does the math, enforces the rules, and executes. The model never invents a number, because numbers come from tools — it just decides which tools to call. Every agent is defined the same way: `name + description + system_prompt + tools`.

**Honest status.** Three agents work today, plus the orchestrator and the policy layer; the memory and evaluation infrastructure are live. The first agents coach training and recovery — not because this is a fitness project, but because my own life was the most unforgiving test domain I had. The roadmap is an *everyday* mesh — weekend planning, gifts, dinner, travel — each new agent built on the same shared contract. **It's a personal-scale build, not enterprise software; the claim is that the problems are the same class, and solving them small is a real way to understand them.**

**Engineering discipline:** a five-layer hermetic test suite gates every change; every bug fix ships with a regression test; a pre-push gate blocks anything that breaks the suite. The repo is the proof, not a plan.

→ **[Read the architecture →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## About me

Bay Area product manager. Hyderabad roots. Husband, and father of two — including a newborn who supervised most of the early build during parental leave. I care about building the kind of agents I'd actually want pointed at my own life: ones that remember, coordinate, and know when *not* to act.

I'm writing about agents, the control-plane idea, and what building Rahat teaches — in plain language, for people who want to understand where this is going without the jargon.

---

<sub>Local-first · Apple Silicon · SQLite · frontier LLM + structured tool calling · Telegram. Built on [OpenClaw](https://openclaw.ai). Opinions my own.</sub>
