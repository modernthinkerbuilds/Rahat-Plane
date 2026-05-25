# Venkat Sadras

> **AI PM who builds agent infrastructure — and makes it make sense.**

I build **Rahat**, a *control plane for AI agents*, at human scale — a local-first system that gives a mesh of personal agents one shared memory, one rulebook, and one orchestrator. I work on it daily, and I write about what it teaches about deploying agents anywhere.

The short version of my bet: **AI gets genuinely useful when it remembers your life and coordinates — not just when the model gets smarter.** The model is the engine; the layer underneath it — memory, governance, orchestration, evaluation — is the car. That layer is where I spend my time.

→ **[See the architecture: Rahat →](https://github.com/modernthinkerbuilds/Rahat-Plane)**

---

## How I think about agents

Not a manifesto — the questions I'm actually working on, and where the field stands in 2026. I work on each one concretely in Rahat, so these are positions I can defend with a running system, not just opinions.

**1. "Agent control plane" became the category — and that's the tell.** The enterprise fight in 2026 is no longer about building one clever agent; it's about the *control plane* — orchestration plus governance, observability, and identity, often described as "Kubernetes for AI agents." What I find most interesting is the convergence: a personal system built for one household and an enterprise platform built for thousands land on the **same four primitives** — memory, governance, orchestration, evaluation. When the small case and the huge case agree on the primitives, the primitives are probably right. That convergence is why I think personal-scale agent work transfers directly to the enterprise problem.

**2. Memory went from a feature to a category — and "typed" beat "retrieval."** Two years ago "agent memory" meant a vector store bolted onto a chat log. By 2026 the research and the tooling have converged on something closer to what I bet on early: a *typed, structured* memory the agent updates deliberately — not everything dumped into embeddings and hoped for. The live open problems now are multi-scope memory (per-user / per-agent / per-run, merged at read time) and *restraint* — not letting an agent treat every passing fact as something worth keeping forever. Memory should be an action an agent takes, not always-on plumbing.

**3. The standards settled faster than the patterns.** MCP (the model-to-tool connection) crossed into default status across enterprises and moved under a neutral foundation; A2A is consolidating the agent-to-agent half. The honest position for me: I built Rahat's tool layer *before* those standards settled, which makes the real question concrete rather than abstract — **what migrates to the open protocols, and what stays bespoke?** The interoperability layer is being poured right now, and the interesting work is deciding where to stand on it.

**4. Governance, not capability, is the gate.** The 2026 consensus — and my experience — is that what decides whether an agent deployment survives production isn't model quality or cost; it's governance. Can you say, and enforce, what an agent is allowed to do on its own? Rahat has had a single policy layer that every action passes through from the start. It's the least glamorous primitive and the one that actually determines whether you can let an agent act at all.

**5. The runtime is becoming the product.** Providers are packaging permissions, checkpointing, and memory into the runtime itself — 2026's "agent harness." That's exactly Rahat's next milestone: a shared runtime where the next agent is a prompt plus a tool list, not a hand-written pipeline. The architectural question that follows me: *what makes adding agent number eleven as cheap as agent number one?* Whoever answers that cleanly owns a lot of the next decade.

**6. Local and cloud, not local versus cloud.** Frontier reasoning belongs in the cloud; personal state and policy decisions belong on the device. Rahat routes the hard reasoning out and keeps the memory and the vetoes local — the same split enterprise platforms are landing on, for the same reasons: cost, latency, and privacy. "Your data never leaves your machine" is becoming a product claim that sits *alongside* cloud features, not against them.

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
