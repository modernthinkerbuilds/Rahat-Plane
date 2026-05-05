<div align="center">

# 🪶 Rahat

### A Sovereign Intent Runtime for Personal AI Agents

*Local-first. Heartbeat-driven. Built for the agentic decade.*

[Vision](#-the-vision) · [Architecture](#-architecture) · [The Agent Mesh](#-the-agent-mesh) · [Build Journey](#-the-build-journey) · [PRD](#-product-requirements)

</div>

---

## 🌅 The Vision

Most personal AI today is a **chatbot you prompt**. You ask, it answers. You forget, it forgets. You context-switch, it loses the plot.

**Rahat** (Urdu: *رہات* — relief, ease, the lifting of a burden) is what I think comes next:

> An ambient mesh of specialized agents that observe your life, share a single source of truth, and quietly coordinate to close the gap between where you are and where you want to be.

Built on a Mac Mini. Owned by you. Powered by a heartbeat, not a prompt.

---

## ⚡ Why this exists

I'm a PM by day. By night, I have a toddler, a CrossFit habit, a 80kg target weight, a 155kg deadlift goal, a guitar I'm learning, family logistics, and a fairly demanding job. The number of small decisions and trivial logging required to "stay on track" with any of it is genuinely exhausting.

Existing tools fail in two ways:

1. **They're reactive.** I have to open them, log into them, prompt them. They never act on their own.
2. **They're siloed.** My fitness app doesn't know I had a heavy lunch. My calendar doesn't know my HRV is low. My grocery app doesn't know I'm cutting weight.

Rahat is the runtime that fixes both. **Cognitive offload, by design.**

---

## 🏗️ Architecture

```
                    ┌─────────────────────────┐
                    │      The Miya           │
                    │  (Orchestrator + UI)    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Intent Ledger (SQLite)│
                    │  ── shared state bus ── │
                    └────────────┬────────────┘
                                 │
        ┌──────────┬─────────────┼─────────────┬──────────┐
        ▼          ▼             ▼             ▼          ▼
   ┌─────────┐ ┌────────┐  ┌──────────┐  ┌──────────┐ ┌────────┐
   │Scientist│ │ Coach  │  │ Foodie   │  │ Voyager  │ │Bajrangi│
   │(Vitality│ │(CrossFit│  │(Nutrition│  │(Logistics│ │(Safety │
   │  + math)│ │  + WOD)│  │  + vision│  │  + travel│ │  veto) │
   └─────────┘ └────────┘  └──────────┘  └──────────┘ └────────┘
        ▲          ▲             ▲             ▲          ▲
        └──────────┴─────────────┼─────────────┴──────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Heartbeat (15-min)    │
                    │  ── OpenClaw daemon ──  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Sensors (passive)     │
                    │  Watch · Calendar · CSV │
                    └─────────────────────────┘
```

### Three architectural pillars

**1. Sovereignty as infrastructure**
All state lives in `vault/rahat.db` on local M4 silicon. Git-ignored. No cloud syncs of biometric data. Trust is silicon-deep, not Terms-of-Service deep.

**2. State-level continuity**
Every agent reads from and writes to the same SQLite ledger. When the Scientist flags low HRV, the Foodie knows to suggest anti-inflammatory meals, and the Coach knows to scale tomorrow's load. **No agent operates in isolation.**

**3. Plug-and-play extensibility**
Adding a new agent is a Markdown spec + a SQL contract. The runtime doesn't care if it's a `Coffee Agent` or a `Tax Agent` — same interface, same ledger.

---

## 🤖 The Agent Mesh

Five P0 agents currently in the runtime:

### 🦅 The Miya — Orchestrator
The only agent the user talks to. Synthesizes outputs from the rest of the mesh and delivers them with Dakhini-Hyderabadi wit + PM brevity. Manages notification budget so you only hear from the system when something actually needs you.

### 🧪 The Scientist — Vitality
Owns the trajectory math. Calculates weekly caloric targets using linear-decay against a deadline-bound goal. Treats your body like a roadmap with an OKR.

> `Deficit_weekly = ((Weight_current − Weight_target) × 7700) / Weeks_remaining`

### 🏋️ Coach (Fraser) — Performance
Audits CrossFit volume against the Scientist's burn target. Adjusts loading on heavy lifts based on previous sessions and recovery state. Keeps the 155kg deadlift on the runway without overcooking.

### 🩺 Bajrangi — Governance
The safety veto. Has root authority to mute every other agent based on HRV, sleep deficit, or family priority. The reason Rahat doesn't burn me out.

### 🍳 The Foodie — Nutrition
Vision-based meal audits. Identifies dietary compliance (gluten, red meat) from photos. Pairs with the Scientist's daily targets to nudge what to eat next, not just what you ate.

---

## 📐 Product Requirements

The full PRD lives in [`/specs/PRD.md`](./specs/PRD.md) — including agent personas, deterministic logic, intent-ledger schema, and the "Universal Sniff Test" criteria for scaling beyond a personal build.

**Current PRD highlights:**

- ✅ State-machine ledger (no agent collisions across 20+ specialists)
- ✅ Sub-100ms latency on the OpenClaw heartbeat (M4-optimized)
- ✅ Zero-input ambient ingestion (Apple Watch → SQLite → agents)
- ✅ Passive vision audits via multimodal LLMs (Gemini Flash / Claude)
- 🔜 Telegram Mini App (TMA) for the read-only cockpit view
- 🔜 Vector layer (`sqlite-vss`) for semantic retrieval across agent memories

---

## 🛠️ Tech Stack

| Layer | Tool | Why |
|---|---|---|
| **Orchestration** | [OpenClaw](https://openclaw.ai) | Heartbeat-driven, async, channel-flexible |
| **State** | SQLite (+ JSON1, vss) | One file, ACID, zero ops, Mongo-flexible |
| **Compute** | Mac Mini M4 | Quiet, sovereign, always-on |
| **Intelligence** | Claude / Gemini APIs | Best-in-class reasoning + vision |
| **Interface** | Telegram | Native multimodal, zero app-store friction |
| **Sensors** | HealthKit, Calendar, CSV | Ambient, passive, opt-in |

---

## 📓 The Build Journey

I'm documenting this build in public, in this repo. The 90-day arc:

- **Month 1 — Architecture:** Why a control plane, why local-first, how the agent mesh shares state
- **Month 2 — Agents:** Building each persona, how they negotiate, why Bajrangi has veto power
- **Month 3 — Results:** Performance against the trajectory, what worked, what I'd architect differently

Follow along via commits, the [PRD](./specs/PRD.md), and [Discussions](../../discussions).

---

## 🚦 Status

Rahat is a **personal build**, not a product. The architecture and PRDs are public. The agent personas, vault data, and runtime configuration are private (and will stay that way — that's the whole point).

If you're building something similar and want to compare architectures, reach out. If you're a PM thinking through the agentic future, the build journey is for you.

---

## 📜 License & credit

Architecture and documentation: MIT.
Built on top of [OpenClaw](https://github.com/openclaw/openclaw) — credit and respect to that team.

---

<div align="center">

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

— Building Rahat in public

</div>
