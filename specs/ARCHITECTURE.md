# Rahat: the platform under Miya, as built

**Miya is my personal AI agent. Rahat is the platform underneath it:** the doors messages come in by, the rules in code that meet them, the specialist agents, the checks between an answer and me, the memory they share, and the jobs that keep it fed.

**Version:** 3.0 · 2026-10-17
**Author:** Venkat Sadras
**Status:** In production on one Mac mini, for one owner and his household: three front doors, six agents, eight launchd services. Every section says what is shipped and what is not built.

> **See it move.** Two interactive pages tell this document's story in pictures you can click:
>
> - **[Rahat Architecture](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html)**: the whole system on one map. Hover any box for what happens there, watch eight moments move through it, drop real messages through the routing ladder, and play a day of scheduled jobs.
> - **[Rahat Harness Architecture](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/ADR-018.html)**: inside one agent, the eight steps every Bourdain answer runs ([ADR-018](./ADR-018-outcome-contracts-and-independent-evaluation.md)).
>
> Their sources are [`RAHAT-ARCHITECTURE.html`](./RAHAT-ARCHITECTURE.html) and [`ADR-018.html`](./ADR-018.html), beside this file. GitHub shows those files as source; the links above open them as pages, and so does any browser.

> **v3.0 update note (2026-10-17).** v2.0 (May 2026) described a model-first Kobe and a memory design. This version is rewritten as built, by Architect A (platform lane), and checked against the code and the records on October 17; every count carries its date (Appendix B). What changed since v2.0:
>
> 1. **Three front doors on the new-plane runners** ([ADR-013](./ADR-013_migrate_to_new_plane.md)): Miya on the Miya bot for the owner; Genie and Bourdain on their own bots for the household.
> 2. **Rules before models.** Sixteen ordered rules in code claim the messages they recognize; only what none of them claims reaches Miya's model. Inside Kobe, a single ordered dispatcher answers what it recognizes before the reasoner runs, so v2.0's "every inbound message goes to the reasoner first" no longer holds.
> 3. **Checks in the path.** Every reply Miya sends, a specialist's included, goes through one sink: the validator, a never-empty guard and the Charter. Fraser's designs pass a ten-point contract. Bourdain's picks are verified in code.
> 4. **The harness** ([ADR-018](./ADR-018-outcome-contracts-and-independent-evaluation.md)): since October 4 every Bourdain answer runs eight steps, with outcome contracts checked in code, a judge in shadow and golden sets that replay for $0.
> 5. **A knowledge base with provenance** under Bourdain, an open-data gazetteer of 21,555 places, twelve food feeds read every night without a model, and Genie's events from 122 sources.
> 6. **The system reports its own breakage** (the 08:15 health line) **and prices every call that goes through the wire** (the cost ledger).
> 7. **4,209 hermetic tests** (v2.0 had 475 eval cases), a regression pin for every fix, and gates on push, on merge and in CI.
>
> Two v2.0 statements were wrong by October and are corrected here: memory consolidation is not scheduled (not since May), and not every model call goes through one gate (three older paths call the model directly, §10.1). The v2.0 drawings are kept in `archive/specs/diagrams/`; the pages above replace them. [`specs/README.md`](./README.md) indexes this folder.

---

## Contents

1. [Executive summary](#1-executive-summary)
2. [Context and constraints](#2-context-and-constraints)
3. [Principles](#3-principles)
4. [The system as built](#4-the-system-as-built)
5. [How a message flows](#5-how-a-message-flows)
6. [The agents](#6-the-agents)
7. [The harness](#7-the-harness-adr-018)
8. [Data plane: the vault](#8-data-plane-the-vault)
9. [Ingestion and scheduled work](#9-ingestion-and-scheduled-work)
10. [Control plane](#10-control-plane)
11. [Reliability and operations](#11-reliability-and-operations)
12. [Testing and the rails](#12-testing-and-the-rails)
13. [Security and privacy](#13-security-and-privacy)
14. [Architecture decisions](#14-architecture-decisions)
15. [Now, Next, Later](#15-now-next-later)
16. [Open questions and known gaps](#16-open-questions-and-known-gaps)
- [Appendix A. Where the code lives](#appendix-a-where-the-code-lives)
- [Appendix B. Counts, dated](#appendix-b-counts-dated)

---

## 1. Executive summary

Rahat is the platform under Miya, a personal AI agent. It runs on one Mac mini at home, keeps its state in one SQLite file, and serves one owner and his household through three Telegram bots. Miya is the one place the owner asks; specialists answer behind it; two agents also have doors of their own for the household.

**What runs today (October 17):**

- **Three front doors.** The Miya bot (Miya, with Kobe, Fraser, Huberman and Genie behind it), the Genie bot and the Bourdain bot.
- **Six agents.** Miya (the orchestrator), Kobe (the week's numbers), Fraser (workout design), Huberman (cooldowns), Genie (household weekends and events) and Bourdain (where to eat).
- **Rules before models.** A 16-rung ladder in code routes Miya's messages; Kobe's dispatcher and Genie's intents answer what they recognize in code; Bourdain's grammar answers its lookups in code.
- **Checks before the owner sees anything.** One governance sink for every Miya reply (§5.1), Fraser's design contract, Bourdain's verify step, and the Charter's policies.
- **One harness for agents** (§7), serving Bourdain since October 4.
- **One record.** A decisions ledger with a trace per message, a cost ledger with every billable event priced, harness runs and per-outcome results, judge verdicts, and the Charter's log.
- **Work that happens while nobody types.** A minute tick sends the morning brief, the nudges, the 9:30 pm cooldown and the 08:15 health line; event runs three times a day; the food run at 05:30.
- **The rails.** 4,209 hermetic tests; a fix commit without a new regression pin is refused at merge; a push carrying a private word is blocked.

**What isn't built:**

- A loop that changes behaviour from outcomes ([ADR-015](./ADR-015_outcome_validated_learning_engine.md) is design-only).
- Memory that weights recall by what changed an outcome; scheduled memory consolidation.
- Bourdain's database-first answer path (the knowledge base answers first, the web only when it comes up short).
- Authority for the judge (it grades in shadow until labels exist).
- A second harness tenant (parked by the owner on October 11).
- A source and date on every fact beyond Bourdain's.
- One wire for every model call (three older paths call the model directly).

**Scale, honestly.** One household. Bourdain and Fraser made 22 harness runs between October 4 and 11. Nothing here is a benchmark; it is a system that runs in one life every day.

---

## 2. Context and constraints

### 2.1 The thesis

Personal AI today is reactive: the user prompts, the model answers, neither remembers, neither acts. Rahat takes the position that the useful version is proactive and stateful:

- **No prompt for routine work.** The morning brief, the nudges, the 9:30 pm cooldown and the event digest arrive on their own.
- **State, not turns.** The system carries context across days and weeks: plans, places, preferences, what was said.
- **Agents are roles, not prompts.** Each agent owns a domain, its data and its checks; a model is one of its tools.

### 2.2 Who it serves

One owner, who talks to Miya; his household, who use Genie from their own phones, and Bourdain. Every concrete personal value (profiles, household rules, home, health data) lives only in the vault on the Mac mini. The public repository carries the code and synthetic tests, never the vault.

### 2.3 Hard constraints

1. **Sovereignty.** State lives on the Mac mini. Cloud APIs (Gemini, Telegram, open-data services) are tools, not homes for state. §13 lists exactly what leaves the machine.
2. **One voice per door.** Miya is the one place the owner asks; specialists never talk to him directly through the Miya bot.
3. **Deterministic where it matters** ([ADR-011](./ADR-011-deterministic-shell-llm-core.md)). State, arithmetic, routing and vetoes are code. Intent, taste and phrasing are the model's.
4. **Every change gated.** Hermetic tests on every push; a regression pin for every fix; nothing red ships.
5. **A public repo with a private life.** No personal data in commits or tests; the pre-push gate blocks a push that would publish a private word.

### 2.4 Why not a framework

LangGraph, AutoGen and CrewAI were evaluated early and rejected for the same reason: they assume cloud-resident state, make the graph the runtime, and produce reasoning chains rather than code-checked contracts. Rahat's runtime is small and its own. By October the market had converged on much of the same shape (a tool loop with caps, code that decides before a model does, schema-validated outputs, evals graded on outcomes), and [ADR-018](./ADR-018-outcome-contracts-and-independent-evaluation.md) adopts that shape. What no framework enforces, "no claim without a source", is Rahat's to build. OpenClaw stays a parked adapter, not the runtime ([ADR-014](./ADR-014_openclaw_position.md)).

---

## 3. Principles

Each principle is paired with the alternative rejected and the cost accepted.

| Principle | Rejected alternative | Cost accepted |
|---|---|---|
| **Local-first sovereignty** | Cloud-resident state with encryption | One machine, no failover (§11). |
| **Deterministic shell, LLM core** ([ADR-011](./ADR-011-deterministic-shell-llm-core.md)) | The model plans and computes everything | More code for facts and arithmetic; the model gets intent, taste and phrasing. |
| **Rules before models** | A model routes every message | A ladder to maintain, ordered with care; a miss falls through to Miya's model, not to a wrong agent. |
| **Checks in the path** | Review after sending; trust the model | A reply can be held, rewritten, repaired once, or sent with a plain "not verified" line. |
| **One record** | Per-agent logs | A row per step; any turn can be reconstructed from its trace id. |
| **Heartbeat over prompts** | A cron job per feature, or prompt-only | An always-on runner with a minute tick. |
| **Every fix ships a pin** | Fix and forget | 219 regression pins and a merge gate that refuses a fix without a new one. |
| **Say what isn't verified** | A confident guess | Replies carry gap lines and fall back to floors (a library session, an offline plan). |
| **Frictionless setup** | Per-machine instructions | Templated launchd plists, `.env.example`, `bootstrap.sh`; no tracked file holds a machine path. |

---

## 4. The system as built

### 4.1 The map

```
  Miya bot              Genie bot               Bourdain bot
  the owner             the household           the household
       │                       │                       │
┌──────▼──────────────┐ ┌──────▼──────────────┐ ┌──────▼──────────────┐
│ miya.v2             │ │ genie               │ │ bourdain            │
│ the ladder: 16      │ │ household gate,     │ │ grammar: a lookup   │
│ rules in code       │ │ Genie's intents     │ │ or an ask           │
├─────────────────────┤ ├─────────────────────┤ ├─────────────────────┤
│ Miya's model, or    │ │ Genie: weekends,    │ │ Bourdain on the     │
│ Kobe · Fraser ·     │ │ the calendar,       │ │ harness: 8 steps    │
│ Huberman · Genie    │ │ events              │ │                     │
├─────────────────────┤ ├─────────────────────┤ ├─────────────────────┤
│ validator, Fraser's │ │ Genie's rules       │ │ verify in code,     │
│ contract, Charter   │ │                     │ │ judge in shadow     │
└──────┬──────────────┘ └──────┬──────────────┘ └──────┬──────────────┘
       └───────────────────────┴ back out on Telegram ─┘

┌─────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  the model wire (Gemini Flash, Pro on hard prompts;   │
│ Search and Maps grounding) · the Charter · the decisions ledger ·   │
│ the cost ledger · the 08:15 health line · 4,209 tests, push and     │
│ merge gates                                                         │
├─────────────────────────────────────────────────────────────────────┤
│ THE VAULT  vault/rahat.db, one SQLite file, with JSON beside it:    │
│ health data · the gym's week · events · the knowledge base ·        │
│ memory · scoped state · records · profiles and plans                │
└──────▲────────────────▲──────────────▲────────────────────▲─────────┘
   vitals.v2       sugar.bridge      events            food.nightly
   iPhone, Watch   the gym's app     3 runs a day      05:30, no model
   always on       always on         122 sources       12 feeds
```

The interactive [Rahat Architecture](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html) page draws the same map with every box explained and eight moments animated.

### 4.2 Services

Eight launchd services on the Mac mini. The always-on ones restart on exit (`KeepAlive`).

| Service | Runs | What it does | Code |
|---|---|---|---|
| `com.rahat.miya.v2` | Always on | The Miya bot: long-polls Telegram, routes through the ladder, runs Miya and the minute tick | [`new_plane/miya_runner/`](../new_plane/miya_runner/) |
| `com.rahat.genie` | Always on | The Genie bot: pairing, Genie's commands and questions, the 08:00 digest | [`new_plane/genie_runner/`](../new_plane/genie_runner/) |
| `com.rahat.bourdain` | Always on | The Bourdain bot: pairing, the grammar, the harness, shared locations | [`new_plane/bourdain_runner/`](../new_plane/bourdain_runner/) |
| `com.rahat.vitals.v2` | Always on | The health bridge on port 5000, behind a key: the phone's HealthKit exports, and a location push for Bourdain | [`bridges/healthkit/`](../bridges/healthkit/) |
| `com.rahat.sugar.bridge` | Always on | The gym bridge: the week's programming, sent from the gym's app | [`bridges/sugarwod/`](../bridges/sugarwod/) |
| `com.rahat.events` | 07:00, 12:30, 18:00 | Reads the event sources, dedupes and tags them into the events inventory | [`bridges/events/`](../bridges/events/) |
| `com.rahat.events.search` | Wed and Sat, 03:00 | The paid grounded-search pass for events no feed covers (also on demand: Genie's `/refresh`) | [`bridges/events/`](../bridges/events/) |
| `com.rahat.food.nightly` | 05:30 | Twelve food feeds and places' own pages into the knowledge base, without a model | [`bridges/food/`](../bridges/food/) |

Built and templated, **not scheduled yet**: the nightly golden-set replay (03:10), the weekly report (Sunday 18:05), the command screener (Sunday 18:20) and the harness shadow diff (21:35), all under [`scripts/`](../scripts/); and memory consolidation (`scripts/memory_consolidate.py`), unscheduled since May.

### 4.3 The three planes

v2.0's decomposition still holds, with October's contents:

- **Control plane: what may happen, and what is written down.** The Charter (policies), the model wire (timeouts, retries, the budget gate, the harness's per-ask brake), the checks (validator, Fraser's contract, the harness's verify and judge), the decisions and cost ledgers, the health line, and the registry of agents and manifests. §10.
- **Data plane: what the system knows.** One SQLite file and the JSON beside it. §8.
- **Runtime plane: what runs.** The three runners and their minute ticks, the agents, the harness's steps, the bridges and the scheduled feeds. §5, §6, §7, §9.

---

## 5. How a message flows

### 5.1 Miya (the Miya bot)

1. **Receive.** The runner long-polls Telegram and gives every message a trace id.
2. **The ladder** ([`delegate_classifier.py`](../new_plane/miya_runner/delegate_classifier.py)). Rules in code, checked top to bottom; the first that claims the message decides who answers.

| Rung | Claims | Goes to |
|---|---|---|
| 1 | An address: `@kobe`, `@fraser`, `@huberman`, `@genie`, `@miya`, or `/kobe`, `/fraser`, `/huberman`, `/miya` | That agent (`@miya` asks for Miya's own answer) |
| 2a | Genie's commands: `/genie`, `/weekend_plan`, `/family_log` | Genie |
| 2 | Any other slash command | Kobe |
| 2b | A what-if about the owner's numbers | Miya's model, with Kobe's facts and no writes |
| 3 | Plan changes | Kobe |
| 4 | Logs | Kobe |
| 4a | The owner's own numbers: typical burns, a weekly target | Kobe |
| 4a½ | A cooldown request | Huberman |
| 4b | Design intent ("design a workout") | Miya, which hands it to Fraser's design |
| 5 | Status questions | Kobe |
| 5b | Plan views | Kobe |
| 6 | Profile changes | Kobe |
| 6b | Cooldown, stretch, mobility | Huberman |
| 7 | Recovery protocols | Kobe |
| 8 | The gym's workout | Kobe, verbatim, no model |
| 8a | Weekend and household planning | Genie |
| — | Nothing claims it | Miya's model |

The plain-language intent layer ([ADR-017](./ADR-017_nl_intent_layer.md)) sits just above the default, behind a flag that is off. The [interactive page](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html) drops eight real messages through these rungs; each lands where `classify_delegation` sends it.

3. **Continuation.** A short reply goes back to whoever asked: to Kobe when its reasoner asked a question, to Huberman when its 9:30 pm message asked whether the owner trained, and to Fraser while a design is under discussion (twelve hours).
4. **A specialist's reply goes through one sink** (`_finalize_delegated` in [`orchestrator.py`](../new_plane/miya_runner/orchestrator.py)): voice leaks scrubbed; the reply re-voiced by Miya's synthesizer unless it is an exact answer from code (Kobe's dispatcher, Genie), which goes out word for word; the validator; a never-empty guard; the Charter; then it is published and recorded. One sink makes "every reply is governed the same way" a property of the code rather than a review checklist.
5. **What no rung claims** goes to Miya's orchestration: facts fetched from the specialists within a per-turn budget, arbitration when they disagree, a Charter precheck, a model chosen by the cost router (Gemini 2.5 Flash by default, Pro for hard prompts), one synthesized reply, and the validator, which corrects any claim that something changed when the turn wrote nothing.

**Inside Kobe** ([`agents/the_scientist/handler.py`](../agents/the_scientist/handler.py)): the single ordered dispatcher ([ADR-009](./ADR-009-single-dispatcher.md), on by default) answers what it recognizes in code; slash commands answer from Kobe's command table; Fraser's and Huberman's territory is delegated ([ADR-007](./ADR-007-cross-agent-delegation.md)); a reasoner with tools takes the rest; a reasoner failure falls back to the legacy rules. Checked on October 17: `/week`, "how many calories did I burn yesterday" and "what's the WOD tomorrow" are all answered in code, with no model call.

### 5.2 Genie (its own bot, and behind Miya)

- **The household gate.** Only paired phones get in; a pairing link adds one.
- **Routing in code:** slash commands, then precise intents (go with, swap, why not, replan, what's on), then weekend-plan intents (who it's for, how much energy, how many options), then a greeting. The deterministic surface always answers.
- **A weekend plan** takes live discovery through `core.llm` with search grounding, then deterministic day sequencing. A plan never puts two things in one slot; on a Sunday it plans today alone; when live search is down the reply says so and offers the offline plan.
- **The 08:00 digest** comes from the events inventory: at most six picks a day, no more than three from one source, chosen rather than first-come, with a line when the feeds are stale.
- Behind Miya, Genie's replies go out as Genie wrote them; they are not re-voiced.

### 5.3 Bourdain (its own bot)

- **The grammar** ([`manifest.json`](../agents/bourdain/manifest.json)): fifteen commands and eight exact replies. Lookups (`/list`, `/who`, `/where`, `/cost`, `/kb`, `/help`, `/join`, `/household`) are answered by code from the knowledge base and the harness's own session. Asks (`/eat`, `/coffee`, `/coffee_gf`, `/dessert`, `/save`, "more", "which one", "something else", "just me", "going") are handed to the harness as the words they stand for, so one brain with one memory answers every ask (October 17).
- **Where you are:** a location shared in Telegram (once, or live until the owner stops it, throttled to meaningful moves), a push from the phone to the health bridge, or a town named in words. It is kept for three hours in scoped state; after that the origin is home. Only the place's label is logged.
- **Everything else** is the harness: §7.

### 5.4 Telegram

- A reply Telegram refuses for its formatting goes again as plain text; long replies are split.
- A scheduled message that doesn't land is tried again every minute for up to 30 minutes, then dropped and logged; the health line counts lost messages.
- Each bot publishes its slash-command menu to Telegram when it starts.

---

## 6. The agents

| Agent | Door | Owns | How it answers | Status |
|---|---|---|---|---|
| **Miya** | The Miya bot | The conversation | The ladder, then a specialist or Miya's synthesizer; one voice | Live |
| **Kobe** | Behind Miya | The week's numbers, the plan, the gym's workout | Dispatcher and commands in code; delegation; a reasoner with tools | Live |
| **Fraser** | Behind Miya | Workout design | Constraints read in code; the model writes; a ten-check contract, one repair pass; outcomes recorded | Live |
| **Huberman** | Behind Miya, and the 9:30 pm tick | Cooldowns | A 25-drill library with rotation; the model coaches each drill; the library alone when the model can't answer. A run is read from the Watch's minute data hours before the workout export, and gets the post-run session | Live since Aug 24 |
| **Genie** | Its own bot, and behind Miya | Weekends, the household calendar, the events digest | Intents in code; live discovery with search; the digest from the inventory | Live; behind Miya since Aug 6 |
| **Bourdain** | Its own bot | Where to eat | The harness | Live since Sep 20; on the harness since Oct 4 |

Code: [`agents/the_scientist/`](../agents/the_scientist/) (Kobe), [`agents/fraser/`](../agents/fraser/), [`agents/huberman/`](../agents/huberman/), [`agents/genie/`](../agents/genie/) and [`agents/bourdain/`](../agents/bourdain/). One more agent lives in a private build lane and is deliberately outside these public docs; it runs on the same substrate documented here.

**How an agent joins.** It starts on its own front door, with its own bot and tests, where its failures are easy to see and can't break anything else. When it passes, it is registered behind Miya in [`agent_boot.py`](../new_plane/miya_runner/agent_boot.py). An agent registered since June 17 must emit numeric facts as typed fields (`emits_structured_facts`); the contract test refuses one that doesn't. The front door's own judged criteria are written ([`core/checklists/front_door.json`](../core/checklists/front_door.json)) and not yet running.

---

## 7. The harness (ADR-018)

One loop for an agent's answer, owned by the platform: the model reads and researches, code decides what counts as done, and a second model grades judgment once it has earned a say. The [harness page](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/ADR-018.html) animates a message through it.

A strict command check runs first ([`core/inbound.py`](../core/inbound.py)); then, in [`core/harness/run.py`](../core/harness/run.py):

| Step | Who | What happens |
|---|---|---|
| 1. Understand | Model (the cheaper one) | A typed reading of the message (the agent's frame schema), refined by rules in code |
| 2. Contract | Code | The outcomes this answer must meet, drawn from the agent's outcome library (Bourdain: 52 outcomes, 3 always required) |
| 3. Execute | Model with tools | Research with the agent's allow-listed tools under a brake (Bourdain: 12 tool calls, 10 searches and $0.50 an ask; a wide ask gets 18, 14 and $0.80); the evidence comes back as records, not prose |
| 4. Verify | Code | Every pick checked against its evidence: open when you'd arrive, inside the travel time, each diet confirmed |
| 5. Judge | A second model | Grades judgment against a 21-criterion checklist; in shadow, without authority |
| 6. Control | Code | Accept, repair once, or fall to the floor; say what couldn't be verified |
| 7. Render | Code | Templates fill every number, time and address from the evidence; a sources line and the time checked |
| 8. Record | Code | The run, each outcome's result, and the cost |

- **Evals that replay for $0.** Every model and tool call under test is recorded against its whole request ([`core/cassette.py`](../core/cassette.py), [`core/httpcache.py`](../core/httpcache.py)), so a golden-set run replays without a network or a bill; production never reads a recording. [`core/eval_outcomes.py`](../core/eval_outcomes.py) scores a case by its outcomes, not by substrings. The golden sets (72 real Bourdain asks, 14 Genie asks) stay private; public tests use synthetic twins.
- **Status.** One tenant, Bourdain, served since October 4. Fraser borrows the contract pattern and records its outcomes in the same table, but isn't a tenant. A second tenant is parked (October 11). The judge has no authority yet. A memo cache for repeated asks is not built. Five dated amendments (A1–A5) are in [ADR-018 §7](./ADR-018-outcome-contracts-and-independent-evaluation.md).

---

## 8. Data plane: the vault

### 8.1 One file, and what's beside it

`vault/rahat.db` is the system of record: one SQLite file on the Mac mini. JSON files beside it hold what is edited as documents: the household calendar and committed plans, the family and athlete profiles, Bourdain's household profile. The whole of `vault/` is outside git.

With `RAHAT_TEST_MODE=1`, every connection is redirected to a per-process sandbox, even one that names the live file explicitly ([`core/io.py`](../core/io.py)); tests cannot touch the vault.

### 8.2 Tables, by what they hold

| Domain | Tables | Written by |
|---|---|---|
| **Observations** | `raw_vitals`, `sleep_sessions`, `workouts_hk`, `hrv_log`, `weighin_log`, `workout_log` | The health bridge; Kobe's logs |
| **Plans and intents** | `intents`, `weekly_plan`, `week_preferences`, `weekly_campaigns` | Kobe |
| **The world** | `events_inventory`, `events_refresh_log`, `events_popularity` | The event runs |
| **The knowledge base** | `kb_place`, `kb_location`, `kb_fact`, `kb_hours`, `kb_menu_item`, `kb_mention`, `kb_acclaim`, `kb_gazetteer`, `kb_save`, `kb_verdict`, `kb_visit`, `kb_list`, `kb_leg`, `kb_meta` and helpers | The harness, the food run, the gazetteer loader, the household's saves and verdicts |
| **Memory** | `memory_entities`, `memory_events`, `memory_threads`, `memory_preferences`, `memory_relationships`, `memory_archival` | Agents through their adapters |
| **State** | `state_scoped`, `user_state`, `nudge_log` | The harness, the runners |
| **Records** | `decisions`, `cost_ledger`, `governance_log`, `harness_runs`, `harness_outcomes`, `harness_reactions`, `judge_verdicts`, `judge_labels` | Every agent, the wire, the Charter, the harness, the judge |

Bourdain's first tables (`bourdain_places`, `bourdain_saves`, `bourdain_mentions`, `bourdain_verdicts`) belong to the pipeline that answered before the harness; the harness reads the knowledge base.

### 8.3 The knowledge base

[`core/kb/`](../core/kb/) is the platform's store of places and facts with provenance ([ADR-018 §2.6](./ADR-018-outcome-contracts-and-independent-evaluation.md)). Every fact carries its source and the time it was read. It fills from four directions: the harness's research, the nightly food run (mentions, acclaim, closures, and hours from places' own pages), the household's saves and verdicts, and a gazetteer of every eating place in open data (21,555 Bay Area places from OpenStreetMap, loaded October 17). Agents ask it only through tested queries ([`core/kb/query.py`](../core/kb/query.py)), never with ad hoc SQL. The gazetteer holds open data only; nothing from Google Maps goes into it.

### 8.4 Memory

Five primitives in the same file (entities, events, threads, preferences, relationships) plus an archive of text with an embedding, searched by cosine similarity in Python ([`core/memory/`](../core/memory/)). Each agent that uses memory writes a small adapter with its own entity types. In daily use: entities and events (1,621 and 1,694 through October 12). Barely used: preferences and relationships. The consolidation job (`scripts/memory_consolidate.py`) exists but has not been scheduled since May.

Chat memory keeps the last four hours of a chat for a design conversation with Fraser; Miya records its own turns there only behind a flag that is off by default ([`core/chat_memory.py`](../core/chat_memory.py)).

### 8.5 Scoped state

Every fact an agent remembers about a conversation has a scope with an expiry: turn, session, day, trip or standing ([`core/state_scopes.py`](../core/state_scopes.py)). The harness keeps the last ask there for follow-ups ("more", "which one"), and where you are for three hours.

### 8.6 Why SQLite

- **One file, transactions, no operations.** The workload is a handful of readers and one writer at a time; SQLite fits it exactly.
- **Flexible where it needs to be.** JSON columns absorb a new agent's shapes without a migration.
- **Backup is a copy.** The whole state is one meaningful, queryable file.
- **Rejected:** Postgres (a server and migrations for nothing the workload needs), DuckDB (built for analytics, not this), a separate vector store (two stores, two backups, two consistency stories), plain JSON (no transactions, no queries).

Revisit when state must live on more than one machine (§15).

---

## 9. Ingestion and scheduled work

| What | When | Reads | Writes | Model |
|---|---|---|---|---|
| Health bridge | On each phone export | Health Auto Export on the iPhone: 150+ metrics at minute resolution, sleep stages, typed workouts | `raw_vitals`, `sleep_sessions`, `workouts_hk` | None |
| Gym bridge | When the week is sent | The gym's programming, from its app by a bookmarklet | The week's plan file | None |
| Event runs | 07:00, 12:30, 18:00 | 50 sources read in plain code; 52 pages a model reads once a day | `events_inventory`, deduped on title, start and city and tagged by audience | For the 52 pages |
| Event search | Wed and Sat 03:00, and Genie's `/refresh` | 20 grounded searches for what no feed covers, and for sites that refuse the reader | `events_inventory` | Yes |
| Food run | 05:30 | 12 food feeds; places' own pages for missing or old hours | Mentions, acclaim, closures and hours in the knowledge base | None |
| Miya's minute tick | 08:00 brief · 08:15 health line · walk nudges 10:00–20:00 on a day that's behind · 21:00 recovery nudge · 21:30–21:59 Huberman's cooldown · Sunday 23:55 weekly reset | The vault | Telegram, `nudge_log` | Huberman's coaching |
| Genie's digest | 08:00 | The events inventory | Telegram | None |

A nudge another agent has vetoed is held back (§10.3). The jobs built but not scheduled are listed in §4.2.

---

## 10. Control plane

### 10.1 The model wire and spend

- **Models.** Google Gemini: Flash for most work; Miya's cost router sends hard prompts to Pro ([`cost_router.py`](../new_plane/miya_runner/cost_router.py)); grounding through Google Search and Google Maps. The harness's model is a deployment setting.
- **One wire.** [`core/llm.py`](../core/llm.py) is the chokepoint for generation and [`core/llm_chat.py`](../core/llm_chat.py) the harness's wire: a 90-second timeout on the client, a row in the cost ledger and the decisions ledger for every billable call, and on the harness wire two retries on a provider hiccup.
- **Spend controls.** A daily budget gate per actor, set in the environment (a cap of 0 turns it off); the harness's per-ask brake on tool calls, searches and dollars; and the provider account's own spend cap and alerts, which are the owner's stop. Bourdain's `/cost` shows today, this week and this month per agent, and the month's searches against the free allowance.
- **Outside the wire.** Three older paths call the model directly: Miya's synthesizer and Kobe's reasoner (logged, not gated), and Fraser's design (through a helper that returns text only, so its cost isn't recorded). Bringing them onto the wire is on the list (§16).

### 10.2 Checks in the path

| Check | Where | What it does |
|---|---|---|
| The validator | Every Miya reply ([`validator.py`](../new_plane/miya_runner/validator.py)) | Seven checks against what is true (claims about lifts and loads, pace, goal and rest targets, movement constraints, and a change claimed when nothing was written); rewrites before send |
| Fraser's contract | Every design ([`contract.py`](../agents/fraser/contract.py)) | Ten checks: sections, warm-up, cool-down, loads, how-to cues, duration, exclusions, the gym's coming sessions, distinct work, calories; one repair pass; what still fails is said |
| Verify | Every Bourdain answer ([`core/verify.py`](../core/verify.py)) | Each pick against its evidence (§7) |
| The judge | Every Bourdain answer ([`core/judge.py`](../core/judge.py)) | Grades judgment in shadow |
| Never empty | Every Miya reply | No turn is silently dropped |

### 10.3 The Charter

[`core/charter.py`](../core/charter.py) is a registry of Python predicates; a work order is approved, modified or vetoed, and every verdict lands in `governance_log`. The policies today: quiet hours for ambient nudges (a direct question is always answered), no intensity pushes on a red recovery day, Fraser's workout commits and loading increases gated on recovery, and any veto another agent has recorded. Predicates rather than a policy language: they are easy to write, test and debug in the same process; the cost is that policies aren't editable without code, which is fine while the owner is the developer.

### 10.4 The decisions ledger

[`core/decisions.py`](../core/decisions.py): append-only, one row per step under the message's trace id, with span and parent ids, the actor, the operation, input and output, latency, tokens, cost, outcome and error. 7,408 rows by October 14. Kept apart from `governance_log` because they are read differently: one per trace, the other per policy question.

### 10.5 The health line

[`core/health.py`](../core/health.py): at 08:15 the minute tick checks for a feed fetching nothing, a page gone stale, food feeds silent for three nights, a quiet Watch, lost messages, an old gym plan and refused model calls. If something is broken, one line goes to the owner; otherwise nothing does.

Since October 19 every refresh row records why a feed yielded nothing ("HTTP 403", "no such host", "redirect loop", "nothing upcoming; newest 2025-06-14"), and the line groups dead feeds by it: sites that refuse the reader, addresses gone, feeds that fail, feeds that answer with nothing. A calendar the registry marks `quiet` is named only when its site fails. `python -m bridges.events --check [id …]` reads sources once with the production client, writing nothing and calling no model.

### 10.6 Registry and manifests

Miya's agents are registered in code ([`agent_boot.py`](../new_plane/miya_runner/agent_boot.py)). A harness tenant declares itself in a manifest ([`core/manifest.py`](../core/manifest.py); Bourdain's is [`manifest.json`](../agents/bourdain/manifest.json)): its grammar, prompts, frame schema, outcome library, judge checklist, allow-listed tools, state scopes, budgets, render templates, banned phrases and golden sets.

---

## 11. Reliability and operations

| Failure | What happens today | Not built |
|---|---|---|
| The Mac mini dies | Everything stops; the state is one file to copy | A replica or a scheduled off-machine backup |
| A runner crashes | launchd restarts it (`KeepAlive`) | |
| A model call hangs or fails | 90-second timeout; retry on a hiccup; agents fall to floors (Huberman's library session, Genie's offline plan, Bourdain's "not verified" lines); Kobe falls back to its legacy rules | |
| The model provider refuses calls | The health line reports refused calls the next morning | |
| Telegram refuses a reply | Sent again as plain text | |
| A scheduled message doesn't land | Retried each minute for 30 minutes, then dropped and logged; the health line reports lost messages | |
| A feed goes dark or stale | The digest says when feeds last refreshed; the health line names the feed and why (refused, gone, nothing listed) | Automatic repair of a moved feed |
| A regression returns | The pre-push gate and CI block it (§12) | |

**Operations.** The owner alone pushes and flips live flags; services are restarted with `launchctl kickstart`. Logs live beside the vault. Two build lanes share `main` under a two-architect protocol: lanes by directory, and a commit that touches a shared file says so in a `COORDINATION:` line.

---

## 12. Testing and the rails

- **4,209 tests**, all hermetic: no network, no live database, no secrets loaded (`RAHAT_TEST_MODE=1`). The regression registry holds 1,877 of them in 219 pins; the layered suites (unit, contract, eval, adversarial, quality gate, silent failure, production parity across time zones) hold 1,370; the module tests hold 962.
- **A fix ships a pin.** A `fix:` commit that doesn't add a new file under `tests/regression_registry/` is refused at merge.
- **The pre-push gate** ([`scripts/hooks/pre-push`](../scripts/hooks/pre-push)): private words, the bug-to-test policy, the regression registry, the silent-failure guard, the unit and contract layers. Anything red blocks the push.
- **CI** ([`.github/workflows/`](../.github/workflows/)): the full layered suite on every push to `main` and every pull request; the registry, the silent-failure guard, the bug-to-test policy and the time-zone matrix on pushes to `main`, `feat/*` and `fix/*`; and the gates again every hour on `main`, to catch drift from outside.
- **Golden sets and replay:** §7.

---

## 13. Security and privacy

- **Secrets** live only in `.env` on the Mac mini; `.env.example` names every key without a value.
- **Private words.** A name, an address, a token or a chat id in anything a push would publish blocks the push. The words live outside the repo.
- **What leaves the machine.** Each model or Maps call sends that request's context to Google, and a location sent to Maps is rounded to about a kilometre. Messages travel through Telegram. Geocoding goes to OpenStreetMap's Nominatim and forecasts to the US National Weather Service, both as documented. Feeds and pages are fetched from their sites. Everything else stays on the Mac mini.
- **Pages are data, never instructions.** What the harness reads from a page comes back as facts for the model to use, not as text it obeys ([`core/fetch.py`](../core/fetch.py)).
- **Doors are gated.** Genie and Bourdain answer only paired phones; the health bridge needs its key.
- **The public repo** carries code and synthetic tests only. Profiles, household rules, golden sets and recordings stay private.

---

## 14. Architecture decisions

| ADR | Decision | Status |
|---|---|---|
| [002](./ADR-002-rebrand-risk.md) | Agent renames and their risk | Accepted |
| [003](./ADR-003-multi-agent-storage-convention.md) | A storage convention for many agents | Accepted |
| [004](./ADR-004-five-file-agent-pattern.md) | The five-file agent pattern | Accepted |
| [005](./ADR-005-budget-enforcement.md) | Token-budget enforcement | Accepted; amended by ADR-018 |
| [007](./ADR-007-cross-agent-delegation.md) | Cross-agent delegation | Accepted |
| [008](./ADR-008-clarification-policy.md) | When to ask a clarifying question | Accepted |
| [009](./ADR-009-single-dispatcher.md) | A single ordered dispatcher | The file says Proposed; the dispatcher runs in Kobe's route, on by default; amended by ADR-018 |
| [011](./ADR-011-deterministic-shell-llm-core.md) | Deterministic shell, LLM core | Accepted |
| [012](./ADR-012-shared-tool-calling-runtime.md) | A shared tool-calling runtime | Accepted as amended by ADR-018 §3 |
| [013](./ADR-013_migrate_to_new_plane.md) | Move the runtime to the new plane | Accepted |
| [014](./ADR-014_openclaw_position.md) | OpenClaw: a parked adapter, not the runtime | Accepted |
| [015](./ADR-015_outcome_validated_learning_engine.md) | An outcome-validated learning engine | Proposed, design only |
| [016](./ADR-016_platform_seams.md) | Platform seams before the second agent, surface or tenant | Accepted; amended by ADR-018 |
| [017](./ADR-017_nl_intent_layer.md) | A plain-language intent layer | Accepted, behind a flag that is off; amended by ADR-018 |
| [018](./ADR-018-outcome-contracts-and-independent-evaluation.md) | Outcome contracts and independent evaluation: the harness | Accepted; in production since October 4 |

There is no ADR-010 in this repository. [`specs/README.md`](./README.md) groups these by theme. Two ADRs moved to [`archive/`](../archive/README.md) on October 18: ADR-001, the first Now/Next/Later plan, never accepted; and ADR-006, an LLM router over agent descriptions that the Miya bot doesn't use (it routes by the ladder, §5.1).

---

## 15. Now, Next, Later

The rule from v2.0 still decides promotion: an item moves to Now when an agent in use needs it, when not building it would make several agents reinvent it, and when its contract is clear.

**Now (shipped):** everything in §4 to §13.

**Next, with the trigger that promotes each:**

| Item | Trigger |
|---|---|
| Schedule the nightly replay, the weekly report, the command screener and the shadow diff | After the golden set is recorded again |
| Bring Miya's synthesizer, Kobe's reasoner and Fraser's design onto the wire | The owner's call: pricing Fraser's calls first, gating only if he asks |
| Bourdain's database-first path: the knowledge base answers, code applies the facts, the model applies taste once, the web only when the base comes up short | The owner's design (October 15); built once the run counters (recorded since October 15) show how often the knowledge base could have answered |
| Schedule memory consolidation, or retire it | Before memory is described as consolidating again |
| A second harness tenant | When the owner unparks it |
| Give the judge authority | When enough owner labels exist to measure its agreement |

**Later:** a learning loop that changes behaviour from outcomes ([ADR-015](./ADR-015_outcome_validated_learning_engine.md)); memory recall weighted by what changed an outcome; a source and date on every fact, not just Bourdain's; an off-machine replica; a gateway for a mobile client; manifests for agents beyond the harness.

---

## 16. Open questions and known gaps

1. **The budget gate's role.** The gate exists per actor; whether it runs with a cap, or the provider account's cap is the only stop, is a deployment choice that should be written down with its reason.
2. **Three model paths outside the wire** (§10.1). Fraser's design cost isn't recorded today.
3. **Memory consolidation** has had no schedule since May; preferences and relationships barely run.
4. **ADR-009's status line** still says Proposed while its dispatcher runs in production.
5. **A single machine.** No replica and no scheduled off-machine backup.
6. **Feeds that move.** A feed whose address changes is reported by the health line, with why, but not repaired; `python -m bridges.events --check` reads a fixed or new feed with the production reader.
7. **The front door's judged criteria** are written and not yet running.

---

## Appendix A. Where the code lives

| Path | What's there |
|---|---|
| [`new_plane/miya_runner/`](../new_plane/miya_runner/) | The Miya runner: the ladder, the orchestrator and its sink, the synthesizer, the validator, the cost router, the Telegram client, menus, agent registry |
| [`new_plane/genie_runner/`](../new_plane/genie_runner/), [`new_plane/bourdain_runner/`](../new_plane/bourdain_runner/) | The household bots' runners |
| [`agents/`](../agents/) | One folder per agent (Kobe's code is in `agents/the_scientist/`) |
| [`core/harness/`](../core/harness/) | The harness's steps: frame, contract, execute, answer, evidence, control, render, record, run |
| [`core/kb/`](../core/kb/) | The knowledge base: schema, queries, the gazetteer, cities, geography |
| [`core/`](../core/) | The platform: the wire (`llm.py`, `llm_chat.py`), the Charter, decisions, the cost ledger and budget, the health line, scoped state, memory, the judge, verify, manifests, tools, cassettes |
| [`bridges/`](../bridges/) | Health, gym, events and food ingestion |
| [`scripts/`](../scripts/) | Launchd templates, the gates, evals, reports and loaders |
| [`tests/`](../tests/) | The regression registry, the layered suites and the module tests |
| [`specs/`](./) | This document, the ADRs ([index](./README.md)) and the two interactive pages |
| [`archive/`](../archive/) | Documents, scripts and tests that no longer describe or run the system (October 18) |

## Appendix B. Counts, dated

| Count | Value | As of |
|---|---|---|
| Front doors | 3 | Oct 17 |
| Agents | 7 | Oct 17 |
| launchd services | 8 (5 always on) | Oct 17 |
| Event sources | 122 (50 in code, 52 pages read by a model, 20 searches) | Oct 19 |
| Food feeds | 12 | Oct 17 |
| Gazetteer places | 21,555 | Oct 17 |
| Tests | 4,209 (1,877 in 219 regression pins) | Oct 17 |
| Bourdain outcome library | 52 outcomes, 3 always required | Oct 18 |
| Judge checklist | 21 criteria | Oct 17 |
| Golden sets (private) | 72 Bourdain asks, 14 Genie asks | Oct 17 |
| Harness runs | 22 (19 Bourdain answers, 3 Fraser designs) | Oct 4–11 |
| Decisions ledger | 7,408 rows | Oct 14 |
| Memory | 1,621 entities, 1,694 events | Oct 12 |
