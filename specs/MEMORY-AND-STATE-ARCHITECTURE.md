# Memory & State — Mesh-Wide Architecture

**Author:** Claude (L8 Agent Architect)
**Date:** 2026-05-08 (rev 2)
**Status:** Proposal, awaiting green-light. Stops the reactive-patching cycle.
**Scope:** All agents on Rahat. Reusable mesh-wide primitives + per-agent adapters.

---

## TL;DR

Ten rounds of fixes have been band-aids. The real bug is structural: agents treat every Telegram message as a fresh stateless query, re-anchored each turn via prompt + tool catalog + ad-hoc context. Every persistent fact about the user (active goal, committed plan, weekday preferences, current tier, decisions made yesterday) has to be re-discovered by the model each turn.

This spec proposes a **two-layer memory architecture**:

  - **Layer 1 — Universal substrate** (mesh-wide, agent-agnostic): five primitives — `events`, `entities`, `threads`, `preferences`, `relationships` — that any agent uses. Single source of truth, single SQLite namespace, shared retention/observability.
  - **Layer 2 — Agent-specific adapters** (per agent, opt-in): each agent registers its own entity types and its own pre-prompt context assembler + post-reply state extractor. The Scientist tracks `goal`, `plan`, `tier`. Bajrangi tracks `recovery_protocol`, `sleep_concern`. A foodie agent tracks `cuisine_focus`, `meal_history`. Curriculum tracks `lesson`, `milestone`. None of them share a forced schema.

The substrate gives every agent the same memory primitives. The adapter gives every agent the freedom to model its own domain. Cross-agent queries (Miya routing, "user mentioned a Japan trip in Foodie last month") become possible without coupling.

Estimated cost: 5 days for the substrate + Scientist adapter, then ~½ day per future agent to plug in.

---

## 1. Why the previous draft was wrong

The first version proposed five Scientist-shaped tables (`active_goal`, `commitments`, `active_plan`, `preferences`, `threads`) as if they were universal. They aren't. Two examples:

  - **Bajrangi (HRV/sleep agent)** has no "goal" in the Scientist sense. It has a *current recovery concern* ("trending HRV crash since Tue") and a *protocol* ("low-intensity for 48h"). Those are real state that needs persistence, but they don't fit the Scientist schema.
  - **A future Foodie agent** has no "plan." It has a *cuisine focus this week* (e.g. "South Indian, low-salt"), a *meal history*, and *learned dietary preferences*. Again real state, again incompatible with the Scientist schema.

If we ship the Scientist schema as the universal one, every new agent will either contort to fit (bad) or get its own parallel schema (worse — fragmented memory across the mesh).

---

## 2. The two-layer architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Universal memory substrate (core/memory.py + new tables) │
│                                                                       │
│  Five primitives, agent-agnostic, shared:                            │
│                                                                       │
│  • events            — every meaningful occurrence (msg, reply,      │
│                        tool call, vital reading, decision); time-    │
│                        stamped, agent-scoped, optionally entity-     │
│                        tagged. The append-only firehose.             │
│  • entities          — first-class objects: any agent's "thing that  │
│                        has lifecycle and payload." JSON payload,     │
│                        type per agent. Status ∈ active / superseded  │
│                        / expired / archived. Lifecycle observable.   │
│  • threads           — conversation threads, scoped per (agent,      │
│                        topic). Holds summary, open questions,        │
│                        decisions. Auto-summarized.                   │
│  • preferences       — sticky k/v per agent. Confidence decay.        │
│  • relationships     — entity-to-entity links, can cross agents      │
│                        ("this Scientist commitment relates to that   │
│                        Bajrangi protocol"). Enables cross-agent      │
│                        reasoning.                                    │
│                                                                       │
│  API surface (all agent-scoped by default; cross-agent via Miya):    │
│    memory.put_entity(agent, type, payload, ...) -> entity_id          │
│    memory.list_entities(agent, type=..., status='active') -> [...]   │
│    memory.update_entity(entity_id, **fields)                         │
│    memory.add_event(agent, kind, payload, entity_ids=[...])          │
│    memory.recent_events(agent, since=..., kinds=[...]) -> [...]      │
│    memory.thread_for(agent, topic) -> thread                          │
│    memory.upsert_pref(agent, key, value, confidence=1.0)             │
│    memory.link(entity_a, entity_b, kind='references')                 │
└─────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ↓                           ↓                           ↓
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│ LAYER 2 ADAPTER │        │ LAYER 2 ADAPTER │        │ LAYER 2 ADAPTER │
│ Scientist       │        │ Bajrangi (HRV)  │        │ Foodie (cuisine)│
│                 │        │                 │        │                 │
│ entity types:   │        │ entity types:   │        │ entity types:   │
│   - goal        │        │   - recovery    │        │   - cuisine     │
│   - plan        │        │     protocol    │        │     focus       │
│   - commitment  │        │   - sleep       │        │   - meal_log    │
│   - tier_change │        │     concern     │        │   - dietary     │
│                 │        │   - vitals      │        │     phase       │
│                 │        │     window      │        │                 │
│                 │        │                 │        │                 │
│ assembler():    │        │ assembler():    │        │ assembler():    │
│   builds Coach  │        │   builds HRV    │        │   builds food   │
│   state block   │        │   state block   │        │   state block   │
│                 │        │                 │        │                 │
│ extractor():    │        │ extractor():    │        │ extractor():    │
│   parses turn → │        │   parses turn → │        │   parses turn → │
│   goal/plan/    │        │   protocol/     │        │   meal/cuisine/ │
│   commitment    │        │   concern updates│        │   pref updates  │
└─────────────────┘        └─────────────────┘        └─────────────────┘
```

What's universal vs agent-specific:

  - **Universal:** the substrate, the API, the lifecycle (active/superseded/expired/archived), the observability (every memory write goes to the decisions ledger), the retention policy.
  - **Agent-specific:** which entity types to define, what the context block looks like, how to extract state from a turn. None of these are forced — an agent that doesn't need entity X simply doesn't register it.

---

## 3. Schema (Layer 1)

Five tables, all SQLite, all small:

```sql
-- The append-only firehose. Every message, reply, tool call, vital
-- reading, decision-made — anything an agent or the runtime cares
-- about. The decisions ledger we already have can be a view over this
-- with kind='decision' for back-compat.
CREATE TABLE memory_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            DATETIME DEFAULT CURRENT_TIMESTAMP,
    agent         TEXT NOT NULL,         -- 'scientist' | 'bajrangi' | 'miya' | ...
    kind          TEXT NOT NULL,         -- agent-defined: 'msg.in' | 'msg.out' | 'tool.call' | 'commitment.made' | 'vital.hrv' | ...
    payload       TEXT,                  -- JSON
    actor         TEXT,                  -- who/what triggered (user | agent | tick | sensor)
    related_ids   TEXT,                  -- JSON list of entity_ids touched
    trace_id      TEXT                   -- ties multi-step decisions
);
CREATE INDEX events_agent_ts ON memory_events(agent, ts DESC);
CREATE INDEX events_kind_ts  ON memory_events(kind, ts DESC);

-- First-class objects with lifecycle. Each agent defines its own types.
CREATE TABLE memory_entities (
    entity_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    type           TEXT NOT NULL,        -- agent-defined: 'goal' | 'plan' | 'protocol' | ...
    payload        TEXT NOT NULL,        -- JSON, agent-specific shape
    status         TEXT DEFAULT 'active', -- active | superseded | expired | archived
    valid_from     DATETIME DEFAULT CURRENT_TIMESTAMP,
    valid_until    DATETIME,             -- NULL = indefinite
    superseded_by  INTEGER,              -- FK self-ref when a newer entity replaces this
    rationale      TEXT,                 -- "user committed to this on May 8 after pushback"
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX entities_agent_type ON memory_entities(agent, type, status);
CREATE INDEX entities_active     ON memory_entities(agent, status, valid_until);

-- Conversation threads. Topic + summary + open questions + decisions.
CREATE TABLE memory_threads (
    thread_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent          TEXT NOT NULL,
    topic          TEXT NOT NULL,
    started_at     DATETIME NOT NULL,
    last_active_at DATETIME NOT NULL,
    summary        TEXT,
    open_questions TEXT,                 -- JSON
    decisions      TEXT,                 -- JSON
    status         TEXT DEFAULT 'open'   -- open | resolved | abandoned
);

-- Sticky preferences per agent.
CREATE TABLE memory_preferences (
    agent          TEXT NOT NULL,
    key            TEXT NOT NULL,
    value          TEXT NOT NULL,        -- JSON or scalar
    confidence     REAL DEFAULT 1.0,     -- decays over time without reinforcement
    learned_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent, key)
);

-- Cross-entity links. Lets Miya correlate "this Scientist commitment
-- relates to that Bajrangi recovery protocol."
CREATE TABLE memory_relationships (
    rel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a      INTEGER NOT NULL,
    entity_b      INTEGER NOT NULL,
    kind          TEXT NOT NULL,         -- 'references' | 'caused_by' | 'supersedes' | 'about'
    metadata      TEXT,                  -- JSON
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Five tables, ~50 lines of DDL. Auto-migrate on first import.

---

## 4. Layer 2 — agent adapters

Each agent that wants memory writes a small adapter at `agents/<name>/memory.py`. The contract:

```python
# Each agent declares its entity types (just for documentation +
# linting; the substrate doesn't enforce them).
class ScientistEntityTypes:
    GOAL        = "goal"
    PLAN        = "plan"
    COMMITMENT  = "commitment"
    TIER_CHANGE = "tier_change"


def assemble_context(agent: str = "scientist") -> str:
    """Build the agent's state block for the reasoner.
    Pure-Python, deterministic. No LLM call here."""
    today = datetime.now().strftime("%A, %B %-d, %Y")
    blocks = [f"[Today: {today}]"]

    # Each agent reads its own entity types via the universal API.
    goal = memory.list_entities(agent, type="goal", status="active")
    if goal:
        g = goal[0].payload
        blocks.append(f"[Active goal: {g['target_lbs']} lbs by "
                      f"{g['target_date']} — daily intake {g['daily_intake']}, "
                      f"weekly active {g['weekly_active']}, tier {g['tier']}]")

    commits = memory.list_entities(agent, type="commitment", status="active")
    if commits:
        # ...
        pass

    plan = memory.list_entities(agent, type="plan", status="active")
    if plan:
        # ...
        pass

    # And so on. Each agent surfaces what's relevant to ITS domain.
    return "\n".join(blocks)


def extract_state(user_msg: str, bot_reply: str) -> None:
    """Read a single turn and write any state changes to the substrate.
    Uses a small Gemini Flash call (~$0.0001) to parse intent."""
    # Prompt the extractor with the agent's known entity types:
    prompt = (f"User: {user_msg}\n\nCoach: {bot_reply}\n\n"
              "Extract any new commitments, goal changes, plan choices, "
              f"or learned preferences for the {agent} agent. Output JSON "
              f"matching schema: {ScientistEntityTypes.SCHEMA}")
    parsed = gemini_flash_json(prompt)
    if parsed.get("new_goal"):
        memory.put_entity(agent, "goal", parsed["new_goal"],
                          rationale=parsed.get("rationale"))
    if parsed.get("new_commitments"):
        for c in parsed["new_commitments"]:
            memory.put_entity(agent, "commitment", c,
                              valid_until=c.get("expires_at"))
    # And so on.
```

Bajrangi's adapter would look completely different (smaller, no goal/plan, just `recovery_protocol` + `sleep_concern`). Foodie's would have `cuisine_focus` + `meal_log`. Same substrate, completely different domain modeling.

---

## 5. Reasoner integration

Each agent's reasoner gets two new lines:

```python
def reason(msg: str, ...):
    # NEW: pull state from this agent's adapter
    from agents.the_scientist.memory import assemble_context, extract_state
    state_block = assemble_context()
    framed_msg = f"{state_block}\n\n{msg}"
    messages = [{"role": "user", "content": framed_msg}]

    # ... existing tool-using loop ...

    # NEW: write state changes from this turn
    extract_state(msg, last_text)
    return voice.dress(last_text, kind="status")
```

The 60-min ledger lookback I added in the previous round becomes redundant — the assembler now reads structured state, not raw chat history.

---

## 6. What this fixes vs current bug pattern

Same as the rev-1 doc, but now the fix is mesh-wide, not Scientist-only:

| Bug class | Reactive fix I'd been adding | Layered architecture fix |
|---|---|---|
| Bot loses preferences across messages | 60-min ledger lookback | `memory_preferences` (agent-scoped, sticky) |
| Bot ignores user's committed weekly target | `target_kcal_for_week` parameter | `memory_entities(type='commitment')` — assembler injects always-active commitments |
| Bot lectures after user commits | "anti-lecture" prompt rule | State block shows commitment with timestamp; model knows the warning was given |
| Bot puts Friday as Rest after user said CF | Conversation history prepend | `memory_entities(type='plan', source='user_committed')` |
| Bot uses wrong year for "05/18" | `[Today:]` prefix injection | Stays — universal across agents |
| Bot's plan totals don't match | "verify totals" prompt rule | Tools become rarer because state is preloaded; deterministic post-check on plan totals |
| Bot says "you've hit the target" at 686/6000 | "no false celebration" prompt rule | Assembler shows `[This week: 686/6000]` — model can't credibly claim the gap doesn't exist |
| Bajrangi's "current recovery concern" forgotten between sessions | (would need its own patch) | Same substrate; Bajrangi's adapter just defines its own entity types |
| Foodie agent's "cuisine focus this week" forgotten | (would need its own patch) | Same substrate; Foodie defines `cuisine_focus` entities |

The shift: **stop telling each agent's model what to remember. Show it the remembered state via a per-agent adapter over a shared substrate.**

---

## 7. Cross-agent reasoning (the bonus)

Because the substrate is unified, Miya (the orchestrator) gains powerful cross-agent capabilities for free:

  - "User mentioned a Japan trip 3 weeks ago to the Foodie agent — surface that to the Scientist when they ask about jet lag recovery." → `memory.list_entities(type='trip', status='active')` returns the Foodie's trip entity.
  - "User committed to a hammer week with the Scientist on May 8 — Bajrangi should know to flag HRV more aggressively this week." → cross-agent commitment query.
  - "User's preferences across agents" — `memory.list_preferences(agent=any)` aggregates.

These weren't possible with the rev-1 Scientist-only schema. They cost nothing in the rev-2 substrate.

---

## 8. Migration plan (revised)

**Day 1 — Substrate.** Build `core/memory.py` (the five primitives + their DAL). 5 tables, ~250 LOC. Auto-migrate. Unit-tested.

**Day 2 — Scientist adapter.** Define entity types (`goal`, `plan`, `commitment`, `tier_change`). Write assembler that reads them from the substrate. Write extractor that parses Scientist replies.

**Day 3 — Reasoner integration + backfill.** Wire assembler/extractor into `reasoner.py`. One-time script walks last 30 days of decisions ledger and infers state retroactively (so the assembler isn't empty on day 1).

**Day 4 — Eval suite + observability.** Add B10 cases for assembler correctness given seeded state. Add B11 cases for extractor correctness on canonical conversations. Add a `/memory <agent>` debug endpoint that dumps current state.

**Day 5 — Hardening.** Cross-agent ergonomics test: create a stub Foodie adapter to confirm the substrate composes. Soak the Scientist with the new architecture for 24h. Roll back hatch via `RAHAT_LEGACY_DISPATCH=1` stays available.

After day 5: each future agent (Bajrangi, Curriculum, Foodie, Japan-recall) takes ~½ day to add — define entity types, write a small adapter, reuse the substrate. No new tables. No new patterns.

---

## 9. What this does NOT do (deferred or out-of-scope)

  - **Vector / semantic retrieval.** Returning recent + active state is a good approximation; semantic search over past conversations (sqlite-vss or similar) is a follow-up Now-Next.
  - **Long-term episodic compression / GraphRAG.** Threads have summaries that get regenerated; we don't yet build a graph or do hierarchical summarization.
  - **Cross-agent permission model.** Default is agent-scoped reads; cross-agent reads go through Miya. A formal capability/permission model is a follow-up when the mesh has 5+ agents.
  - **Memory archiving / retention policies.** All five tables grow forever today. SQLite handles 100M+ rows fine for our scale, but we'll want auto-archive to a "cold" memory file when the live db crosses ~1M rows.

---

## 10. The decision

**Path A — full substrate + Scientist adapter (5 days).** Mesh-wide foundation, ready for every future agent. Recommended.

**Path B — Scientist-only memory now, generalize later (3 days).** Faster but bakes Scientist assumptions in; we'd refactor when Bajrangi or Curriculum lands.

**Path C — keep patching.** Bug rate stays high; structural fragility persists.

I recommend Path A. It's only +2 days vs Path B, and it sets up every future agent for ~½ day onboarding instead of inheriting another rebuild.

If you greenlight Path A, I start Day 1 (substrate) immediately and ship phase-by-phase with eval gates between days. The legacy flag stays available the whole time.

Tell me which.
