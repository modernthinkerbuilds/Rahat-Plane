<div align="center">

# 🪶 Rahat

### A Sovereign Intent Runtime for Personal AI Agents

*Local-first. Heartbeat-driven. Built so the 11th agent costs the same as the 1st.*

[Vision](#-the-vision) · [Architecture](#-architecture-the-three-planes) · [Agents](#-the-agent-mesh) · [What's Shipped](#-whats-actually-shipped) · [Roadmap](#-roadmap-now--next--later) · [PRD](./specs/PRD.md)

</div>

---

## 🌅 The Vision

Most personal AI today is a **chatbot you prompt**. You ask, it answers. You forget, it forgets. You context-switch, it loses the plot.

**Rahat** (Urdu: *رہات* — relief, ease, the lifting of a burden) is what I think comes next:

> An ambient mesh of specialized agents that observe your life, share a single source of truth, and quietly coordinate to close the gap between where you are and where you want to be.

Built on a Mac Mini. Owned by you. Powered by a heartbeat, not a prompt.

---

## ⚡ Why this exists

I'm a Google PM. I have a toddler, a newborn, a CrossFit habit, an 80kg target weight, a 155kg deadlift goal, a guitar I'm learning, family logistics, and a fairly demanding job. The number of small decisions and trivial logging required to "stay on track" with any of it is genuinely exhausting.

Existing tools fail in two ways:

1. **They're reactive.** I have to open them, log into them, prompt them. They never act on their own.
2. **They're siloed.** My fitness app doesn't know I had a heavy lunch. My calendar doesn't know my HRV is low. My grocery app doesn't know I'm cutting weight.

Rahat is the runtime that fixes both. **Cognitive offload, by design.**

---

## 🏗️ Architecture: The Three Planes

Most agent projects collapse the moment you add the second agent — because there's no separation between *what the agent does*, *what it knows*, and *what it's allowed to do*. Rahat is built around three planes that keep these concerns clean.

**In plain English — think of a restaurant:**

| Plane | Restaurant analogy | In Rahat |
|---|---|---|
| **Control Plane** | The menu, the recipes, who's allowed to use which station | The agent registry, The Charter (policies), the tool permissions |
| **Data Plane** | The walk-in, the pantry, the reservation book | The Intent Ledger (SQLite) — vitals, intents, episodic memory |
| **Runtime Plane** | The kitchen on a Friday night, tickets coming in, food going out | The Miya orchestrator + agents executing their `tick()` loops |

```
        Telegram ┐                                          ┌ Notifications
        Sensors  ┘                                          ┘
                 │                                          ▲
                 ▼                                          │
         ┌────────────────┐         ┌──────────────────────┴────┐
         │   The Miya     │         │  The Charter (policy)      │
         │  Orchestrator  │         │  approve · modify · veto   │
         │  (Dakhini wit) │         └──────────────────────▲────┘
         └───────┬────────┘                                │
                 │  hybrid router                          │  every work order
        ┌────────┼────────────────────────────┐            │  passes through
        ▼        ▼              ▼             ▼            │
   ┌─────────┐ ┌──────────┐  ┌──────────┐  ┌──────────┐   │
   │Scientist│ │ Bajrangi │  │  Foodie  │  │  +20     │ ──┘
   │vitality │ │HRV/sleep │  │ nutrition│  │  agents  │
   │  math   │ │recommend │  │  vision  │  │  next    │
   └────┬────┘ └─────┬────┘  └─────┬────┘  └─────┬────┘
        │            │             │             │
        └────────────┴──────┬──────┴─────────────┘
                            ▼
              ┌──────────────────────────────┐
              │  Intent Ledger (SQLite)       │  ◄── ⏱  Heartbeat
              │  ─────────────────────────   │      fires tick()
              │  • intents (the OKRs)        │      on every agent,
              │  • vitals projections        │      every minute
              │  • episodes (life-phases)    │
              │  • work_orders + governance  │
              │  • decisions trace log       │
              └──────────────────────────────┘
```

### What makes this different from a "multi-agent framework"

**1. Sovereignty as infrastructure**
All state lives in `vault/rahat.db` on a local Mac Mini M4. Git-ignored. No cloud syncs of biometric data. Trust is silicon-deep, not Terms-of-Service deep.

**2. Shared state, not shared chat history**
The hardest problem in multi-agent systems isn't reasoning — it's memory. Rahat agents don't pass context to each other through conversations. They read and write the **same SQLite ledger.** When the Scientist updates a weight projection, the Coach sees it on the next tick. When Bajrangi flags low HRV, The Charter blocks intensity pushes from every agent at once.

**3. The Charter as the single chokepoint**
Every outbound action — a Telegram nudge, a calendar invite, a future grocery order — passes through The Charter before it ships. Quiet hours, HRV-red blocks, family priority overrides: written once, applied uniformly across all agents. Adding a new agent doesn't mean re-writing safety rules; the rules are the rules.

**4. Decisions trace, end-to-end**
Every routing call, every tool invocation, every Charter verdict gets a row in `decisions` with a `trace_id`, latency, token count, and outcome. With 20 agents in the mesh, this is the difference between *"I can debug what happened at 9pm last Tuesday"* and *"I have no idea."*

**5. Voice as a layer, not an agent property**
Miya wraps every outbound message through `core/voice.py` — a deterministic Hyderabadi Dakhini phrasebook. Numbers, dates, and structure pass through verbatim. Idempotent (re-dressing is a no-op). Toggleable to neutral mode for debug. The Scientist returns *"Today (Tue May 5): 98 kcal."* — the user sees *"Hau bhai — Today (Tue May 5): 98 kcal."* Adding a new agent doesn't require teaching it Dakhini; the voice is owned at the orchestrator.

---

## 🤖 The Agent Mesh

Five P0 agents in the Now window. Each has a single, scoped responsibility:

### 🦅 The Miya — Orchestrator
The only agent the user talks to. Owns the Telegram inbox, runs a hybrid router (regex shortcuts → Gemini Flash classifier on ambiguity), synthesizes outputs from the rest of the mesh, and dresses every reply through a dedicated **Hyderabadi voice layer** (`core/voice.py`) — Dakhini openers, idempotent, numbers and structure preserved verbatim. Toggleable per-environment via `RAHAT_VOICE=neutral` for debug. Manages notification budget so you only hear from Rahat when something actually needs you.

The Scientist also recognises Dakhini queries directly — *"aaj crossfit hai na"*, *"kya chal ra Miya"*, *"aaj ka workout kya hai"* — routing them to deterministic handlers instead of letting the LLM hallucinate plan details.

### 🧪 The Scientist — Vitality
Owns the trajectory math. Calculates weekly caloric targets using linear-decay against a deadline-bound goal. Reads HRV, sleep, and weight projections; emits work orders for plan changes.

> `Deficit_weekly = ((Weight_current − Weight_target) × 7700) / Weeks_remaining`

### 🏋️ Coach (Fraser) — Performance
Audits CrossFit volume against the Scientist's burn target. Adjusts loading on heavy lifts based on previous sessions and recovery state. Keeps the 155kg deadlift on the runway without overcooking.

### 🩺 Bajrangi — Recovery & Readiness
Reads HRV, sleep duration, and resting heart rate. **Doesn't enforce anything** — that's The Charter's job. Bajrangi *recommends*: "Today is yellow, scale to 70%." "Sleep was 5h, prioritize Zone 2 over CF." His outputs feed The Charter's policy decisions; his voice goes through The Miya.

### 🍳 The Foodie — Nutrition
Vision-based meal audits. Identifies dietary compliance (gluten, red meat) from photos. Pairs with the Scientist's daily targets to nudge what to eat next, not just what you ate.

### ⚖️ The Charter — Policy Plane *(infrastructure, not an agent)*
The chokepoint. Every outbound work order — across every agent — passes through `charter.review()` before execution. Returns `approved`, `modified`, or `vetoed` with a reason. Written as composable Python predicates: quiet hours, HRV-red blocks intensity pushes, family-priority overrides. **Writes to `governance_log` so every decision is auditable.** Bajrangi *advises*; The Charter *enforces*.

---

## 📈 What's actually shipped

This isn't a vaporware repo. Today, on my Mac Mini:

| Component | Status | Notes |
|---|---|---|
| `core/` scaffolding | ✅ Shipped | 8 modules: `io.py`, `agent.py`, `decisions.py`, `charter.py`, `episodes.py`, `miya.py`, `eval.py`, `voice.py` |
| The Scientist (production agent) | ✅ Live | ~2,600 LOC, runs on launchd, replies on Telegram, handles Dakhini routing |
| The Miya (orchestrator) | ✅ Live | Hybrid router (regex → Flash classifier), single-voice-out, Charter-mediated outbound |
| Hyderabadi voice layer | ✅ Live | `core/voice.py` — idempotent, numbers/structure preserved, neutral-mode toggle for debug |
| The Charter (policy plane) | ✅ Live | Quiet hours, HRV-red, external-veto policies wired in; writes to `governance_log` |
| Decisions trace log | ✅ Live | Every routing call, tool invocation, and verdict logged with `trace_id`, latency, outcome |
| Episodic memory | ✅ Live | `episodes` + `episode_notes` tables; 6-line Python API (`open` / `close` / `note` / `get` / `list_open` / `find`) |
| Eval harness | ✅ Live | **316 cases passing** across 3 independent paths (legacy router / agent wrapper / extended 7-dimension) |
| Apple Watch ingestion | ✅ Live | HRV + active calories pumped into the ledger via local FastAPI bridge |
| Bajrangi, Coach (Fraser), Foodie | 🚧 Next | Each: ~3–5 hours on the new agent base class |
| Curriculum (toddler/newborn) | 🚧 Next | Episodic memory specifically promoted to Now to support life-phase tracking |
| Voyager, Barista, Annapurna | 🔜 Later | Concierge-class agents — wait for profile store + semantic memory layer |

---

## 🗺️ Roadmap: Now / Next / Later

The roadmap is honest about what scaffolding is worth building today vs. what should wait until a real use case forces it.

### Now (months 1–6) — scaling to ~20 agents

The forcing function: by month 6 the mesh is 20 deep. Anything that has to be done 20 times must be cheap.

- ✅ Single voice (Miya owns the Telegram inbox; agents never speak directly to the user)
- ✅ Shared tools (every agent imports `core/io.py` — no boilerplate redefinition)
- ✅ The Charter as a single chokepoint with `governance_log` audit trail
- ✅ Decisions trace log (debug + replay surface)
- ✅ Generalized eval harness (every agent ships a `cases.yaml`)
- ✅ Episodic memory (Scientist's weight cycles, Coach's training blocks, Curriculum's newborn phases)
- 🚧 Cut launchd over from Scientist-as-entrypoint to Miya-as-entrypoint

### Next (months 6–12) — when concierge agents arrive

Triggered by specific use cases, not the calendar. Each item below is added only when its consuming agent lands.

- **Profile store** — `profile_facts(subject, key, value, confidence, source, valid_from, valid_to)`. Pulled in when Foodie/Voyager need persistent preferences ("vegetarian, no red meat," "loves authentic-family-run spots").
- **Semantic memory** (`sqlite-vss`) — for retrieval over saved bookmarks, photos, scraped pages. Triggered by the first concierge agent that needs free-text search.
- **Event log + projection rebuild** — when "replay last week through a new Miya routing strategy" becomes a real ask.
- **Embedding-based agent retrieval** — when 20 agents have overlapping descriptions and the Flash classifier starts misrouting.

### Later (12+ months) — when mobile and multi-user are real

- **FastAPI gateway over Tailscale** — read-only projections served to a thin mobile cockpit.
- **Mobile client** (SwiftUI or React Native) — Sign-in-with-Apple, APNs push, offline read cache. Mac Mini stays the brain.
- **Multi-tenant** — partner and toddler get their own subjects. (Cheap insurance: `subject_id` columns added to projections in Now.)
- **Skill manifests + true registry** — only worth it if external/third-party agents plug in.

---

## 📐 Product Requirements & ADRs

- **Full PRD:** [`/specs/PRD.md`](./specs/PRD.md) — agent personas, deterministic logic, intent-ledger schema, the "Universal Sniff Test."
- **ADR-001:** [`/specs/ADR-001-control-plane.md`](./specs/ADR-001-control-plane.md) — the three-plane architecture review, decision rule for promoting items from Next to Now, hour-level effort estimates.

---

## 🛠️ Tech Stack

| Layer | Tool | Why |
|---|---|---|
| **Orchestration** | [OpenClaw](https://openclaw.ai) + custom Miya | Heartbeat-driven, async, single Telegram inbox owner |
| **State** | SQLite (+ JSON1) | One file, ACID, zero ops; vector layer added in Next |
| **Compute** | Mac Mini M4 | Quiet, sovereign, always-on |
| **Intelligence** | Gemini Flash + Claude | Flash for routing classification, Claude for deeper reasoning |
| **Voice** | `core/voice.py` (deterministic phrasebook) | Hyderabadi Dakhini, idempotent, zero per-message LLM cost |
| **Interface** | Telegram | Native multimodal, zero app-store friction |
| **Sensors** | HealthKit (Apple Watch), Calendar, CSV | Ambient, passive, opt-in |
| **Daemonization** | macOS `launchd` | Resilient KeepAlive, native logging |
| **Testing** | Hermetic eval harness | 316 cases / 3 paths, runs in <30s |

---

## 📓 The Build Journey

I shipped the first version of Rahat during parental leave with a newborn — naps and bedtime were the build windows. The 90-day arc, documented in commits and Discussions:

- **Month 1 — Architecture:** Why three planes, why local-first, why "shared state, not shared chat history" beats RAG-over-chat.
- **Month 2 — Agents:** Building each persona, the Miya cutover, Charter policy decisions, the regression bugs and how the eval suite caught them.
- **Month 3 — Scaling:** Adding agents 6 through 20 against the new contract. Hour estimates per agent, what scaffolding paid off.

Follow along via commits, the [PRD](./specs/PRD.md), the [ADR](./specs/ADR-001-control-plane.md), and [Discussions](../../discussions).

---

## 🚦 Status

Rahat is a **personal build**, not a product. The architecture, PRDs, and ADRs are public. The agent personas, vault data, and runtime configuration are private (and will stay that way — that's the whole point of "sovereign").

If you're building something similar and want to compare architectures, open a Discussion. If you're a PM thinking through the agentic future, the build journal in commits is for you.

---

## 📜 License & credit

Architecture and documentation: MIT.
Built on top of [OpenClaw](https://github.com/openclaw/openclaw) — credit and respect to that team.

---

<div align="center">

*"The future of personal AI isn't a smarter chatbot. It's a quieter life."*

— Building Rahat in public

</div>
