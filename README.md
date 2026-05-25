<div align="center">

# 🪶 Rahat

### A control plane for AI agents — built at human scale

*A local-first system that gives a mesh of personal AI agents one shared memory, one rulebook, and one orchestrator — so adding the next agent is configuration, not a rebuild.*

[What this is](#what-this-is) · [Why it matters](#why-this-matters-beyond-my-house) · [The four problems](#the-four-problems-a-control-plane-has-to-solve) · [How it works](#how-it-works) · [What's shipped](#whats-actually-shipped) · [The roadmap](#the-roadmap--an-everyday-mesh) · [Architecture docs](#specs--decision-records)

</div>

---

## What this is

Most AI today is a **chatbot you prompt**: you open it, it answers, it forgets you the moment you close the tab. Each new assistant starts from zero, and nothing one of them knows is shared with another.

**Rahat is the opposite — and the agents are only the visible part.** The real thing being built is the **habitat** underneath them: one shared memory of your life, one rulebook, one runtime, one orchestrator that the whole fleet lives in. Because they share that environment, they remember you and work together — helping with the messy, real moments, not just one-off questions.

In platform terms, that habitat is a **control plane** for agents: the layer that decides what each agent knows, what it's allowed to do, and how they coordinate. It's the same idea at two scales — a warm word for a household, a technical one for a company running a fleet of agents. Rahat is a working version of it, running locally on a Mac Mini, used daily through one person's life. The agents on top happen to be about training and recovery today; the habitat doesn't care what they're about.

> **Rahat** (Urdu: *رہات* — relief, ease, the lifting of a burden) is a bet on one idea: **AI gets genuinely useful when it remembers your life and coordinates — not just when the model gets smarter.** The model is the engine; the habitat around it is the car.

---

## Why the first agents are about training — and why that's not the point

If you skim the live code, you'll see agents about workouts and recovery, and you might conclude this is a fitness project. It isn't.

I needed a **real, unforgiving domain** to prove the architecture on, and my own life was the most honest test available — a domain where the agent is wrong in ways I'll actually notice, every day. So the first agents coach training and recovery. They're the **proving ground, not the product.**

What's actually being built is **domain-agnostic**: the shared memory, the policy rulebook, the orchestration, the evaluation harness. None of that knows or cares that the first agents happen to be about fitness. The roadmap below is a 21-agent *everyday* mesh — planning the family weekend, getting a gift right, sorting out dinner, planning a trip. Same control plane underneath; different lens on top.

---

## Why this matters beyond my house

Here's the bridge that makes this more than a personal toy.

The moment *any* team goes from running one agent to running a fleet, they hit the exact problems Rahat is built around: *How do these agents share what they know? Who decides what an agent is allowed to do on its own? How do you keep them coordinated, debuggable, and within budget? How do you even know an agent is any good?*

Those aren't personal-assistant questions — they're **control-plane questions**, and every company deploying agents is wrestling with them now. "Agent control plane" became *the* enterprise-AI category in 2026 ("Kubernetes for agents"). The striking part: a system for one household and a platform for thousands converge on the *same* primitives — shared memory, central governance, orchestration, evaluation. When the small case and the huge case agree, the primitives are probably right — and solving them at human scale is an honest way to understand them anywhere.

To be clear about scale: **Rahat is a personal-scale build, not enterprise software.** The claim isn't that it's a product for companies — it's that the *problems* are the same class, and solving them at human scale is a real way to understand them.

---

## The four problems a control plane has to solve

Each one is stated plainly, then how Rahat handles it, then why it matters once you have more than one agent.

### 1. Memory — agents that actually remember you

**The problem:** chat history is too raw and too short-lived to rely on. An agent that "remembers" by re-reading the transcript forgets your commitments within an hour. (This is exactly how Rahat's first agent failed, repeatedly, until the memory layer existed.)

**What Rahat does:** a shared, typed memory layer that every agent reads from and writes to — facts, ongoing commitments, preferences (which fade if you stop reinforcing them), and a searchable long-term archive. A nightly job compacts it so it stays sharp instead of bloating. Memory is something an agent *does* (decides when to remember, recall, or forget), not always-on plumbing.

**Why it matters at scale:** when one agent learns something, the others should benefit. Shared memory is what turns a pile of separate bots into a coordinated system — and it's the single thing most multi-agent setups get wrong.

### 2. Governance — one rulebook, enforced once

**The problem:** five agents that can each take actions will each reinvent the rules — quiet hours, what needs confirmation, what's simply off-limits — three different, conflicting ways.

**What Rahat does:** a single policy layer (the **Charter**) that every action-taking tool must pass through before it runs. Rules are written once and applied to every agent uniformly, with an audit log of every decision.

**Why it matters at scale:** governance is the difference between a demo and something you can actually let act on your behalf. A central policy chokepoint is how you keep a growing fleet safe and accountable — and it barely exists as a first-class primitive in commercial agent platforms yet.

### 3. Orchestration — many agents, one coherent experience

**The problem:** the user shouldn't have to know which agent to talk to, and agents shouldn't talk over each other.

**What Rahat does:** a single orchestrator (**Miya**) owns the conversation, routes each request to the right agent, lets agents hand work to each other, and speaks back to you in one consistent voice. Adding an agent doesn't change the interface.

**Why it matters at scale:** routing and a single coherent surface are what make a fleet feel like one assistant instead of twenty disconnected ones.

### 4. Evaluation & reliability — knowing it's good, and proving it

**The problem:** "the chat felt fine today" is not a quality metric once agents take real actions and persist state.

**What Rahat does:** a **deterministic shell around an LLM core** — the model proposes; tested, deterministic code does the math, enforces the rules, and executes. Every routing decision, tool call, and policy verdict is logged with a trace, latency, and cost. Every bug fix ships with a regression test, and a pre-push gate blocks any change that breaks the suite.

**Why it matters at scale:** this is how you debug *"what happened at 9pm Tuesday"* across twenty agents instead of shrugging — and how you change the architecture without silently breaking behavior.

---

## How it works

**The governing principle ([ADR-011](./specs/ADR-011-deterministic-shell-llm-core.md)):**

> **Deterministic shell, LLM core.** The substrate — state, math, routing, persistence, safety vetoes — is deterministic and tested. The intelligence — understanding intent, structure, phrasing, constraints — is the model's job. *The LLM proposes; deterministic guards dispose.*

This keeps the system both smart and trustworthy: the model never does the arithmetic or invents a number, because numbers come from tools. It just decides which tools to call.

**The agent contract.** Every agent is defined the same way:

```
Agent = { name, description, system_prompt, tools[] }
```

The model returns a structured action plan over a set of typed tools; deterministic code validates and executes it. That uniformity is the whole bet: once the runtime is shared, the *next* agent is mostly a prompt plus a tool list — configuration, not a new pipeline.

**The layers:**

| Layer | What lives here | Plain-English analogy (a restaurant) |
|---|---|---|
| **Control** | The Charter (policy) + the registry of what each agent can do | The kitchen's rules and who's allowed at which station |
| **Data** | Shared memory + a ledger of intents and decisions | The pantry, the reservation book, and the chef's notebook of regulars |
| **Runtime** | Miya (orchestrator), the reasoning loop, tool dispatch, voice | The Friday-night line: tickets, the expediter, the pass |
| **Adapter** | Each agent's thin connector to the shared memory | Each chef's personal mise en place |

```
        you (Telegram, sensors) 
                 │
                 ▼
        ┌──────────────────┐        ┌────────────────────────────┐
        │      Miya        │        │   The Charter (policy)     │
        │  orchestrator /  │  ────► │  approve · modify · veto   │  ◄── every
        │     router       │        │       + audit log          │      action
        └────────┬─────────┘        └────────────────────────────┘      passes
                 │                                                       through
     ┌───────────┼───────────────┐
     ▼           ▼               ▼
 ┌───────┐  ┌─────────┐     ┌──────────┐
 │ Kobe  │  │ Fraser  │ ... │  + more  │
 └───┬───┘  └────┬────┘     └────┬─────┘
     │           │               │
     ▼           ▼               ▼
 ┌──────────────────────────────────────────┐
 │           Shared memory layer             │
 │  events · facts · preferences · archive   │
 │      + intent & decision ledger           │
 │            (local SQLite)                  │
 └──────────────────────────────────────────┘
                 ▲
       nightly consolidation (summarize · decay · archive)
```

Six standalone SVG diagrams live in [`/specs/diagrams/`](./specs/diagrams/) (memory, the reasoning loop, mesh extensibility, the roadmap).

---

## What's actually shipped

Honest status. This is a real running system, not a slide.

| Component | Status | What it is |
|---|---|---|
| **Miya** — orchestrator | ✅ Live | Routing, single voice out, cross-agent hand-off |
| **The Charter** — policy layer | ✅ Live | Every action-taking tool checks it first; decisions audited |
| **Shared memory layer** | ✅ Live | Events, facts, decaying preferences, semantic archive + nightly consolidation |
| **Kobe** — first full agent | ✅ Live | Real daily use; full memory + a typed tool catalog; the proving-ground agent |
| **Fraser** — second full agent | ✅ Live | Built on the shared runtime — the proof the pattern generalizes |
| **Huberman / Bajrangi** — stubs | ✅ Shipped | Minimal agents that prove a *new* agent reuses the memory layer cleanly |
| **Deterministic shell / LLM core** | ✅ Live | Model proposes, tested code disposes; full decision trace per turn |
| **Test discipline** | ✅ Live | Five-layer suite (unit / contract / eval / adversarial / regression); every fix adds a regression test; a pre-push gate blocks red changes |
| **Frictionless setup** | ✅ Live | `bootstrap.sh` + `.env.example`; clone to a green test suite in one command, no hardcoded paths |
| The everyday mesh (Genie, Santa, …) | 🔜 Roadmap | Not built yet — the next agents, each ~1 day on the shared contract |

**Three working agents today.** The goal isn't twenty agents for their own sake — it's to test one hypothesis: *can the control plane make the next agent cheap?* The build log will say whether it's working.

---

## The roadmap — an everyday mesh

This is where the control plane points: away from fitness, toward the ordinary moments anyone recognizes. Each new agent is meant to onboard in roughly a day on the shared contract — same memory, same rulebook, same runtime, different lens.

| Agent | The everyday job | Status |
|---|---|---|
| **Genie** | Plans the family weekend around everyone's real energy | 🔜 Next |
| **Santa** | Gets the gift right because it remembers what people love | 🔜 Next |
| **Ramsay** | Figures out dinner from what's in the kitchen and who's eating | 🔜 Next |
| **Montessori** | Keeps a newborn and a toddler in the picture | 🔜 Next |
| **Disney** | Plans a weekend day the kids will actually love | 🔜 Next |
| **Polo / Bourdain** | Plans a trip, then guides you once you're there | 🔜 Later (travel-triggered) |
| *(+ a dozen more across calendar, pantry, coffee, music)* | | 🔜 Later |

The naming convention: each agent is named after a renowned figure whose life embodies its job. It keeps the cast memorable — and keeps me honest that an agent should be as good at its one thing as its namesake was.

---

## Specs & decision records

Living documentation in [`/specs/`](./specs/):

- [`ADR-011-deterministic-shell-llm-core.md`](./specs/ADR-011-deterministic-shell-llm-core.md) — the governing principle: deterministic shell, LLM core
- [`ADR-001-rahat-control-plane.md`](./specs/ADR-001-rahat-control-plane.md) — the control-plane decision and the Now / Next / Later trajectory
- [`ARCHITECTURE.md`](./specs/ARCHITECTURE.md) — the current target architecture
- [`MEMORY-AND-STATE-ARCHITECTURE.md`](./specs/MEMORY-AND-STATE-ARCHITECTURE.md) — the memory contract
- [`SOTA-AGENT-ARCHITECTURE-REVIEW.md`](./specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md) — the review that motivated the memory layer
- ADRs 002–012 — storage conventions, the agent file pattern, budget enforcement, routing, delegation, the shared tool-calling runtime

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| **Reasoning** | Frontier LLM + structured tool calling | The model orchestrates; tools enforce the math, so numbers are never hallucinated |
| **State & memory** | Local SQLite | One file, transactional, zero ops; a semantic layer is added only where it earns its place |
| **Compute** | Mac Mini (Apple Silicon) | Quiet, always-on, and the data never leaves the machine |
| **Interface** | Telegram | Native, multimodal, no app-store friction |
| **Sensors** | HealthKit, calendar (opt-in) | Ambient and passive, so the system can act without being prompted |
| **Always-on** | macOS `launchd` | Production-grade daemonization with auto-restart |
| **Quality** | Hermetic five-layer test suite | Runs locally in seconds; gates every change |

Local-first by design: state lives on the machine, not in the cloud. A frontier model handles novel reasoning; the personal state and the policy decisions stay local. That cloud-plus-local split is, not coincidentally, the same shape enterprise agent platforms are converging on.

---

## Status & who's building it

Rahat is a **personal build, not a product.** The architecture, decision records, and diagrams are public; the personal data and runtime configuration are private and stay that way — that's the point of "local-first."

Built by a Bay Area product manager with a toddler, a newborn, and a long-running interest in where agent platforms are going. The first version shipped during parental leave, in the gaps between naps. If you're building something similar and want to compare architectures, open a Discussion.

Built on top of [OpenClaw](https://github.com/openclaw/openclaw) — credit and respect to that team. Architecture and documentation: MIT.

---

<div align="center">

*"The future of personal AI isn't a smarter chatbot. It's a system that remembers your life — and a quieter you."*

— Building Rahat in public

</div>
