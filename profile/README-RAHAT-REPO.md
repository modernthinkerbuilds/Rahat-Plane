<div align="center">

# 🪶 Rahat

### A Sovereign Intent Runtime for Personal AI Agents

*Local-first. Memory-aware. Model-first reasoner over a deterministic core.*

[Vision](#-the-vision) · [Architecture](#-architecture-four-layers) · [Memory](#-memory-the-layer-that-makes-agents-trustworthy) · [Reasoner](#-the-model-first-reasoner) · [Agents](#-the-agent-mesh) · [What's Shipped](#-whats-actually-shipped) · [Roadmap](#-roadmap-now--next--later) · [Diagrams](#-architecture-diagrams)

</div>

---

## 🌅 The Vision

Most personal AI today is a **chatbot you prompt**. You ask, it answers. You forget, it forgets. You context-switch, it loses the plot. You commit to something and 30 minutes later it lectures you about the opposite.

**Rahat** (Urdu: *رہات* — relief, ease, the lifting of a burden) is what I think comes next:

> An ambient mesh of specialized agents that observe your life, **share a single source of truth and a single memory substrate**, run a model-first reasoner over a deterministic tool catalog, and quietly coordinate to close the gap between where you are and where you want to be.

Built on a Mac Mini. Owned by you. Powered by a heartbeat, not a prompt.

---

## ⚡ Why this exists

I'm a Google PM. I have a toddler, a newborn, a CrossFit habit, an 80kg target weight, a 155kg deadlift goal, a guitar I'm learning, and a fairly demanding job. The number of small decisions and trivial logging required to "stay on track" with any of it is genuinely exhausting.

Existing tools fail in three ways:

1. **They're reactive.** I have to open them, log into them, prompt them. They never act on their own.
2. **They're siloed.** My fitness app doesn't know I had a heavy lunch. My calendar doesn't know my HRV is low.
3. **They forget.** I commit to something, and they don't remember the next time I open them.

Rahat is the runtime that fixes all three. **Cognitive offload, by design.**

---

## 🏗️ Architecture: Four Layers

The original three-plane model held until I hit a wall: agents need to *actively manage their own memory* the way a human coach remembers your goals, your commitments, and last week's conversation. Memory isn't passive infrastructure — it's an agent-facing responsibility. So Rahat now has four conceptual layers:

| Layer | What lives here | Implementation |
|---|---|---|
| **Control** | Charter (policy enforcement), agent capability registry | `core/charter.py`, `core/agent.py` |
| **Data** | Intent ledger, decisions trace, **memory substrate** (4 tiers) | `vault/rahat.db` (SQLite) |
| **Runtime** | Miya orchestrator, reasoner loop, tool dispatch, voice layer | `core/miya.py`, `core/voice.py`, agents' `reasoner.py` |
| **Agent Adapter** | Per-agent context assemblers + state extractors over shared memory | `agents/<name>/memory.py` |

**Plain-English version (the restaurant analogy):**

| Plane | Restaurant | Rahat |
|---|---|---|
| **Control** | Recipes, kitchen rules, who can use which station | Charter + agent registry |
| **Data** | Pantry, walk-in, reservation book, **the chef's notebook of regulars** | Intent ledger + memory substrate |
| **Runtime** | Friday-night kitchen, tickets, expediter | Miya + reasoner + tools |
| **Adapter** | Each chef's personal mise en place + tasting notes | Per-agent memory.py |

```
        Telegram ┐                                          ┌ Notifications
        Sensors  ┘                                          ┘
                 │                                          ▲
                 ▼                                          │
         ┌────────────────┐         ┌──────────────────────┴────┐
         │   The Miya     │         │  The Charter (policy)      │
         │  Orchestrator  │         │  approve · modify · veto   │
         │  Supervisor    │         └──────────────────────▲────┘
         │  Capability    │                                │
         │  registry      │                                │
         └───────┬────────┘                                │
                 │                                         │
        ┌────────┼────────────────────────────┐            │
        ▼        ▼                            ▼            │
   ┌─────────┐ ┌──────────┐             ┌──────────┐      │
   │Scientist│ │ Bajrangi │     ...     │  +20     │ ─────┘
   │ + reasoner│ + reasoner│             │  agents  │   write tools
   │ +25 tools │ + tools   │             │          │   pass through
   └────┬────┘ └─────┬────┘             └─────┬────┘   Charter
        │            │                        │
        │  context   │                        │
        │  assembler │  state extractor       │
        ▼            ▼                        ▼
  ┌──────────────────────────────────────────────────────┐
  │            Memory Substrate (universal)              │
  │  events · entities · threads · prefs · archival     │
  │  + intent ledger + decisions trace                   │
  │  ────────────────────────────────────────────────   │
  │            vault/rahat.db (SQLite)                  │
  └──────────────────────────────────────────────────────┘
                          ▲
                          │
          ┌───────────────┴───────────────┐
          │  Sleep-time consolidation     │
          │  03:00 cron · summarize       │
          │  decay · archive · GC         │
          └───────────────────────────────┘
```

### What makes this different from a "multi-agent framework"

**1. Sovereignty as infrastructure.** All state lives in `vault/rahat.db` on a local Mac Mini M4. Git-ignored. No cloud syncs of biometric data. Trust is silicon-deep.

**2. Shared memory, not shared chat history.** RAG over conversation logs is brittle. Rahat's substrate has six first-class primitives every agent reads/writes (events, entities, threads, prefs, archival, relationships). When the Scientist commits a goal, the Foodie sees it on next turn. When Bajrangi flags low HRV, the Scientist's reasoner downgrades intensity automatically.

**3. The Charter as the single chokepoint.** Every write-tool — across every agent — calls `charter.check()` first. Quiet hours, HRV-red blocks, family-priority overrides: written once, applied uniformly. Writes to `governance_log` for audit.

**4. Decisions trace, end-to-end.** Every routing call, tool invocation, charter verdict, and reasoner hop gets a row in `decisions` with `trace_id`, latency, tokens, cost, outcome. With 20 agents, this is the difference between *"I can debug what happened at 9pm Tuesday"* and *"no idea."*

**5. Voice as a layer, not an agent property.** Miya wraps every outbound message through `core/voice.py` — a deterministic Hyderabadi Dakhini phrasebook. Idempotent. Numbers/structure preserved verbatim. Adding a new agent doesn't require teaching it Dakhini.

**6. Model-first reasoner over a deterministic tool catalog.** Each agent runs a Gemini 2.5 Flash reasoning loop with a registered tool catalog (the Scientist has 25 tools). The model decides what to call; tools execute deterministically; results return; reply composed. Regex routing remains as a fallback when the API is unreachable.

---

## 🧠 Memory: the layer that makes agents trustworthy

> *The cutting-edge insight: memory operations are tools the agent calls, not infrastructure that's always-on. The agent learns when to remember, when to forget, when to recall.* — internal SOTA review

### Why memory had to be built

The Scientist was supposed to remember commitments. Instead, every message was a fresh start. I'd say *"I'll do 7,000 kcal of burn this week"* (a real commitment), and an hour later the bot would suggest a plan that ignored it. I'd commit to *"hammer tier for 2 weeks,"* and the next day it would lecture me about recovery — even though I'd just said I wanted to push.

After ten rounds of patches (60-minute lookbacks, re-reading chat history, hardcoding "don't lecture after commit"), the root cause was undeniable: **the system had no memory architecture.** Chat history is too raw, too short-lived, and too unstructured to be reliable. The agent needed to actively manage its own state — like a human coach.

### The four-tier substrate

```
┌─────────────────────────────────────────────────────────┐
│  Working memory (recall)                                │
│  memory_events — append-only firehose, ~5K rows/day    │
│  Every meaningful event: messages, tools, vitals       │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Core memory (entities)                                 │
│  memory_entities — first-class objects with lifecycle  │
│  Scientist: goal, plan, commitment, tier_change        │
│  Bajrangi: recovery_protocol, sleep_concern, hrv_window│
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Semantic memory (preferences)                          │
│  memory_preferences — sticky k/v, confidence decay     │
│  "preferred_lunch=paneer+jowar" reinforced per turn    │
│  decays 5%/week without reinforcement                  │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  Archival memory (long-term)                            │
│  memory_archival — text + 768-d embeddings             │
│  Gemini text-embedding-004; cosine search              │
│  "what did I tell you about Tokyo 3 months ago"        │
└─────────────────────────────────────────────────────────┘
```

Plus `memory_threads` (conversation topics) and `memory_relationships` (entity-to-entity links — including cross-agent: a Scientist commitment can link to a Bajrangi recovery protocol).

### How agents use it (the adapter pattern)

Each agent at `agents/<name>/memory.py` exports just two functions:

```python
def assemble_context(db_path=None) -> str:
    """Pure-Python, deterministic, no LLM. Returns a [state] block:
       [Today: Friday May 8] [Active goal: 198lb by 2026-05-22]
       [Commitments: hammer tier × 2wk] [Plan: 3-CF, 1-Z2]
    """

def extract_state(user_msg, bot_reply, db_path=None) -> None:
    """Gemini Flash JSON-mode parses the (msg, reply) pair.
       Writes new entities/commitments/preferences back to substrate.
       Costs ~$0.0001/turn.
    """
```

The substrate is universal; adapters are thin (Scientist's is ~365 LOC, Bajrangi's stub is ~110 LOC). **Every future agent — Curriculum, Foodie, Voyager — onboards in ~1 day.**

### Sleep-time consolidation

A nightly cron at 03:00 runs `scripts/memory_consolidate.py`:

- Summarizes threads inactive >24h
- Decays preferences not reinforced in 7 days
- Archives entities past `valid_until`
- GCs events older than 365 days
- Purges unused archival entries

Without this, the substrate would bloat to junk. With it, the data plane stays sharp.

---

## 🔁 The Model-First Reasoner

The Scientist used to be a regex router with 25 hardcoded handlers and an LLM fallback for unmatched messages. It failed on five distinct classes of bug — multi-clause questions, ad-hoc constraints, week-so-far reasoning, multi-source composition, the Dakhini-English mix. The intuition *"regex for cheap determinism, LLM for free-form fallback"* aged poorly. Modern Gemini 2.5 Flash with structured tool calling doesn't hallucinate math when the math comes from tools.

### The new flow (per inbound message)

1. **Assembler.** Memory adapter builds the `[state]` block (~5ms, pure Python).
2. **Reasoner.** Gemini 2.5 Flash receives `[state] + user_msg + 25-tool catalog + system prompt`.
3. **Loop (≤8 hops).** Model emits `function_call` parts; `tools.dispatch()` executes the tool deterministically (read or charter-gated write); results return as `function_response` parts; model composes the reply.
4. **Extractor.** Flash JSON-mode parses (msg, reply) → new entities/prefs.
5. **Voice.** `core/voice.py` dresses the reply with a Dakhini opener.
6. **Send.** Telegram splitter chunks at <4000 chars.
7. **Trace.** Every hop logged to `decisions` with tokens + cost.

### The tool catalog (25 tools, 3 categories)

| Category | Examples |
|---|---|
| **Read (cheap, safe)** | `get_week_burn`, `get_today_target`, `get_weight_timeline`, `get_eligible_cf_days`, `get_blacklist`, `get_missed_workouts`, `get_hrv_status`, `get_recent_context` |
| **Write (charter-gated)** | `propose_replan`, `commit_picks`, `tolerate_movement`, `log_weight`, `swap_day`, `set_recovery_tier` |
| **Memory (used by extractor)** | `recall_search`, `archival_search`, `archival_insert`, `list_active`, `upsert_pref` |

### Cost & resilience

- **Cost ledger:** `core/cost.py` is the single source of truth. Gemini 2.5 Flash: `$0.30/M in, $2.50/M out`. Cost per turn: **~$0.001**. Latency: **~2–6s** (1–3 hops typical).
- **Two-tier fallback:** Gemini 5xx or no API key → `legacy_route()` (regex dispatcher, fully working) → `_ensure_nonempty()` (last-resort canned reply). No silent failure modes.
- **Provider-agnostic by design:** `core/gemini_reasoner_io.py` is the active adapter; `core/anthropic_io.py` is a tombstone (removed for vendor-risk concentration). Swappable later if needed.

---

## 🤖 The Agent Mesh

| Agent | Status | Role | Memory entity types |
|---|---|---|---|
| **The Miya** | ✅ Live | Orchestrator + supervisor; single voice; capability registry; cross-agent broker | (orchestration only) |
| **The Scientist** | ✅ Live | Vitality — trajectory math, weekly planning, recalibration, the 155kg deadlift baseline | `goal`, `plan`, `commitment`, `tier_change` |
| **Bajrangi** | 🚧 Stub shipped | Recovery — reads HRV/sleep/RHR; advises (Charter enforces) | `recovery_protocol`, `sleep_concern`, `hrv_window` |
| **The Charter** | ✅ Live (infrastructure) | Policy plane — every write-tool passes through; quiet hours, HRV-red, external veto | (writes to `governance_log`) |
| **Coach (Fraser)** | 🚧 Next | Performance — CrossFit programming, load auditing | `training_block`, `lift_history` |
| **Curriculum** | 🚧 Next | Toddler + newborn developmental phases | `lesson`, `milestone`, `behavior_log` |
| **The Foodie** | 🔜 Later | Vision-based meal audits, dietary compliance | `cuisine_focus`, `meal_log`, `dietary_phase` |
| **The Voyager** | 🔜 Later | Travel research + deep-cut concierge | `trip`, `stop`, `recall_corpus` |

Each future agent is **~1 day to onboard**: define entity types, write the adapter (~120–280 LOC), register tools, register with Miya. Same substrate, different lens.

---

## 📈 What's actually shipped

| Component | Status | Notes |
|---|---|---|
| `core/` scaffolding | ✅ Shipped | 10 modules: `io.py`, `agent.py`, `decisions.py`, `charter.py`, `miya.py`, `eval.py`, `voice.py`, `cost.py`, `gemini_reasoner_io.py`, plus `memory/` package |
| The Scientist (production) | ✅ Live | Four files: `protocols.py` (~325 LOC) + `state.py` (~1,000 LOC) + `handler.py` (~2,040 LOC) + `main.py` (~200 LOC). Originally a 2,930-LOC monolith — split 2026-05-11 (specs/PHASE_4D_R1_PLAN.md). 25-tool reasoner, full memory adapter, Dakhini routing |
| The Miya (orchestrator) | ✅ Live | Hybrid router, single-voice-out, supervisor with capability registry, cross-agent broker |
| Hyderabadi voice layer | ✅ Live | `core/voice.py` — idempotent, neutral-mode toggle for debug |
| The Charter (policy plane) | ✅ Live | Quiet hours, HRV-red, external-veto policies; writes `governance_log` |
| Memory substrate (4 tiers) | ✅ Live | `core/memory/` package — `__init__.py` (~620 LOC, 5 primitives) + `archival.py` (~250 LOC, embeddings) + Scientist adapter (~365 LOC) |
| Sleep-time consolidation | ✅ Live | `scripts/memory_consolidate.py` (~270 LOC), nightly 03:00 cron |
| Bajrangi (stub) | ✅ Shipped | Demonstrates substrate reuse for non-Scientist agents (~110 LOC adapter) |
| Decisions trace log | ✅ Live | Every routing call, tool invocation, verdict logged with `trace_id`, latency, tokens, cost |
| Frictionless setup | ✅ Live | `bootstrap.sh` + `.env.example` + templated `*.plist.template` files. Anyone can clone the repo and reach a green hermetic test suite in one command — zero hardcoded `/Users/<name>/...` paths in tracked files. Promoted to a first-class architectural principle in `specs/ARCHITECTURE.md §3` |
| Model-first reasoner | ✅ Live | Gemini 2.5 Flash + 25 tools; legacy regex as fallback |
| Cost ledger | ✅ Live | `core/cost.py` — single source of pricing truth; daily-digest scaffold |
| Eval harness | ✅ Live | **475 cases passing** across 8 suites (legacy / wrapper / extended / reasoner / reasoner-robust / Gemini-parity / memory / PDF use-cases) |
| Apple Watch ingestion | ✅ Live | HRV + active calories via local FastAPI bridge |
| Curriculum, Foodie, Voyager | 🔜 Later | Each ~1 day — define entity types + adapter + tools |

### Test breakdown (475 cases, 100% passing)

| Suite | Cases | What it covers |
|---|---|---|
| `eval_suite` (legacy regex) | 148 | All 25 handler intents through `sci.route()`. Moved to `tests/scientist/` in the Phase 4 cleanup; runnable as `python -m tests.scientist.eval_suite` |
| `eval_extended` (7-dimension) | 54 | Tick behavior, Charter integration, state persistence, time-of-day, edges, recalibration, conversation invariants |
| `eval_reasoner` (B8) | 10 | Reasoner happy-path: tool calls, cost ledger, voice idempotence |
| `eval_reasoner_robust` (B9) | 21 | Hallucination guardrails, fallback ladder, 8-hop ceiling |
| `eval_gemini_parity` (G1–G38) | 39 | Reasoner output matches legacy regex on all 38 high-signal intents |
| `eval_memory` (M1–M6) | 22 | Memory write/read parity across tiers, decay, consolidation |
| `eval_gemini_pdf_usecases` (P1–P33) | 33 | Multimodal smoke tests (PDF use-cases for vision tools) |

Plus `eval_reasoner_live.py` (10 cases, opt-in behind `RAHAT_EVAL_LIVE=1`).

---

## 🗺️ Roadmap: Now / Next / Later

### Now (months 1–6) — scaling to ~20 agents

The forcing function: by month 6 the mesh is 20 deep. Anything done 20 times must be cheap.

- ✅ Single voice (Miya owns the inbox; agents never speak directly to user)
- ✅ Shared tools (`core/io.py`)
- ✅ Charter as single chokepoint with `governance_log` audit
- ✅ Decisions trace log (debug + replay)
- ✅ Generalized eval harness (every agent ships `cases.yaml`)
- ✅ Episodic memory (Scientist's weight cycles, Coach's training blocks, Curriculum's newborn phases)
- ✅ **Memory substrate** (4 tiers: events / entities / preferences / archival) + **sleep-time consolidation**
- ✅ **Model-first reasoner** (Gemini 2.5 Flash + 25-tool catalog) with legacy regex fallback
- ✅ **Cost ledger** (per-turn telemetry, daily-digest scaffold)
- 🚧 Cut launchd from Scientist-as-entrypoint to Miya-as-entrypoint
- 🚧 Bajrangi full agent (stub already proves substrate reuse)
- 🚧 Curriculum agent (toddler + newborn — Months 1–3)

### Next (months 6–12) — when concierge agents arrive

Triggered by use cases, not the calendar.

- **Profile store** — `profile_facts(subject, key, value, confidence, source, valid_from, valid_to)`. Pulled in when Foodie/Voyager need persistent preferences.
- **Embedding-based agent retrieval** — when 20 agents have overlapping descriptions and the Flash classifier starts misrouting.
- **Event log + projection rebuild** — when "replay last week through a new Miya routing strategy" becomes a real ask.
- **The Foodie & Voyager** — each ~1 day on the new contract.

### Later (12+ months) — when mobile and multi-user are real

- **FastAPI gateway over Tailscale** — read-only projections to a thin mobile cockpit.
- **Mobile client** — SwiftUI, Sign-in-with-Apple, APNs push, offline cache. Mac Mini stays the brain.
- **Multi-tenant** — partner and toddler get their own subjects (cheap insurance: `subject_id` columns added now).
- **Skill manifests + true registry** — only worth it if external/third-party agents plug in.

---

## 📊 Architecture Diagrams

Six standalone SVG diagrams live in [`/specs/diagrams/`](./specs/diagrams/):

| File | Title |
|---|---|
| [`01-three-plane-architecture.svg`](./specs/diagrams/01-three-plane-architecture.svg) | Three planes — Control / Data / Runtime, Miya as orchestrator |
| [`02-now-next-later-roadmap.svg`](./specs/diagrams/02-now-next-later-roadmap.svg) | Now / Next / Later — shipped vs. deferred with trigger conditions |
| [`03-routing-and-trace-flow.svg`](./specs/diagrams/03-routing-and-trace-flow.svg) | Routing & trace flow — one inbound message, end to end (legacy regex) |
| [`04-memory-architecture.svg`](./specs/diagrams/04-memory-architecture.svg) | **Memory architecture** — four-tier hierarchy, agent adapters, consolidation |
| [`05-model-first-reasoner-flow.svg`](./specs/diagrams/05-model-first-reasoner-flow.svg) | **Model-first reasoner flow** — Gemini 2.5 Flash + tools + memory + voice |
| [`06-mesh-extensibility.svg`](./specs/diagrams/06-mesh-extensibility.svg) | **Mesh extensibility** — per-agent adapters over shared substrate |

These are hand-written SVG with named CSS classes, diff-friendly, and embed cleanly into Google Docs / Notion / GitHub / printed PDFs. See [`specs/diagrams/README.md`](./specs/diagrams/README.md) for usage and editing notes.

---

## 📐 Specs & ADRs

Living documentation in [`/specs/`](./specs/):

- [`PRD.md`](./specs/PRD.md) — agent personas, deterministic logic, intent-ledger schema
- [`ARCHITECTURE.md`](./specs/ARCHITECTURE.md) — current target architecture (807 LOC)
- [`ADR-001-rahat-control-plane.md`](./specs/ADR-001-rahat-control-plane.md) — three-plane decision record
- [`MEMORY-AND-STATE-ARCHITECTURE.md`](./specs/MEMORY-AND-STATE-ARCHITECTURE.md) — the four-tier memory contract
- [`MODEL-FIRST-PIVOT.md`](./specs/MODEL-FIRST-PIVOT.md) — why we left regex behind
- [`SOTA-AGENT-ARCHITECTURE-REVIEW.md`](./specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md) — gap analysis that motivated memory
- [`SOTA-BUILD-STATUS.md`](./specs/SOTA-BUILD-STATUS.md) — what's actually shipped
- [`LLM-COST-OPTIMIZATION.md`](./specs/LLM-COST-OPTIMIZATION.md) — pricing model + cost-control playbook
- [`RUNBOOK-miya-cutover.md`](./specs/RUNBOOK-miya-cutover.md) — launchd swap procedure
- [`RUNBOOK-model-first-cutover.md`](./specs/RUNBOOK-model-first-cutover.md) — pivot rollback procedure

---

## 🛠️ Tech Stack

| Layer | Tool | Why |
|---|---|---|
| **Orchestration** | [OpenClaw](https://openclaw.ai) + custom Miya | Heartbeat-driven, async, single Telegram inbox owner |
| **State** | SQLite (+ JSON1) | One file, ACID, zero ops; vector layer added when needed |
| **Memory** | `core/memory.py` (4 tiers) + `core/archival.py` (embeddings) | Letta-style substrate; agent-agnostic; sleep-time consolidation |
| **Reasoner** | Gemini 2.5 Flash + tool calling | $0.001/turn, 2–6s latency, 25-tool catalog per agent |
| **Embeddings** | Gemini `text-embedding-004` (768-d) | Archival semantic search |
| **Compute** | Mac Mini M4 | Quiet, sovereign, always-on |
| **Voice** | `core/voice.py` (deterministic phrasebook) | Hyderabadi Dakhini, idempotent, zero per-message LLM cost |
| **Interface** | Telegram | Native multimodal, zero app-store friction |
| **Sensors** | HealthKit (Apple Watch), Calendar, CSV | Ambient, passive, opt-in |
| **Daemonization** | macOS `launchd` | Resilient KeepAlive, native logging |
| **Testing** | Hermetic eval harness | **475 cases / 8 suites**, runs in <60s |

---

## 📓 The Build Journey

I shipped the first version of Rahat during parental leave with a newborn — naps and bedtime were the build windows. The architecture has gone through three pivots as the system grew:

1. **Three planes.** When agent #2 forced the question of how multiple specialists share state without colliding — a control / data / runtime split, plus the Charter as a policy chokepoint.
2. **Memory substrate.** When the Scientist kept forgetting commitments after ten rounds of patches — a four-tier memory architecture with per-agent adapters and sleep-time consolidation.
3. **Model-first reasoner.** When regex routing broke on multi-clause questions and Hyderabadi-English code-mixing — a Gemini 2.5 Flash loop over a deterministic 25-tool catalog, with legacy regex as the fallback.

Each pivot made adding the next agent cheaper. Today, **the 11th agent costs ~1 day** (entity types + adapter + tool registration). That's the moat.

Follow along via commits, the [PRD](./specs/PRD.md), the [ADR](./specs/ADR-001-rahat-control-plane.md), the [SOTA review](./specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md), and [Discussions](../../discussions).

---

## 🚦 Status

Rahat is a **personal build**, not a product. The architecture, PRDs, ADRs, and diagrams are public. The agent personas, vault data, and runtime configuration are private (and will stay that way — that's the whole point of "sovereign").

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
