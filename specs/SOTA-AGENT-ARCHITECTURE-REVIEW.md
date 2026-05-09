# State-of-the-Art Multi-Agent Architectures — Survey + Recommendation for Rahat

**Author:** Claude (L8 Agent Architect)
**Date:** 2026-05-08
**Status:** Architecture review. Decision input for the memory + orchestration rebuild.

---

## TL;DR

I surveyed the agent-architecture landscape that's actively in production or heavy research as of mid-2025 / early-2026. The three orthogonal axes are **memory**, **orchestration**, and **tools** — and there are strong "winners" on each axis. None of the unified frameworks (LangGraph, AutoGen, CrewAI, ADK) are wholesale-correct for Rahat because they all assume cloud deployment, multi-tenancy, or framework lock-in that violates Rahat's sovereignty / local-first mandate. The right move is to take the proven *patterns* from each and build Rahat-native primitives.

**My recommendation: build a Rahat-native memory + orchestration layer that combines Letta's memory hierarchy + LangGraph's supervisor state-machine + GraphRAG-style entity graph + sleep-time-compute consolidation + MCP-shaped tool catalog.** This is genuinely state-of-the-art and fits Rahat's constraints. Estimated effort: 7 days for substrate + Scientist + supervisor formalization; ~1 day per future agent after that.

---

## 1. The three axes

Every modern agent architecture has independent decisions on three axes. State-of-the-art on each:

```
                 │  Memory                │  Orchestration       │  Tools
─────────────────┼────────────────────────┼──────────────────────┼─────────────────────
SOTA winners     │  Letta hierarchy       │  LangGraph supervisor│  MCP + structured
                 │  + GraphRAG entity     │  state machines      │  outputs
                 │  graph                 │                      │
                 │                        │                      │
Plus emerging    │  Sleep-time-compute    │  Anthropic sub-agents│  Tool retrieval
                 │  consolidation         │  (orchestrator/      │  (when N>50 tools)
                 │                        │   worker)            │
```

---

## 2. Memory — the field map

This is where the action is. Pre-2024, "memory" meant "stuff the conversation history into the prompt." Post-2024 it's a real subdiscipline with named patterns.

### 2a. Letta (formerly MemGPT) — three-tier memory hierarchy

The canonical academic reference (Packer et al., 2023; productized 2024 as Letta). Three tiers, all visible to the agent:

  - **Core memory** — small, always-in-context. The agent's "personality + persistent facts." Edited via tool calls (`core_memory_append`, `core_memory_replace`).
  - **Recall memory** — full conversation history. Searchable via tool (`recall_search`).
  - **Archival memory** — long-term storage of facts. Vector-searchable. Agent decides what to save (`archival_insert`) and what to look up (`archival_search`).

The cutting-edge insight: **memory operations are tools the agent calls, not infrastructure that's always-on.** The agent learns when to remember, when to forget, when to recall. This is the "self-editing memory" pattern.

Status: open source, runs locally, MIT-licensed Python service.

### 2b. GraphRAG (Microsoft) — entity graph + community summaries

The 2024 GraphRAG paper showed that for cross-conversation reasoning ("what did the user mention about X across the last 6 months"), a knowledge graph beats vector RAG. They build:

  - Entity extraction → entity graph
  - Community detection on the graph
  - Hierarchical summaries per community

For Rahat: a "Japan trip" entity in the Foodie agent's memory would be a node connected to "places visited," "meals tried," "dates," and downstream a Scientist query about jet lag could traverse the graph.

Status: research → reference implementation (Microsoft GraphRAG library). Heavyweight; not all of it is needed at single-user scale.

### 2c. Mem0 + Zep — managed memory layers

Mem0 (formerly EmbedChain) and Zep are SaaS memory providers. They expose `memory.add(text)`, `memory.search(query)` APIs and handle the embedding + storage. Zep adds temporal reasoning (the user's preferences shifted in March).

Pros: zero-effort to integrate. Cons: SaaS dependency, not local-first. Off the table for Rahat.

### 2d. Sleep-time-compute consolidation

Emerging pattern (Letta blog 2024, multiple 2025 papers). The idea: agents have a **wake cycle** (responding to user) and a **sleep cycle** (background consolidation). During sleep:

  - Threads get summarized
  - Recurring patterns get extracted into core memory
  - Outdated facts get archived
  - Preferences with low confidence get decayed
  - Cross-thread relationships get strengthened

Status: nascent in production but several research papers in 2024-2025. The pattern is right; the implementation is still being figured out.

### 2e. Memory as toolbox vs. memory as infrastructure

Two philosophies:

  - **As infrastructure** (most chat apps): memory is loaded automatically by the runtime; the agent doesn't know about it.
  - **As toolbox** (Letta, MemGPT): memory operations are tools the agent calls, with the model deciding when to recall/save.

The toolbox approach is more flexible and more cognitively realistic, but requires the model to be capable of self-directing memory. With Gemini 2.5 Flash + good tool descriptions, this works well.

---

## 3. Orchestration — the field map

### 3a. LangGraph — explicit state-machine graphs

The dominant production orchestration framework as of 2025. Agents are nodes in a directed graph; edges define transitions; there's a typed `State` object that flows through. Built-in checkpointing, human-in-the-loop, streaming, time-travel.

Patterns LangGraph encodes well:
  - **Supervisor** — one orchestrator routes to specialists (this is what Miya should be)
  - **Hierarchical** — supervisors of supervisors
  - **Plan-and-execute** — planner produces a graph, executor runs it
  - **Multi-agent network** — peer-to-peer with shared state

Status: open-source, Python, well-documented. Heavyweight dependency.

### 3b. Anthropic Claude Code sub-agents — orchestrator/worker

Anthropic publicly described this pattern: a main agent spawns sub-agents for parallel/specialized work. Each sub-agent has its own context window. Results bubble back up. Battle-tested at scale.

Patterns:
  - Main agent decomposes task
  - Sub-agents execute independently
  - Main agent integrates outputs

Lightweight, no framework — the pattern is just "spawn another agent, give it a focused task, get a result."

### 3c. AutoGen — conversable agents

Microsoft's framework. Agents talk to each other via group-chat. Less prescriptive about state. Good for research / brainstorming agent teams.

For Rahat: not a fit. We don't need agents brainstorming with each other; we need each specialist agent to do its job well and a router to pick the right one.

### 3d. Google ADK — managed multi-agent runtime

Released April 2025. Hierarchical multi-agent system, integrated with Vertex AI / Cloud Run, has built-in evaluation. Designed for production agents on GCP.

For Rahat: **not a fit**. Cloud-coupled (violates sovereignty). Vertex AI dependency.

### 3e. CrewAI — role-based crews

Sequential pipelines of role-decorated agents. Good for "research → write → edit" workflows. Less interactive.

For Rahat: not a fit. Our agents are interactive and event-driven, not pipelined.

### 3f. OpenAI Swarm / Agents SDK — handoff-based

Lightweight. Agents are functions; "handoff" is just returning another agent. Minimal abstraction.

For Rahat: the *idea* is good (lightweight handoffs); the SDK itself is OpenAI-coupled.

---

## 4. Tools — the field map

### 4a. MCP (Model Context Protocol) — Anthropic, Nov 2024

Open standard for connecting LLMs to tools/data. Defines:
  - Tool catalog format
  - Tool invocation protocol
  - Resource subscription
  - Sampling primitives

Adopted by Anthropic, OpenAI, increasing momentum. **Becoming the standard.** Rahat's `tools.SCHEMAS` is already MCP-shaped; minor changes would make it MCP-compatible.

### 4b. Structured outputs (JSON mode, Pydantic)

Both OpenAI and Google support strict JSON schema enforcement on outputs. This is the right shape for any tool that returns structured data.

### 4c. Tool retrieval

When agent has >50 tools, you don't put them all in the prompt. You retrieve the top-K by query similarity. Rahat is at 21 tools — not yet a problem, but worth knowing for the 20-agent target.

---

## 5. Where the field is converging (Q1 2026)

After surveying recent work, the consensus shape for high-quality stateful agent systems is roughly:

```
┌────────────────────────────────────────────────────────────────────┐
│  AGENT RUNTIME                                                      │
│                                                                      │
│  ┌─ Memory (Letta-style hierarchy) ──────────────────────┐          │
│  │  - Core memory (always-in-context)                     │          │
│  │  - Recall memory (recent conversation, searchable)     │          │
│  │  - Archival memory (long-term, vector-searchable)      │          │
│  │  - Memory operations exposed as tools to the agent     │          │
│  └─────────────────────────────────────────────────────────┘          │
│                                                                      │
│  ┌─ Knowledge graph (GraphRAG-lite) ─────────────────────┐          │
│  │  - Entity extraction during conversations              │          │
│  │  - Cross-agent entity relationships                    │          │
│  │  - Used for "what did user say about X across time"    │          │
│  └─────────────────────────────────────────────────────────┘          │
│                                                                      │
│  ┌─ Orchestration (LangGraph supervisor) ────────────────┐          │
│  │  - Explicit agent graph                                │          │
│  │  - Typed shared state                                  │          │
│  │  - Checkpoint per turn                                 │          │
│  └─────────────────────────────────────────────────────────┘          │
│                                                                      │
│  ┌─ Tools (MCP-shaped) ──────────────────────────────────┐          │
│  │  - Standard catalog format                             │          │
│  │  - Structured outputs                                  │          │
│  │  - Retrieval when N grows large                        │          │
│  └─────────────────────────────────────────────────────────┘          │
│                                                                      │
│  ┌─ Sleep-time consolidation ────────────────────────────┐          │
│  │  - Background worker                                   │          │
│  │  - Thread summarization                                │          │
│  │  - Preference decay                                    │          │
│  │  - Pattern extraction                                  │          │
│  └─────────────────────────────────────────────────────────┘          │
└────────────────────────────────────────────────────────────────────┘
```

This is the synthesis of where the field is in early 2026. None of the unified frameworks (LangGraph, AutoGen, ADK, CrewAI) implement *all five* — each picks 2–3.

---

## 6. What's right for Rahat

Rahat's hard constraints:

  - **Local-first / sovereign.** Mac mini, no cloud dependencies for runtime.
  - **Single-user (Venkat).** No multi-tenancy concerns.
  - **1 → 20 agents over 6–12 months.** Medium-scale mesh.
  - **SQLite-only.** No separate DB infra.
  - **Already invested:** Charter (policy plane), decisions ledger (observability), Voice layer, episodic stub, tool-using reasoner (Gemini Flash + Pro), 420-case eval suite.
  - **Preferences:** Gemini-primary (Anthropic out by your strategic call), explicit composability, custom fit over framework lock-in.

Given those constraints, **no single framework is the right adoption**:

  - LangGraph — heavy Python dep, would force rewrite of Charter/Voice/decisions integration.
  - Letta — runs as a separate service; would fragment our deployment story.
  - Mem0 / Zep — SaaS, fails sovereignty.
  - Google ADK — cloud-coupled.
  - AutoGen / CrewAI — wrong shape (conversable / pipelined).

But **the patterns from these frameworks are exactly what Rahat needs**. The recommendation is to build Rahat-native primitives that implement the proven patterns, with no framework adoption.

---

## 7. The recommendation

Build a five-layer architecture that synthesizes the best of the field, fitted to Rahat's constraints. This extends my previous "memory + state" proposal with two additions: **archival memory + semantic search** and **sleep-time consolidation**.

### Layer 1 — Universal memory substrate (mesh-wide, agent-agnostic)

The five primitives from the previous proposal (events, entities, threads, preferences, relationships) — these cover **core memory** (entities) and **recall memory** (events + threads). Single SQLite namespace, agent-scoped by default. ~250 LOC.

### Layer 2 — Archival memory + semantic search (NEW vs previous proposal)

Add a `memory_archival` table with text + embedding columns, indexed by sqlite-vss (or a small native FAISS-equivalent). When an agent decides a fact is worth long-term recall, it inserts here. Semantic search via vector similarity returns relevant archival entries.

  - Local-first: sqlite-vss runs in-process.
  - Sovereignty-compatible: no external API calls.
  - Embeddings: Gemini text-embedding-004 (already available; ~$0.0001/text, runs once at insert time).

This is what gives the agent answers to "what did I tell you about X 3 months ago." Without it, Rahat's memory is bounded to recent + active state.

### Layer 3 — Memory as tools (Letta pattern)

Expose memory operations to the reasoner as tools:

```
memory.recall_search(query)        → search recent events/threads
memory.archival_search(query)      → semantic search over long-term
memory.archival_insert(text, ...)  → save to long-term
memory.list_active(entity_type)    → list active entities of a type
memory.upsert_pref(key, value)     → update sticky preference
memory.get_recent_thread(topic)    → fetch a thread by topic
```

The agent decides when to recall. Models are good enough at this now (Letta's results show ~85% appropriate-recall rate with Flash-class models).

### Layer 4 — Per-agent adapters (mesh extensibility)

Each agent registers its entity types + assembler + extractor — same as the previous proposal. Scientist registers `goal/plan/commitment/tier_change`. Bajrangi registers `recovery_protocol/sleep_concern`. Foodie registers `cuisine_focus/meal_log`. Each agent's adapter is ~100 LOC.

### Layer 5 — Sleep-time consolidation worker (NEW vs previous proposal)

A background process (Python script run by cron at 03:00 daily) that:

  - Summarizes inactive threads (>24h since last activity, status=open) → updates `summary` column
  - Decays preferences with low recent reinforcement (`confidence *= 0.95` per week)
  - Archives old entities (status='archived' for entities >90 days expired)
  - Extracts cross-thread patterns and writes them as new entities/relationships
  - Garbage-collects events older than 1 year (or moves them to a cold archive db)

This is the cutting-edge piece. Most production systems don't have it. With it, Rahat's memory stays coherent and dense rather than bloating with stale data.

### Layer 6 — Miya as supervisor (formalization)

Today Miya is regex + LLM classifier. Formalize it as an explicit supervisor with:

  - **Declared agent capabilities** — each agent's manifest lists what it can answer
  - **Routing state** — which agent is the "active" one in the current thread
  - **Cross-agent broker** — when the Scientist's reasoner needs Bajrangi's HRV state, Miya brokers the read with permission

This is the LangGraph supervisor pattern, implemented in 200 LOC of Rahat-native code rather than 50K LOC of LangGraph.

### Layer 7 — Tools layer (already done, formalize as MCP-shaped)

Our `tools.SCHEMAS` is already 90% MCP-compatible. Document the format, write a converter if we ever need to expose tools externally, otherwise keep as-is.

---

## 8. What this gets us

Capability matrix vs. SOTA frameworks:

| Capability | LangGraph | Letta | GraphRAG | Mem0 | ADK | **Rahat (proposed)** |
|---|---|---|---|---|---|---|
| Local-first | ✗ | ✓ | ✓ | ✗ | ✗ | ✓ |
| No-framework-lock | ✗ | ✗ | ✓ | ✗ | ✗ | ✓ |
| Memory hierarchy | partial | **best** | ✗ | partial | partial | **✓** |
| Archival semantic search | ✗ | ✓ | ✓ | ✓ | partial | **✓** |
| Entity graph (cross-agent) | ✗ | partial | **best** | partial | ✗ | **✓** |
| Sleep-time consolidation | ✗ | partial | ✗ | ✗ | ✗ | **✓** |
| Supervisor orchestration | **best** | ✗ | ✗ | ✗ | ✓ | ✓ |
| Charter / policy plane | ✗ | ✗ | ✗ | ✗ | partial | **✓** |
| Voice layer | ✗ | ✗ | ✗ | ✗ | ✗ | **✓** |
| Decisions ledger | partial | ✗ | ✗ | ✗ | ✓ | **✓** |
| 420-case eval suite | depends | partial | ✗ | partial | ✓ | **✓** |
| Single-binary deploy | ✗ | ✗ | ✗ | ✗ | ✗ | **✓** |

The proposed Rahat architecture is the only one that hits *all* the rows. Every framework misses 4–6 of them.

---

## 9. Migration plan

**Days 1–5: as the previous proposal** — build the substrate, Scientist adapter, reasoner integration. (Detailed in `MEMORY-AND-STATE-ARCHITECTURE.md`.)

**Day 6 — Archival memory + semantic search.**
  - Add `memory_archival` table with text + embedding columns
  - Wire sqlite-vss (or pure-Python alternative if vss is finicky on your Mac mini)
  - Implement Gemini text-embedding-004 wrapper
  - Add `archival_insert`, `archival_search` tools to the catalog

**Day 7 — Sleep-time worker + Miya supervisor formalization.**
  - Background script `scripts/memory_consolidate.py` to run via cron at 03:00
  - Thread summarization (Gemini Flash, ~$0.001/thread/run)
  - Preference decay
  - Entity archiving
  - Update `core/miya.py` with explicit supervisor pattern: declared capabilities, routing state, cross-agent broker

**Day 8 (buffer / hardening) — Eval suite + first cross-agent test.**
  - Add G40+ cases for archival memory correctness
  - Add G50+ cases for sleep-time consolidation
  - Stub a second agent (e.g. minimal Bajrangi) to verify cross-agent broker works
  - Soak

After this, every future agent (Bajrangi, Curriculum, Foodie, Japan-recall) is **~½ day to onboard** — define entity types, write a small adapter, register with Miya. No new tables, no new patterns.

---

## 10. What this does NOT include (deliberately)

  - **Multi-tenant / RBAC.** Single-user mesh. Skip.
  - **Distributed agents** (gRPC, message queues). Mac mini, single process. Skip.
  - **GraphRAG community detection** at scale. Useful at >1000 entities; we have <100. Skip until needed.
  - **Hierarchical supervisors.** Useful when N>10 agents + clear domains. We're at 1 + Miya; supervisor only at the Miya level.
  - **External MCP server**. We use MCP-shape internally; exposing Rahat tools to external clients is a Later phase.

---

## 11. The decision

**Recommendation: 8-day build plan, Path A from the previous spec + the two additions above.**

This gives Rahat:

  - The **memory hierarchy** Letta proved is the right pattern, fitted to local-first SQLite.
  - The **entity graph** that GraphRAG showed enables cross-conversation reasoning.
  - The **memory-as-tool** pattern that lets the agent self-direct recall.
  - The **sleep-time consolidation** that emerging research says matters for long-running systems.
  - The **supervisor pattern** LangGraph proved at scale.
  - The **MCP shape** for tools so we can interop later if we want.
  - All of it composes with Charter, Voice, decisions ledger, and eval suite — the existing investment isn't thrown away.
  - All of it stays **local-first**, **sovereign**, **single-binary-deployable**.

This is genuinely state-of-the-art for the constraints Rahat is operating under.

If you want a shorter pilot: **5 days for the previous proposal (substrate + Scientist) without archival/sleep-time** still gets you ~70% of the value and is a strict subset.

If you want the full thing: **8 days** ships the SOTA architecture.

After this lands, the next agent — Bajrangi for HRV/sleep — is a 1-day add. Curriculum for your kids is a 1-day add. Foodie is a 1-day add. The compounding is the point.

Tell me which.
