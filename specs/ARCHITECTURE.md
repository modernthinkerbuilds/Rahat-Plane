# Rahat: A Sovereign Intent Runtime for Personal AI Agents

**Document Class:** Architecture Review (ARB-grade)
**Version:** 1.0 (May 2026)
**Author:** Venkat Sadras, with architecture review by Claude (L8 framing)
**Status:** Now-phase shipped; Next-phase deferred until trigger conditions land

**Companion diagrams** (`specs/diagrams/`):

- `01-three-plane-architecture.svg` — referenced in §4 (the three-plane decomposition)
- `02-now-next-later-roadmap.svg` — referenced in §6 (the roadmap)
- `03-routing-and-trace-flow.svg` — referenced in §5 (component deep-dives) and §8.3 (decision tracing)

See `specs/diagrams/README.md` for embedding notes.

---

## 1. Executive Summary

Rahat is a multi-agent runtime that runs locally on a single Mac Mini (M-series Apple Silicon), coordinates a fleet of specialized personal-AI agents through a shared SQLite "intent ledger," and presents a single user-facing voice ("Miya") over a Telegram channel. It is designed to expand from one production agent today (the Sports Scientist) to roughly twenty agents within six months — covering domains like CrossFit programming, weight management, infant/toddler curriculum, appointment scheduling, and concierge-class services (foodie, travel, coffee, play-date planning).

The current shipped state ("Now") includes:

- A **three-plane control-plane architecture** (Control, Data, Runtime) that separates declarative policy from ephemeral compute from durable state.
- **Miya as the orchestrator** — single user-facing process, single Telegram inbox, single voice (Hyderabadi/Dakhini register).
- **The Charter as a policy chokepoint** — every outbound work-order passes through a Python predicate registry that can approve, modify, or veto, with an audit trail in `governance_log`.
- **Decision tracing** — every event in the runtime gets a `trace_id`, every routing decision and tool call writes a row in the `decisions` table, enabling replay and grading.
- **Episodic memory primitives** — first-class support for bounded life-slices (a trip, a training cycle, a sleep regression) shared across agents.
- **A 330-case eval harness** — three independent paths (legacy router, agent wrapper, generalized harness) plus 7-dimensional regression sweep, all 100% green.
- **The Sports Scientist as the reference agent** — refactored to consume the new contract, with its 2,400 LOC monolith split into pure protocols (math, constants) and runtime concerns (handlers, ticks).

The document below justifies every meaningful design choice against the alternatives we considered, and frames the system in language an ARB-style review would expect: trade-offs, failure modes, operational maturity, and a clear migration path to mobile and multi-tenant in 12+ months.

---

## 2. Context

### 2.1 The "Post-Chat" thesis

Today's personal AI is reactive: the user prompts, the model answers, neither remembers, neither acts. Rahat takes the position that the next platform is *proactive*: a substrate of specialized agents that *observe* the user's life (Apple Watch, calendar, gym programming, photos), share a common state, and quietly close the gap between where the user is and where they want to be.

Concretely, this means:

- **No prompt is required for routine work.** Morning brief at 8am, recovery nudge at 9pm, walk-pace check during the day, weekly recap on Sunday at 23:55.
- **State, not turns.** The system carries context across days, weeks, episodes — not just within a chat window.
- **Agents are roles, not models.** A "Sports Scientist" agent is a concrete domain expert with deterministic protocols, not an LLM with a system prompt.

### 2.2 User and use-cases

The reference user is a Bay Area Product Manager (the document author) with: a CrossFit habit and a 80kg/176lb target weight, a deadlift PR target of 155kg, a toddler and a newborn, an espresso ritual, a guitar habit, and a demanding job. The user has been a heavy ChatGPT user for fitness coaching and has ~12 weeks of detailed coaching history that informs the Sports Scientist's protocols (locked 0.75 lb/wk loss rate, 2,600 kcal intake, 6,000 kcal weekly active burn, 3 PRVN CrossFit + 1 Zone-2 10K + 3 active-rest cadence).

The Year-1 agent expansion targets:

| Phase | Agents | Use-case examples |
|---|---|---|
| Months 1–3 | Sports Scientist (live), Coach (Fraser), Curriculum, Bajrangi | Workout programming, toddler developmental phases, HRV-driven recovery |
| Months 3–6 | Appointment scheduler, Foodie (vision-based meal audits), Voyager | Dietary compliance, scheduling, trip logistics |
| Months 6–12 | Concierge-class agents (Japan trip, restaurant/coffee/pastry recommendations from a saved corpus), play-date planner, household manager | "Find that Tokyo coffee place I bookmarked"; "plan a play-date for Saturday based on my calendar and the kids' nap times" |

By month 6 the mesh is targeted at ~20 agents. This is the forcing function for every Now-phase design choice: anything that has to be done 20 times must be cheap, anything that's a chokepoint must exist.

### 2.3 Hard constraints (non-negotiables)

1. **Sovereignty.** All biometric and family state lives on the user's Mac Mini. No cloud sync of HRV, weight, calendar, or any data the user wouldn't want exfiltrated. Cloud APIs (Gemini, Telegram) are tools, not state homes.
2. **Single user voice.** The user must never see "two bots." Even with 20 agents, Miya is the only persona the user talks to.
3. **Deterministic where it matters.** Anything load-bearing for health (kcal math, HRV bands, scheduler) must be deterministic Python, not LLM-generated. LLM is the *fallback* for free-form coaching, not the *primary* compute.
4. **Eval-gated changes.** No production deploy without the eval suite green. (330 cases as of v1.0.)

### 2.4 Why not pick an off-the-shelf framework?

We evaluated LangGraph, AutoGen, and CrewAI early in the design. All three were rejected for the same reason: they assume cloud-resident state, they ask the runtime to be the *graph*, and they produce reasoning chains rather than deterministic protocols. The Rahat thesis — local-first sovereignty + deterministic core + LLM-at-edges — is structurally incompatible with frameworks built for opaque, cloud-resident multi-agent reasoning. We accept the cost of writing our own slim runtime to keep the moat.

---

## 3. Architecture principles

Each principle below is paired with the alternative we rejected and the cost we accepted in choosing it.

| Principle | Rejected alternative | Cost we accepted |
|---|---|---|
| **Local-first sovereignty** | Cloud-resident state with E2E encryption | Single-machine availability (no failover yet); we mitigate via launchd KeepAlive + `caffeinate -imd` to keep the Mac always-up. |
| **Deterministic core, LLM at edges** | LLM does everything (chain-of-thought planning) | More code for the math; harder to "just add a feature." Worth it: load-bearing health/coaching math cannot be probabilistic. |
| **State-bus over RPC** | Agents call each other via in-process methods or HTTP | Slightly more ceremony to write a row than call a function. Worth it: agents stay fully decoupled, swap-in/swap-out is trivial. |
| **Heartbeat-driven** | Cron / event-driven only | A 1-minute tick budget (we run on 1Hz inside the loop with minute-bucket dispatch). Worth it: nudges and recalibration *just happen*. |
| **Manifest-free at small N** | Full TOML manifest registry + scope checks | Slightly higher coupling between agent and runtime. Worth it at N≤6 agents; we'll add manifests when N reaches the marketplace threshold. |

---

## 4. The three-plane decomposition

Borrowed in spirit from Borg / Vertex AI Agent Engine: separate **what to run** (declarative) from **what to remember** (state) from **how to run it** (compute). The goal is that adding agent #20 changes one of the three planes minimally and the other two not at all.

```
                ┌────────────────────────────────────────────┐
                │                  CHANNELS                  │
                │  Telegram (now)  ·  Mobile (later)  ·  …   │
                └────────────────────┬───────────────────────┘
                                     │
                ┌────────────────────▼───────────────────────┐
                │             Miya (Orchestrator)            │
                │  classify → fan-out → synthesize → voice   │
                └─────────┬────────────────────┬────────────┘
                          │                    │
       ┌──────────────────▼──────┐  ┌──────────▼──────────────┐
       │      Charter (Policy)    │  │  Decisions (Trace Log)  │
       │  every work-order passes │  │ trace_id, span, latency │
       │  approve · modify · veto │  │  cost, outcome, replay  │
       └──────────────────┬───────┘  └──────────┬──────────────┘
                          │                     │
                ┌─────────▼─────────────────────▼─────────────┐
                │              Agent Host                      │
                │  ScientistAgent · Coach · Curriculum · …     │
                │     (Python class implements: Agent)         │
                └────────────────────┬─────────────────────────┘
                                     │
                ┌────────────────────▼─────────────────────────┐
                │           SQLite Intent Ledger               │
                │ raw_vitals · weighin_log · workout_log       │
                │ weekly_plan · week_preferences · intents     │
                │ governance_log · decisions · episodes        │
                └──────────────────────────────────────────────┘
```

### 4.1 Control plane — declarative

What the system **may do**.

- **Agent registry.** A list of registered `Agent` instances at process start. No TOML manifests yet (deferred to Later when external agents arrive); for the Now-phase 6-agent target, in-process registration is simpler, faster, and one-source-of-truth.
- **Charter (policy engine).** A registry of Python predicates decorated with `@policy("kind_glob")`. Every `WorkOrder` passes through `charter.review()` before execution; the verdict (`approved | modified | vetoed`) is written to `governance_log`. Three starter policies ship: quiet hours (22:30–07:00 with priority bypass), HRV-red blocks intensity pushes, external-veto check (honors any `governance_log` row from another agent).
- **Identity / scopes.** Single-user today (`subject_id` not yet plumbed). The decision to pre-add `subject_id` columns to *new* projections in Now (5 min/table) is documented in the Now-phase plan as cheap insurance against the future multi-user migration.

**Why "control plane" as a separate plane** rather than per-agent config: at N=20 agents, the same policies (quiet hours, HRV-red veto) apply to many of them. Owning policies once, in one place, lets us update them once. The alternative — every agent enforces its own policies — guarantees inconsistency by month 3.

### 4.2 Data plane — durable state

What the system **knows**.

The Intent Ledger is a single SQLite file (`vault/rahat.db`) with WAL mode enabled. Tables fall into four categories:

| Category | Tables | What they hold |
|---|---|---|
| **Observations** | `raw_vitals`, `vitality_samples`, `vitality_daily_summary`, `hrv_log`, `weighin_log`, `workout_log` | Passive data from sensors and manual logs. Append-only in spirit. |
| **Intents** | `intents` | Hard-coded North Stars: 84 kg by 2026-08-13 (intermediate), 80 kg by 2026-11-03 (final). Auto-recomputed on weight log via `recalibrate_intents()`. |
| **Plans** | `weekly_plan`, `week_preferences`, `weekly_campaigns` | The materialized 7-day schedule for each week, plus per-week overrides (unavailable days, forced CF picks, tolerated blacklist movements). |
| **Audit & runtime** | `governance_log`, `decisions`, `episodes`, `episode_notes`, `nudge_log`, `user_state` | Charter verdicts, decision traces, episodic memory, throttling state, cross-restart key-value (e.g. `recovery_tier`). |

**Why SQLite, not Postgres / DuckDB / lancedb?**

- **One file, ACID, zero ops.** The single biggest ergonomic win for a personal-build runtime. WAL mode supports concurrent readers + serialized writers, which is exactly the workload (4–6 agents reading; one writer at a time via thread-local connections).
- **Mongo-style flexibility via JSON1.** When schema needs to evolve (a new agent introduces a new "what was happening on March 14" episode shape), JSON columns absorb the change without migration.
- **Vector search on the same file.** When the Concierge agent needs semantic search over saved bookmarks/notes, `sqlite-vss` keeps that retrieval *inside the same file* as the rest of state. No second store, no cross-store joins, no consistency gymnastics.
- **Backup is `cp`.** A single-file database means the user's entire state can be snapshotted with `rsync vault/rahat.db ~/Backups/` — and that file is meaningful and queryable on any other machine, today or in 10 years.

**Why not Postgres?** Adds a dependency, requires schema migrations, requires a server process, and gives nothing the workload actually needs. We will revisit if/when the runtime moves off-Mac (Later phase).

### 4.3 Runtime plane — compute

What the system **does right now**.

- **Miya (orchestrator).** Owns the Telegram poll loop and the agent registry. Hybrid router: walk every agent's `triggers` (cheap regex first); if exactly one fires, dispatch; if zero or multiple, fall back to a Gemini Flash classifier ("which of these N agent descriptions best matches?").
- **Agent host.** Lifecycle: `on_start`, `on_message → route → Reply`, `on_tick(now)`, `on_stop`. Each agent implements a 4-method contract (`name`, `description`, `triggers`, `route`, `tick`).
- **Tool broker** (`core/io.py`). Single chokepoint for outbound Telegram calls, Gemini API calls, and SQLite connections. 20 agents share one connection-pooled HTTP session and one Gemini client; without this, we'd have 20 redundant client init paths.
- **Voice layer** (`core/voice.py`). Per PRD §3, Miya owns the persona ("Dakhini-Hyderabadi wit + PM brevity"). Implemented as a deterministic phrasebook (no per-message LLM call) classified by message kind. Idempotent and preserves all numbers/dates/structure verbatim.
- **Decision trace** (`core/decisions.py`). `span()` context manager wraps every meaningful operation; every span gets a `trace_id`, latency, in/out shapes, outcome. With 20 agents this is the difference between "I can debug Tuesday 9pm" and "no idea what happened."
- **Eval harness** (`core/eval.py`). Generalized — every agent ships its own cases; the harness handles isolation, DB seeding, fixture writes. Three independent paths run currently: `eval_suite.py` (legacy router, 142 cases), `eval_via_agent.py` (through the wrapper, 142 cases), `eval_extended.py` (7-dimensional regression sweep, 46 cases).

---

## 5. Component deep-dives

### 5.1 Miya — the orchestrator (the "why" for a single voice)

**Problem:** With 20 agents, the user can't see 20 Telegram bots. Even with 6, two bots is two bots too many — defeats the entire premise of "ambient mesh."

**Solution:** Miya owns the inbox. Every inbound Telegram message hits Miya, which classifies and dispatches; every outbound message goes through Miya, which Charter-reviews and voice-dresses.

**Why a hybrid regex-then-LLM router?** Pure regex doesn't scale to 20 agents — every agent's keyword list overlaps with at least two others. Pure LLM costs ~$0.0001 per message and adds 1–2s latency to every reply. Hybrid solves both: regex shortcuts handle ~80% of high-confidence routes (today, weight, HRV, schedule) at zero cost; LLM only fires on the long tail. We measure this via `decisions` — the `strategy` field records which path fired.

**Why "Miya," and why Hyderabadi?** The PRD calls out the persona explicitly. We isolated the voice to a layer (`core/voice.py`) so the underlying Scientist outputs stay parseable for evals — the persona isn't tangled with the data path. `RAHAT_VOICE=neutral` env var disables the voice entirely (useful for screenshots, debugging, or future users who want a different register). When Coach, Curriculum, etc. ship, they automatically inherit the same voice without writing any voice code themselves.

### 5.2 The Charter — policy chokepoint

**Problem:** Quiet hours, HRV-red blocks, family priority — these constraints apply to *outputs*, not to any single agent's logic. Without a chokepoint, every agent reimplements them; some forget; the user gets a workout nudge at 11:30pm during a sleep regression.

**Solution:** A tiny registry of Python predicates that every `WorkOrder` passes through:

```python
@policy("notify.*", name="quiet_hours")
def quiet_hours(wo, ctx):
    h, m = ctx["now"].hour, ctx["now"].minute
    if h * 60 + m >= 22*60+30 or h * 60 + m < 7*60:
        if wo.priority <= 2:
            return Verdict.approve("urgent — bypassed quiet hours")
        return Verdict.veto("quiet hours (22:30–07:00)")
    return Verdict.approve()
```

Verdicts (`approved | modified | vetoed`) write a row to the existing `governance_log` table. *Now* something writes to it.

**Why predicates, not a DSL?** A DSL would need its own evaluator, parser, error handling, and tooling. Python predicates are: ergonomic to write, easy to test, easy to debug, and run inside the existing Python process. Cost: policies aren't user-editable (no admin UI). Acceptable: the user is the developer.

**Why three starter policies (quiet hours, HRV-red blocks, external-veto check)?** They're the three that have actual business value at N=1 agent. Each represents a distinct mode: time-based (quiet hours), data-based (HRV-red), and cross-agent (external-veto, which honors a row written by *some other agent* like Bajrangi). We'll add policies as concrete needs surface — not preemptively.

### 5.3 The voice layer

**Problem:** PRD §3 says Miya speaks Dakhini-Hyderabadi. The agents under it don't and shouldn't — their outputs are eval-pinned and need to stay deterministic for the 142-case suite to keep working.

**Solution:** A post-processor in Miya's outbound path. Phrasebook keyed on message-kind classification (morning, recovery, walk, weekly_reset, status, schedule, weight, ack). Idempotent — a string already containing "Hau bhai" or "Suno miya" is left alone. Preserves all numbers/dates/structure verbatim (eval-tested across 7 message kinds).

**Why a deterministic phrasebook, not an LLM rewrite?** An LLM rewrite would cost ~$0.0001 + 1–2s per outbound message and risks mangling numbers. A phrasebook with random selection from per-kind opener/closer pools sounds varied without these costs. Cost: less variation than an LLM could produce. Acceptable: the user gets a consistent voice; "every reply sounds slightly different but obviously Hyderabadi" is the goal.

**Why classify by message kind rather than always-prepend?** Errors and acks already have clear visual markers; adding "Hau bhai" to an error message would obscure the fault. The classifier skips errors entirely and uses kind-appropriate flair for the rest.

### 5.4 Decision tracing

**Problem:** Debugging a 20-agent mesh on `print()` and a `tail -f` log is impossible. By month 3, you can't tell *why* Miya routed a specific message to the wrong agent.

**Solution:** Append-only `decisions` table; `trace_id` per inbound event; `span_id` per operation; `parent_id` for nesting. Every meaningful operation wraps in:

```python
with decisions.span("agent.scientist.route", trace_id=tid, actor=name, input={"msg":msg}) as s:
    s.output = agent.route(msg)
```

The `span()` context manager auto-captures latency and exceptions. Cost: ~1 row per inbound message + per tool call (typically 3–5 rows per turn). At 50 messages/day, that's ~250 rows/day, ~90k/year. SQLite handles this trivially.

**Why a separate `decisions` table, not the same `governance_log`?** Different access patterns: `governance_log` is queried per-policy-question ("did anyone veto a coach.push_intensity in the last 24h"); `decisions` is queried per-trace ("show me everything that happened for trace_id X"). Separating them lets each have the right indexes and lets `governance_log` stay the contract surface for cross-agent communication.

### 5.5 Episodic memory

**Problem:** Many agents intrinsically operate in episodes — a training cycle, a weight-loss phase, a trip, a sleep regression, a "Japan trip Jan 2026." Without an `episodes` table, each agent invents its own `phases` / `cycles` / `blocks` table; by agent #15 we have eight near-duplicates and no way to ask "what was happening in my life on March 14?"

**Solution:** `episodes(id, kind, subject, started_at, ended_at, status, entities_json)` + `episode_notes(episode_id, ts, actor, text, payload_json)`. Six-line Python API: `open()`, `close()`, `note()`, `get()`, `list_open()`, `find()`.

**Why ship this in Now even though Concierge is a Year-1 item?** The Curriculum agent (toddler/newborn phases), the Coach (training blocks), and the Scientist (weight cycles) all consume episodic shape. Ship the table now; cost is ~1.5 hours of work + zero ongoing tax. Without it, the first three agents to need an episode will each invent their own.

### 5.6 The agent contract

```python
class Agent:
    name: str = "unnamed"
    description: str = ""              # used by Miya's LLM classifier
    version: str = "0.1.0"
    triggers: list[str] = []           # regex shortcuts for cheap routing
    def route(self, msg: str) -> Reply | None: ...
    def tick(self, now: datetime | None = None) -> list[Reply]: ...
    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...
```

A `Reply` is `(text, confidence, work_orders=[])`.

**Why this exact 4-method shape?** It's the smallest set that supports: synchronous routing (every agent), scheduled work (most agents), startup hooks (DB seed, table create), graceful shutdown. Adding a fifth method should require a clear use-case.

**Why no manifest TOML in Now?** At N=6, in-process Python registration is simpler and faster. We'll add manifests in Later when external/third-party agents arrive (the "anyone can plug in an agent" goal). Ship cost when needed: ~2–3 days for manifests + scope checks.

### 5.7 The Sports Scientist (reference implementation)

The first production agent. Refactored in Phase A to consume the new contract:

- **`protocols.py`** — pure-math constants and helpers (BMR, tier tables, HRV bands, weekday parsing, gym-plan blacklist filter). ~250 LOC, no DB, no network. Other agents (Coach, Curriculum) can import from here without dragging in the runtime.
- **`main.py`** — handlers and route dispatch. 2,400 → 2,200 LOC after the protocols extraction. Imports from `protocols.py` via path bootstrap so it works whether loaded as a module (`importlib.spec_from_file_location`) or as a package.
- **`agent.py`** — `ScientistAgent(Agent)` wrapper. ~150 LOC. Delegates `route()` to `main.route()`, `tick()` to the four legacy nudge functions. No behavior change visible to the user (proven by 142/142 eval cases unchanged).

The Scientist owns: weight intent (84kg / 80kg targets), `weekly_plan`, `week_preferences`, `hrv_log`, `weighin_log`, `workout_log`, `nudge_log`. It *consumes* (read-only): `governance_log` (vetoes from Bajrangi or future agents), the deadlift intent (Fraser will own this when Fraser ships).

**Why was the Scientist worth the refactor (vs. leaving it as the legacy monolith)?** Two reasons. First, it's the working reference for the next 19 agents — every shape Coach/Curriculum/Foodie use is established here. Second, the eval suite (142 cases) is proof the refactor is a true visible no-op; if a future agent breaks something, we know it's not because the Scientist drifted.

---

## 6. Now / Next / Later roadmap

The structure of this roadmap is itself a design choice: triggers, not calendar. We don't promote items to Now until the trigger condition lands.

### 6.1 Now — months 1–6 (shipped)

| Item | Status | LOC |
|---|---|---:|
| `core/io.py` — tool helpers | ✅ Shipped | 161 |
| `core/agent.py` — base contract | ✅ Shipped | 111 |
| `core/decisions.py` — trace log | ✅ Shipped | 197 |
| `core/charter.py` — policy plane | ✅ Shipped | 211 |
| `core/episodes.py` — episodic memory | ✅ Shipped | 226 |
| `core/miya.py` — orchestrator | ✅ Shipped | 280+ |
| `core/voice.py` — Hyderabadi voice layer | ✅ Shipped | 165 |
| `core/eval.py` — generalized harness | ✅ Shipped | 244 |
| Sports Scientist refactor (visible no-op) | ✅ Shipped | (existing) |
| `core/miya_main.py` + `com.rahat.miya.plist` (cutover artifacts) | ✅ Shipped | 30 + 50 |
| Eval suites (142 + 142 + 46 = 330 cases) | ✅ Green | — |

### 6.2 Next — months 6–12 (deferred, with explicit triggers)

| Item | Effort | Trigger to promote to Now |
|---|---|---|
| **Profile store** — `profile_facts(subject, key, value, …)` | ~3–4 hr | First agent that needs durable beliefs (likely Foodie). |
| **Semantic memory — sqlite-vss** | ~½ day | First concierge-class agent in active build (Japan trip recall). |
| **Event log + projection rebuild** | ~½ day | Want to replay 7 days against a new Miya routing strategy. |
| **Embedding-based agent retrieval** | ~½ day | LLM classifier misroutes at >15 overlapping agents. |
| **Cost & latency dashboard** | ~1 hr | Monthly LLM cost surprises. |
| **CLI** — `rahat status / tail / replay / eval` | ~2–3 hr | First time sshing into Mac to grep logs. |

### 6.3 Later — 12+ months

| Item | Effort | Trigger |
|---|---|---|
| **FastAPI gateway over Tailscale** | ~1 day | Mobile horizon enters next 90 days. |
| **Thin mobile client (SwiftUI/RN)** | ~1–2 weeks | Gateway stable for 30 days. |
| **Skill manifests + true registry (TOML)** | ~2–3 days | Third-party / marketplace agents arrive. |
| **Multi-tenant — `subject_id` everywhere** | ~1–2 days | Partner / toddler get their own subjects. |
| **Encrypted off-machine ledger mirror** | ~1 day | First "Mac Mini died" near-miss. |

### 6.4 The promotion rule (so this works without me)

For any future "should this be Now or Next?":

1. **Does at least one Now-window agent actually consume it?** If no → defer.
2. **If you don't build it, will multiple agents reinvent it?** If yes → pull forward.
3. **Is the surface contract clear and the cost <1 day?** If no → defer regardless.

Applied: episodic memory cleared all three (Curriculum/Coach/Scientist all consume it; multi-agent dup-tax was real; cost was 1.5 hr). Semantic memory cleared only #3 (no Now-window agent needs it). Profile cleared *none* in the corrected timeline.

---

## 7. Architecture decisions (ADR-style)

Each entry below is in the format an ARB review expects: context, decision, alternatives considered, consequences, revisit trigger.

### ADR-1: In-house slim runtime over off-the-shelf framework

**Context.** Need to run a multi-agent mesh with shared state and a single user-facing voice. Frameworks exist (LangGraph, AutoGen, CrewAI). Prevailing pattern in 2025 is "use a framework."

**Decision.** Build an in-house ~1,400-LOC core/ runtime.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| LangGraph | Cloud-resident state assumed; the graph IS the runtime; loses sovereignty thesis. |
| AutoGen | Multi-agent reasoning paradigm misaligned with deterministic-core design. |
| CrewAI | Production-immature in 2025; Pydantic-heavy for a domain that's mostly SQL. |

**Consequences.** We own the maintenance of the runtime. Tradeoff: ~1,400 LOC in `core/` (eval-pinned, well-tested) is less than the gym-prog parser inside the Scientist alone. Maintenance is bounded.

**Revisit trigger.** If a framework emerges that supports local-first SQLite-resident state and deterministic-core philosophy, evaluate then. Until then, no.

### ADR-2: SQLite as the system of record

**Context.** Need durable shared state across 20 agents with concurrent reads, occasional writes, semantic search later, and zero ops overhead.

**Decision.** Single SQLite file (`vault/rahat.db`) in WAL mode, with `sqlite-vss` for the eventual semantic layer.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| Postgres | Schema migrations as tax; server process; nothing the workload needs. |
| DuckDB | OLAP-optimized; SQLite better for the OLTP reads of agents. |
| LanceDB / Chroma + SQLite | Two stores = two backups, two consistency stories. |
| Plain JSON files | No transactions; concurrent writers corrupt; no ad-hoc queries. |

**Consequences.** All state lives in one queryable, backup-friendly file. Vector search lives there too via vss. Cost: SQLite single-writer constraint means we serialize writes; at the agent count and message rate involved (~hundreds/day), this is invisible.

**Revisit trigger.** If multi-machine persistence is needed (Later phase mobile or VPS mirror) we'll evaluate adding a Litestream replica or migrating to Postgres. Not before.

### ADR-3: Voice layer in Miya (orchestrator), not in agents

**Context.** PRD §3 says Miya speaks Dakhini-Hyderabadi. Should each agent's outputs be Dakhini, or should Miya transform on the way out?

**Decision.** Voice layer in `core/voice.py`, applied in Miya's outbound path. Agents stay factual.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| Each agent emits Hyderabadi text | 20× the eval test cases need updating. New agents start factual then "drift" Hyderabadi inconsistently. |
| LLM rewrites every reply in voice | Cost: ~$0.0001/msg × hundreds/day = ~$3/month, acceptable. Latency: 1–2s/reply, NOT acceptable for live coaching. |

**Consequences.** New agents inherit the voice for free. Eval substring assertions stay stable across agents. Cost: less variation than an LLM could produce; we live with that.

**Revisit trigger.** If users ask "can my Concierge speak French sometimes?" we'd add per-agent voice config. Not until then.

### ADR-4: Deterministic core, LLM at the edges

**Context.** Anyone building agents in 2025 is tempted to push everything to the LLM. The user ran a fitness journey on ChatGPT for 12 weeks and saw real failures (LLM fabricating timelines like "17 days to target" when the real answer was 14 weeks).

**Decision.** Load-bearing math (kcal targets, HRV bands, weekly plan, weight timeline) is deterministic Python with eval cases. LLM is the fallback for free-form coaching only, with explicit anti-hallucination rules in the prompt.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| LLM-only (chain-of-thought) | Repeated production bugs in user's own ChatGPT history. Not acceptable for health-adjacent coaching. |
| Pure determinism (no LLM at all) | Misses the long tail of Hindi/colloquial questions. 142 eval cases catch the head; the LLM handles the tail. |

**Consequences.** Higher LOC in domain code. Worth it: 142 test cases cover the deterministic surface; LLM costs are bounded to free-form messages.

**Revisit trigger.** If a future LLM proves reliable enough on health-arithmetic to remove the deterministic core — unlikely on the 5-year horizon.

### ADR-5: Heartbeat over prompts

**Context.** "Personal AI you have to prompt" is the failure mode of every existing tool. Rahat must operate proactively.

**Decision.** Minute-bucket scheduler inside Miya's loop. Each agent's `tick(now)` is called at most once per minute; the agent decides what to emit (or `[]` for nothing). Time-of-day gates (morning brief at 8am, recovery nudge at 9pm, weekly reset Sunday 23:55) are owned inside the agent's `tick`.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| Cron / external scheduler | Adds an external dependency; tied to clock-time triggers only (no "after 3 hammer days, suggest a deload" logic). |
| Event-driven only | Misses unprompted intelligence: 9pm recovery check, walk pace nudge. |

**Consequences.** Slight CPU baseline (always running). Negligible on Mac Mini idle.

**Revisit trigger.** If we go mobile and need battery awareness, we'd add adaptive heartbeat intervals.

### ADR-6: 6,500 → recalibration → "behind by N kcal" proposal

**Context.** User commits to weekly active-burn targets. Often falls behind midweek. Generic advice ("just hit it") doesn't help; specific picks ("convert Thu and Sun from rest → CrossFit") do.

**Decision.** Daily recalibration helper computes gap = remaining-to-goal − remaining-planned. When behind beyond 10% tolerance, propose specific rest-day-to-CF conversions in priority order (Mon, Wed, Fri, Tue, Thu, Sun — skip Sat which holds Z2). Wired into the morning briefing automatically.

**Alternatives.**

| Option | Why we rejected |
|---|---|
| Show "you're behind" without specifics | Same nag as a fitness app. User asks "what should I do?" anyway. |
| Auto-apply the redistribution | User must consent — `pick X Y for crossfit` is the explicit apply step. |

**Consequences.** User gets actionable picks, not vague encouragement. The morning brief becomes a coaching loop instead of a status report.

**Revisit trigger.** If users want auto-apply, add a "policy" that writes the picks to `week_preferences` after a confirmation prompt.

---

## 8. Reliability and operations

### 8.1 Eval framework

330 cases across three independent paths:

| Path | Cases | What it proves |
|---|---:|---|
| `eval_suite.py` | 142 | Legacy router behavior — every documented intent path |
| `eval_via_agent.py` | 142 | The Agent wrapper produces byte-identical outputs to the legacy path |
| `eval_extended.py` | 46 | 7-dimensional regression sweep: tick nudges, charter integration, state persistence, time-of-day correctness, edge cases, recalibration math, conversation invariants |

Each suite uses an isolated DB copy (no live DB pollution); time can be frozen (`_FrozenDatetime`); the LLM is stubbed for offline runs. Run all three in <30 seconds. CI gate-able.

The eval philosophy is **production-bug-driven**: every reported user bug becomes a permanent test case. The "scale-prep" Hyderabadi-routing fix added 12 cases (typos, Hindi, anti-hallucination); the morning-brief recalibration added 5; the no-plan fallback added 4.

### 8.2 Charter as policy gate

**Failure mode covered:** an agent emits a high-priority push at 11:30 PM during a sleep regression. Charter's `quiet_hours` policy blocks it. The veto is recorded in `governance_log`. The user is *not* woken.

**Failure mode covered:** the user's HRV is in the red band; Coach (when it ships) tries to push intensity. Charter's `hrv_red_blocks` policy vetoes. Coach gets the verdict back and falls through to a recovery suggestion instead.

**Failure mode covered:** Bajrangi (when it ships) writes a `governance_log` row vetoing all "performance" nudges for 24 hours. Charter's `external_veto_check` policy honors this — the Scientist's morning brief silently skips. No code coordination between Bajrangi and the Scientist needed; they communicate via the ledger.

### 8.3 Decision tracing

Every inbound message is one trace_id. The trace records:

- `miya.route` — which agent was picked, which strategy (`regex` / `regex+llm` / `llm-only`).
- `agent.<name>.route` — what the agent returned (text length, confidence).
- `charter.review` — the Charter verdict and reason.
- `io.telegram_send` — the actual send latency.

Walking a trace_id reconstructs the entire turn. Cost: ~5 rows × 50 messages/day = 250 rows/day, ~90k/year. SQLite handles this trivially. `rahat replay <trace_id>` is a Next-phase tool.

### 8.4 Failure modes acknowledged (and unmitigated)

| Failure | Current mitigation | Future mitigation |
|---|---|---|
| Mac Mini hardware dies | None — single-machine | Later: encrypted Litestream replica to a $5/month VPS. |
| SQLite WAL corruption | None — auto-recovery on next open | Later: nightly `sqlite3 .backup` to a separate volume. |
| Telegram API outage | Bounded retries + queue persists on Telegram side | None needed — message replay is automatic. |
| Gemini API outage / rate limit | LLM-fallback path returns "[llm-error]"; deterministic handlers unaffected | None needed at the Now scale. |
| Agent crashes | launchd KeepAlive restarts within 10s; trace records the failure | None needed; visible via `vault/miya.log`. |

---

## 9. Mobile path (Later)

When mobile is real (~12+ months):

1. **FastAPI gateway** running on the Mac Mini, exposed over Tailscale. Read-only endpoints for projections (today's plan, current weight, week so far). Auth = Sign-in-with-Apple → server-issued JWT bound to device.
2. **Thin SwiftUI/React Native client.** Reads from gateway; writes via the existing Telegram inbox initially (the bot stays as a fallback channel). Push notifications via APNs.
3. **The single discipline that makes this cheap NOW:** every agent talks to the Tool Broker (`core/io.py`), never directly to Telegram. When mobile arrives, we add a `push_notify` skill alongside `telegram_send`; agents don't change.

The cost of pre-paying for the mobile path is essentially zero today: Tool Broker is already there. The cost of *not* pre-paying — every agent hard-coded Telegram — would be a 20-agent rewrite when mobile lands.

---

## 10. Open questions and future work

These are decisions that don't need to be made now but will need to be made.

1. **Vector store at scale.** sqlite-vss is the planned starting point (Year 1) for ~10k semantic items. At ~50k items, evaluate lancedb (better for multimodal embeddings — photos, screenshots) or remain on vss. Decision deferred until corpus growth justifies it.
2. **Multi-tenant subject_id.** Cheap insurance: add `subject_id` columns to *new* projections in Now (5 min/table). Decision: do this for any new table going forward.
3. **Marketplace and external agents.** "Anyone can plug in an agent" implies signed manifests, scope checks, sandboxed execution. Investment scales with ambition: friend-of-family sharing < public marketplace. Defer until the user wants to share an agent with a specific person.
4. **Cost ceiling.** Gemini Flash at scale. At 50 messages/day and ~10% LLM-fallback rate, monthly cost is ~$0.15. At 200 messages/day and 20% fallback (more agents, more long-tail), ~$1.20. Daily cost dashboard (Next phase) catches the surprise.
5. **The Bajrangi agent.** Mentioned in the PRD as the "safety veto" — the agent that owns recovery, HRV, family priority. Not yet built. The Charter has the *enforcement* but Bajrangi will be the *advisor* (the agent that says "tomorrow should be a rest day"). Build when Coach (Fraser) ships, since the two together form the recovery-vs-performance dialectic.

---

## 11. Appendix: test inventory

```
agents/the_scientist/eval_suite.py        — 142 cases — legacy router
agents/the_scientist/eval_via_agent.py    — 142 cases — wrapper parity
agents/the_scientist/eval_extended.py     —  46 cases — 7-dim regression
                                          ─────
                                            330 — total, 100% passing
```

Categories in the legacy suite (A–W):

```
A. Daily burn lookups          (5)    L. LLM fallback                (2)
B. Weekly summary              (6)    M. Adversarial / colloquial    (10)
C. Current weight              (4)    N. Math correctness            (2)
D. Plan / schedule             (12)   O. Cadence-protection          (2)
E. Workout-today disambiguation(6)    P. State persistence           (4)
F. Per-week overrides          (24)   Q. Idempotency                 (2)
G. Weight + timeline           (10)   R. Number formatting           (3)
H. Coaching                    (14)   S. Day-specific lookup         (9)
I. Tier management             (3)    T. Typo tolerance              (4)
J. Manual logging              (4)    U. Hindi / Dakhini routing     (6)
K. Robustness                  (2)    V. LLM anti-hallucination      (2)
                                      W. Recalibration handler       (5)
                                      O.fallback: 4 in extended (B7)
```

Run all three in 30 seconds:

```bash
cd ~/developer/agency/rahat
python3 agents/the_scientist/eval_suite.py
python3 agents/the_scientist/eval_via_agent.py
python3 agents/the_scientist/eval_extended.py
```

CI-gate this trio; refuse to merge or deploy on red.

— End of architecture document —
