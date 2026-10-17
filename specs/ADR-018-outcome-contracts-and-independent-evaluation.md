# ADR-018 — Outcome contracts and independent evaluation

**Status:** Accepted (owner) · in production since 2026-10-04: the first tenant, Bourdain, is served by the harness · amended 2026-10-04, 2026-10-14/15 and 2026-10-17 (§7).
**History:** proposed 2026-10-03 with the owner's decisions of that day (§6) · Phases 1a, 1b and 2 built 2026-10-03/04 · Phase 3 (serve) for Bourdain from 2026-10-04 · Phase 4 (a second tenant) parked by the owner on 2026-10-11 · where each part stands: §7.1.
**Interactive version:** [Rahat Harness Architecture](https://claude.ai/artifact/THBk4pzgJvtenkf4RZmdjK): watch a message move through the harness, hover any box for what happens there, and see where each part stands. Its source is [`ADR-018.html`](./ADR-018.html) beside this file; it opens in any browser.
**Author:** Architect A (platform lane).
**Builds on:** ADR-011 (deterministic shell, LLM core), ADR-003 (storage convention), ADR-015 (outcome-validated learning).
**Amends:** ADR-005 (budget enforcement), ADR-009 (single dispatcher), ADR-012 (shared tool-calling runtime), ADR-016 (platform seams), ADR-017 (NL intent layer).
**Requirements this implements:** the private platform requirements of 2026-10-03 (PR-A, PR-M, PR-H, PR-E, PR-D, PR-S, PR-K, PR-R, PR-G, PR-J, PR-JG, PR-O, PR-B, PR-X, PR-V) and the first tenant's build requirements. Those documents and their golden sets live in `private/` and are cited, not repeated.

---

## 1. Context

Two conversational agents were run live against a household, one in June and one for two weeks in September, each with a human answering alongside the agent. Both probes produced the same five findings:

1. **Understanding is probabilistic, and the platform treated it as a lookup.** A deterministic resolver (regexes and keyword lists over free text) misread who, where, when or what on 18 of 18 replayed asks in one city, and 20 of the 51 logged fixes were understanding failures. ADR-011 already said this code is in the wrong layer; ADR-009's "every factual query gets a regex entry" pushed agents the other way.
2. **Facts without provenance are the biggest liability.** Seed ratings carried no source or count; the live path could state a fact the model had not read; a pick with no counted rating was rejected by the household within minutes. `emits_structured_facts` made a fabricated *number* structurally impossible; nothing made an *unsourced* number impossible.
3. **State has no scopes.** A one-day override, a today-only exclusion with an exception clause, a verdict on a class of rooms, a correction window, a trip leg that ends with no home leg: each lived in a human's head because no store had a place for it.
4. **Checking is the hard part, not understanding.** The human (a frontier model) understood every ask and was still corrected seven times on facts and fit rules a mechanical check would have caught before sending.
5. **Sources are a governance problem.** The household's trusted rating site is robots-blocked; so were two other sources and a weather endpoint fetched as a page. Every workaround is a policy violation; there was no registry, no freshness window, no "trusted source we can't read" concept.

The code the harness must stand on has gaps of its own, verified 2026-10-03: two model wires (`core.llm.generate` has cassettes, a budget check and a spend record but no system prompt, tools or schema; `core.gemini_reasoner_io.chat` has system prompt and native tools but no cassettes, no spend record and no trace id); no cassettes for tool calls; `decisions.cost_usd` never populated; a per-actor daily cap in the wire and no per-ask brake; a seed loader that drops 29 of 48 fields; an upsert that lets a live hit overwrite a seed row; a session store with no lock; Charter context keys that don't match what the policies read.

The market has converged (vendor pages read 2026-10-03): every major harness ships a tool loop with iteration and spend caps, a permission pipeline where code decides before a model does, schema-validated outputs with bounded retries, fresh-context sub-agents for judgment, compaction with standing rules kept outside the transcript, and evals graded on outcomes with model graders calibrated against humans. Gemini 3 supports native function calling, grounded search and structured output in one request. No framework enforces "no claim without a source"; that contract is ours to build.

---

## 2. Decision

### 2.1 Routing: one strict check, then the model

For every agent on the harness, the inbound path is two rungs.

| Input | Path | Who decides |
|---|---|---|
| A dedicated command, exactly (`/eat`, `/list`, `/week`, `/day`, a declared one-word reply such as "more") | Deterministic handler. Code only. Never judged. | The agent's manifest grammar |
| Anything else a person types | The harness (§2.2) | Default |
| A device or scheduled payload (a health export, a workout feed, a cron tick) | A pipeline: its own door, never the ladder, never the model, never judged | Manifest `mode: pipeline` |

Rules: the grammar matches **exactly or not at all**; it is data in the manifest, reviewed like code, and grows only by hand. A **weekly command screener** (control plane) reads the week's traces, clusters recurring asks by the *frame* the model produced, and proposes new commands with counts and evidence; the owner approves; the architect adds the command with a golden case. A test asserts that no handler reachable without the model inspects free text beyond the declared list. The memo cache (same frame, still fresh → the cached verified answer, labeled) lives **inside** the harness after Understand, so a hit is keyed on what was meant rather than on the words. ADR-017's paraphrase layer is not part of the tenant ladder (§3).

> **Amended 2026-10-17 (§7, A4).** A command is a *lookup*, answered by code from the harness's own stores, or an *ask*, handed to the harness as the words it stands for (`/coffee` → "coffee"). There is no second pipeline with a memory of its own. The memo cache above is not built yet (§7.1).

### 2.2 The harness: eight steps, platform-owned

`core/` and `new_plane/` own every step; a tenant supplies a manifest, a system prompt, a frame schema, an outcome library, a judge checklist, tools, templates and golden sets.

| Step | Who | What |
|---|---|---|
| 1 Understand | model | Reads the message plus the session into a **typed frame** validated against the agent's schema, with bounded retries and a distinct failure result. Carries per-field confidence and the inferences made. A cheap sanity check confirms the frame says what the message says before any research. |
| 2 Contract | code | Assembles the **outcome checklist** for this answer from the frame and the agent's library rules ("always", "when present", "when a guest is along"), as a list of outcome IDs with parameters; versioned with the library. Mandatory sets can't be deselected. Household rules learned from verdicts are added automatically. |
| 3 Execute | model + tools | Runs on the shared tool-calling runtime (§2.3) with the manifest's allow-list and the per-ask brake. Every tool returns **evidence records**; the structured answer references record IDs, never values. A holding line goes out within 5 s when research will take longer. |
| 4 Verify | code | Pure functions over frame, contract, answer, evidence, state and clock; runs every mechanical outcome and returns **every failure with a remediation hint**. No network. Hard failures block (a failing pick is repaired or dropped, never shown with a warning). Also the pre-tool (deny or rewrite) and post-tool (provenance, instruction stripping, reject unsourced) hooks. |
| 5 Judge | fresh model | Grades only judgment outcomes (§2.8). |
| 6 Control | code | All musts pass → render. Else at most two repair rounds inside the time budget, feeding hints and judge lines back to Execute; then send with the gap stated, or drop the failing pick. Never an empty reply; never the help text for an in-domain ask. A new message mid-work supersedes the in-flight ask. |
| 7 Render | code | Deterministic templates per agent; evidence-backed fields only; banned phrases rejected by the renderer; one-screen budget; a sources line; the frame shown as the reply header so a wrong reading costs one word to fix. The model never writes the final text of a fact. |
| 8 Record | code | One `trace_id` per inbound message with a span per step, the contract, per-outcome results, repairs, cost and latency; outcome signals to `new_plane/signals`. |

Standing rules (profiles, household rules, the contract, the banned list) are re-injected per step from stores, never carried only in the transcript. Steps are idempotent and checkpointed so a confirm card can resume without repeating a side effect.

> **Amended (§7).** Between Understand and Contract the tenant may apply deterministic reading rules to the model's frame (A1, A4); a message with nothing to find runs Execute without the network tools (A3).

### 2.3 One model wire

`core.llm` becomes the only way any Rahat code calls a model. It gains `chat()` (system prompt, messages, native function declarations, grounded search) and `structured()` (schema-validated output), alongside `generate()`. Every call: checks the per-ask brake and the manifest's optional budget; records a ledger row (§2.9) with the four token buckets and the **search query count from the response metadata**; carries the `trace_id`; and is replayable from a cassette keyed on the whole request (model, system prompt, messages, tool schemas, config). `core.gemini_reasoner_io.chat` becomes a thin call into this wire; the Scientist's behavior is pinned before and after (COORDINATION).

This amends ADR-012's non-goal. ADR-012 chose "LLM-as-planner-over-text" for hermeticity; hermeticity now comes from request-keyed cassettes, and a tolerant text parser is the class of fragility this ADR removes.

> **As built (§7.1).** The wire is `core/llm_chat.py` (`chat`, `structured`) beside `core.llm.generate`; three model paths outside the harness still reach the model by older routes.

### 2.4 The manifest

`agents/<name>/manifest.yaml`, validated at boot, read by `core/agent.py`:

```
mode: conversational | pipeline
grammar: [commands and declared exact replies]        # exact match only
frame_schema: path                                     # JSON Schema, versioned
outcome_library: path                                  # §2.5
judge_checklist: path                                  # §2.8
tools: [allow-list]                                    # names in the registry
state_scopes: [turn, session, day, trip, standing]
budgets: {per_ask: {tool_calls: 12, usd: 0.50, search_queries: 10}, daily_usd: null}
render_templates: path
banned_phrases: [...]
golden_sets: [paths]                                   # private/
cassette_dir: path
quiet_hours: ...
judge_model: {tier, prompt_version}
```

*As built:* `agents/<name>/manifest.json` (JSON rather than YAML), validated by `core/manifest.py`. `name`, `description`, `aliases` remain for the front-door classifier. Adding an agent is a manifest, a system prompt, a library, a checklist, tools and golden sets; not a `route()`.

### 2.5 The outcome library and the contract

An outcome is one checkable statement with a stable ID, a pass condition, a check type (**M** mechanical, **J** judge, **M+J** both), an applies rule and a priority. The library is versioned data per agent; adding an outcome requires a golden case. The contract is `{trace_id, library_version, frame, outcomes:[{id, params, priority}]}`. The frame's fields are derived from the golden cases' contract parameters and versioned with the library.

### 2.6 Evidence and the knowledge base

**The evidence record is the unit of fact** and is one table, `kb_fact`: `{fact_id, subject_type, subject_id, claim_type, value_json, value_text, source_url, source_name, source_rank, method, observed_at, applies_to, freshness_hours, trace_id, superseded_by}`. `method` ∈ {fetch, search_citation, seed, user_report, api, owner}. Facts are append-only; the newest dated source wins at read time; history stays for the trace. A fact field in a reply references a record or carries `status: unverified`, and the renderer prints "unverified", never a bare value. Citation spans from grounded search count as evidence only with a URL and the call time. User reports ("3.9 on their site", "that's a chain") are evidence with `method: user_report` and outrank the web for the household's own bar. Negative claims carry coverage.

**The knowledge base is the first platform-owned store shared by agents** (ADR-003: SQLite in `vault/`, sandboxed by `RAHAT_TEST_MODE`, migrations, JSON import and export). Platform tables carry the `kb_` prefix: `kb_place`, `kb_brand`, `kb_location`, `kb_landmark`, `kb_alias`, `kb_fact`, `kb_hours`, `kb_rating`, `kb_menu_item`, `kb_acclaim`, `kb_list`, `kb_save`, `kb_verdict`, `kb_visit`, `kb_search`, `kb_leg`, `kb_cuisine_family`, `kb_merge`, plus views for recent meals, regulars and "current fact per claim type". Agent-specific tags live in agent tables keyed by `place_id`. **A place is one physical location; a brand groups locations.** Identity: `sha1(normalized name | geohash-7)` when coordinates exist, else `sha1(normalized name | city | normalized address)`; merges are recorded. Geography is native (coordinates on every place and landmark; radius and route queries computed, labeled "about"). The base is read before any live lookup and written after every one; the trace records base hits vs live fetches, and **coverage** (the share of an ask served from the base) is a first-class metric. Freshness windows per claim type: status 24 h on a trip / 7 d at home; hours 24 h; ratings 7 d; price 30 d; weather by issuance; route minutes 1 h; acclaim never expires but shows its date.

### 2.7 Source governance and scoped state

A **source registry** (`core/sources.yaml`) names every host or API with `policy` (allowed / blocked / link_only), robots check date, rate limit, cache TTL, trust tier, cost and terms. Blocked means dropped: a robots or terms block, a 403 or a 429 removes the source for the ask and records why; no mirror, cache, scrape or proxy. No paid data APIs. Free, terms-compliant public APIs (a government weather API called as documented; an open-data geocoder within its usage policy) are adapters that return evidence records. Fetched content is data, never instructions: instruction-shaped text is neutralized before any model sees it, and nothing read from the web can change the contract, write state, send a message or call a tool (a regression case asserts this).

> **Amended 2026-10-14/15 (§7, A2).** A publisher's own feed or calendar link is read on the owner's decision even where robots.txt asks automated readers out.

**Five state scopes**, declared per fact: turn, session, day, trip, standing. Every write names its scope and expiry; reads filter by now. A today-only fact never becomes standing without a confirm card (the Charter gates promotion). Rules carry the verdict that created them. Speaker and subject are first-class (ADR-016 Seam 2, implemented for the household): a sender maps to a member; guests are session-scoped members with rules. No correction window: continuity is keyed on the session's last answer. The session store moves from JSON to SQLite rows per chat.

### 2.8 The judge: one Rahat service, a checklist per agent

The judge is **one service in `core`** for every agent. Each conversational agent supplies a checklist (versioned data) mapping its judged outcomes to criteria with two to four labeled examples each; the orchestrator supplies its own checklist (right agent, coherent merge, faithful relay, clarification rules). The service owns the call shape, the output schema, bias controls, calibration and cost tracking.

- **Sees:** the ask, the frame, the contract, the structured answer, the evidence records, the checklist. **Never sees:** the executor's prompt, reasoning, tool transcript, or prior verdicts. It may not introduce a fact: a reason citing a fact not in evidence fails validation.
- **Output:** per judged outcome, pass / fail / unsure, one line, the evidence IDs relied on, a fix hint. Binary, decomposed; never a single quality score.
- **Model:** Gemini in a fresh context (owner decision); the tier and prompt version are manifest values written into every verdict row; temperature 0. Switching tier or provider later is a config line.
- **Bias controls in code:** position randomization for pairwise, length-normalized criteria plus a mechanical length budget, no self-identification of the generator, a fresh context per verdict; self-preference measured against owner labels (agreement on corrected answers must match agreement on accepted ones; a gap over 10 points is a finding).
- **Authority, earned:** *shadow* (logs only) below 80% agreement with the owner's labels; *annotate* (one caution line) at ≥ 80%, measured **per agent**; *block or repair* at ≥ 90%, measured **per outcome**. No agent leaves shadow before 50 labels. A judge drops a level after two weeks below threshold. Labels come from owner reactions (corrections, verdicts, acceptances) plus a monthly review of about 30 sampled verdicts; agreement is reported as accuracy and Cohen's kappa.
- **Scope:** runs only on harness replies and only on judgment outcomes; never on a command or cache hit; never relitigates a fact or a hard rule (a verdict contradicting the verifier is a judge error). Pipeline agents are judged offline on a sample as a quality trend, never in the delivery path.
- **Cost:** its own ledger line, kept under about 15% of the agent's model spend (note at 25%); verdicts cached for identical (answer, evidence) pairs.

### 2.9 Cost: a ledger, a brake, no daily cap

A **ledger row for every billable event**: model call (agent, step, model, tokens in/out/thought/tool-prompt, cost), each grounded **search query** (Gemini 3 bills per query the model runs, not per call), page fetch, judge call, cache hit (cost zero, counted); with `trace_id`, mode and timestamp. Rollups by day, week and month, per agent and overall, by event type and step; `/cost` and `/cost export`; the month's query count against the free allowance on the weekly report.

Position, decided 2026-10-03: the per-actor daily cap (ADR-005) is **off** for conversational agents and opt-in per manifest; a **per-ask brake** is on (12 tool calls, 10 search queries, $0.50; then answer with what is verified and say what was skipped); the free search allowance is **counted, never limited on**; the provider's own project spending limit is kept as a **disaster brake** at roughly a month's worst case, never as spend control. Degradation (offline, capped by the provider, out of allowance, stopped by the brake) is disclosed in one line and distinguishes which lever tripped; it never serves stale data as fresh and never returns the help text.

> **Amended 2026-10-04 (§7, A1).** A wide ask has its own brake: 18 tool calls, 14 search queries, $0.80.

**The floor.** When the model is down, commands and cache hits still answer. One fenced exception to "no keyword lists": a last-resort frame (meal and cuisine words only), reachable only on the model-down path, answering from the knowledge base with an honest line, and a test asserting it is unreachable when the model is up.

### 2.10 Traces, evals and the outcome loop

One trace per message with spans for routing, frame, contract, each tool call with evidence IDs, verifier results, judge results, repairs, render, cost and latency (`core/decisions.py` gains the span types and `by_trace`). A CLI replay viewer shows a trace as the household saw it beside what the verifier and judge saw, and diffs two runs of a golden case. Redaction by default in anything that leaves the machine.

`core/eval.py` is upgraded from substring matching to **outcome scoring** over golden sets (one JSON object per case: message, context snapshot, journeys, contract, traps, reference, owner verdict, fixes). Replay is deterministic from cassettes for model **and** tool calls; a case passes when all its must outcomes pass; hard-rule cases run pass^k (k = 3, sampling on); efficiency metrics are reported and never fail a case; the nightly job replays every agent's set and a failing must blocks the auto-commit. Public tests use synthetic twins; golden sets stay in `private/`.

The owner's next message is classified (acceptance, correction, verdict, unrelated) and linked to the previous trace: the owner correction rate. Per-outcome results plus the reaction are ADR-015's validated reward; first wiring points are model-tier routing per step and ranking-weight choice. The bandit never changes a hard gate.

### 2.11 Code gates before model gates, in a fixed order

manifest allow-list → source registry → brake and budget → Charter → tool call → post-tool provenance and stripping → verifier → judge → render checks. The order is a test, and the test includes that each Charter policy can actually fire from the orchestrator (today's context keys don't match the policies').

---

## 3. Amendments to earlier ADRs

| ADR | Change |
|---|---|
| ADR-005 budget enforcement | Daily caps become opt-in per manifest; off for conversational agents. The ledger, rollups and the per-ask brake are the control. |
| ADR-009 single dispatcher | Scope narrowed to explicit grammars for agents on the harness: commands and declared exact replies. "Every factual query gets a regex entry" is withdrawn for conversational tenants. The Scientist's dispatcher routes are untouched until that agent becomes a tenant. |
| ADR-011 deterministic shell, LLM core | Unchanged; this ADR is its platform-level implementation. |
| ADR-012 shared tool-calling runtime | Accepted as the Execute step, with one amendment: native function calling and schema-validated output are the wire; "LLM-as-planner-over-text" is retired. The registry, typed tool schemas and the runtime-level Charter gate on writes stand. |
| ADR-015 outcome-validated learning | Gains its first reward signal and its first two wiring points (§2.10). |
| ADR-016 platform seams | Seam 2 (subject scoping) is implemented for the household; Seam 3 (agent dimension) becomes the manifest. |
| ADR-017 NL intent layer | Not part of the tenant ladder (§2.1). It stays flag-gated for the Scientist as today, with its calibrated thresholds (its score is not a probability). |
| ADR-003 storage convention | The knowledge base is the first platform-owned store shared by agents; SQLite in `vault/`. |

---

## 4. Phasing and gates

| Phase | What | Gate |
|---|---|---|
| **1a Foundations** | One model wire with request-keyed cassettes for model and tool calls; the ledger with event types and rollups; trace spans; the knowledge base schema and the seed migration; the manifest loader; frame and contract schemas; the five scopes in SQLite. | Every seed field accounted for in the migration report; `/kb stats` and `/cost` answer; one golden case replays end to end through a stub executor. |
| **1b The loop** | Verifier registry with hooks; controller with the brake and the floor; renderer; judge service in shadow with the first two checklists; outcome-scored eval runner; the gate-order and prompt-injection regressions; the weekly command screener. | All golden cases green in replay on mechanical outcomes; judge shadow logging; the gate-order test green. |
| **2 Shadow** | The current agent still answers; the harness runs on every real ask and logs per-outcome results and base hit rate; a daily diff to the owner. | The owner prefers the harness on most asks. |
| **3 Serve** | The harness answers; the judge annotates where it has earned it; `/prep` builds the next trip's base; the old resolver retires except grammar and the floor. | The tenant's ship gates; owner sign-off. |
| **4 Tenant 2** | The second agent adopts the harness via a manifest, a library, a checklist and a table set; ADR-015's bandit takes its first rewards. | A second golden set green. |

Rules of work: `RAHAT_TEST_MODE=1` on every test and DB command; hermetic tests only; every bug fixed lands as a regression pin; every change to a shared file carries a `COORDINATION:` line; only the owner pushes, touches launchd or flips live flags; the branch is `feat/harness-adr018`.

---

## 5. Consequences

**Gains.** One pipeline and one mental model for every conversational agent; adding one is a manifest. A fabricated or unsourced fact becomes structurally impossible, not merely discouraged. Checking is mechanical where it can be and calibrated where it can't. Every cent and every search query is recorded and attributable. Tests replay real asks against recorded tool results, so a regression is a named case, not a feeling. The second tenant costs data, not code.

**Costs.** Every conversational ask now spends model tokens (roughly a cent to read, a few cents to research, a cent to judge). Latency is 20–60 s for a research ask, mitigated by the holding line, the memo cache, parallel tool calls and a cheap tier for Understand. The judge is advisory for months until labels accumulate. Two shared files change (the model wire and the orchestrator route), which is coordination work. A sliver of keyword logic survives, fenced, for the model-down path.

**What this does not do.** It does not put the model in charge of what counts as a correct answer; code does. It does not remove the deterministic path; it narrows it to exact commands and makes it data. It does not add a paid data feed. It does not change pipeline agents.

---

## 6. Decisions recorded with this ADR (2026-10-03)

One model wire with native tools and schemas; search counted per query; the per-ask brake at 12 / 10 / $0.50; the judge as one Rahat service with per-agent checklists, on the Flash tier at temperature 0 with its version pinned per verdict; authority measured per agent for annotate and per outcome for block; the fenced model-down floor; an open-data geocoder and the government weather API as free, terms-compliant adapters; foundations before the loop with a gate between; the orchestrator route added by the platform lane with coordination; members as ids with roles in code; commands first, then the model, with a weekly screener proposing and the owner approving.

---

## 7. Status and amendments (2026-10-17)

### 7.1 Where each part stands

| Part | State |
|---|---|
| §2.1 Routing | Built: `core/inbound.py` matches the manifest's grammar exactly or not at all; re-cut on 2026-10-17 (A4). The weekly command screener is built (`scripts/command_screener.py`). **Not built:** the memo cache; the database-first path (A3) comes first. |
| §2.2 The eight steps | Live for Bourdain (`core/harness/`), with the tenant's reading rules between Understand and Contract (A1, A4) and a citation sanitizer after Execute: a wrong evidence id costs the citation, never the place. |
| §2.3 One model wire | `core/llm_chat.py` (`chat`, `structured`) carries every harness call, with request-keyed cassettes, the brake, a ledger row and the trace id. **Not done:** three paths outside the harness still reach the model by older routes, outside the brake (Miya's synthesizer, Kobe's reasoner, and Fraser's design, whose cost is not recorded); `core.gemini_reasoner_io.chat` is not yet a call into the wire. |
| §2.4 Manifest | Built as `agents/<name>/manifest.json`, validated by `core/manifest.py`. |
| §2.5 Outcomes and contract | Built; Bourdain's library is `agents/bourdain/outcomes.json`. |
| §2.6 Evidence and knowledge base | Built (`core/kb/`). Added since: a gazetteer of every eating place in open data (`kb_gazetteer`; a row becomes a place only when an ask or a feed touches it), mentions read from food feeds (`kb_mention`) and a nightly re-read of places' own pages, all without a model. Facts grounded on Google Maps are purged after 90 days, per Google's terms. |
| §2.7 Sources and scoped state | Built (`core/sources.py`, `core/sources.json`, `core/state_scopes.py`); amended by A2 and A5. |
| §2.8 Judge | Built (`core/judge.py`) and in **shadow**: the manifest caps its authority there, and it earns annotate only with 50 owner labels at 80% agreement. Corrections label the previous answer since 2026-10-11. |
| §2.9 Cost | Built: the ledger, `/cost` and `/cost export`, the per-ask brake (A1), the fenced floor. No daily cap. |
| §2.10 Traces and evals | Built: a span per step, `scripts/replay_harness.py`, outcome-scored replay (`scripts/eval_outcomes.py`) with a nightly job, reaction labels. The golden set's re-recording is paused until November. |
| §2.11 Gate order | Built; `tests/test_gate_order.py`. |
| Phase 3 | Serving Bourdain since 2026-10-04. **Not done:** `/prep`, building a trip's knowledge base before the trip. |
| Phase 4 | Parked by the owner on 2026-10-11. Fraser checks a workout design against a contract in code and writes its outcomes to the harness's tables, but is not a tenant. |

### 7.2 Amendments

**A1 (2026-10-04, extended 10-05 and 10-11) — Reading rules after Understand; a brake for a wide ask.** The first morning in serve mode answered seven of eight asks with nothing; most causes were readings the model got slightly wrong in ways the household's own words settle. The tenant's `refine_frame` now applies deterministic rules to the model's frame before the contract is assembled: a follow-up keeps what it does not change; a place held up as a style reference is never an exclusion or a verdict; a message that names no one keeps the household's usual party; "my partner's profile" means the partner's rules only. The rules narrow or carry forward what the model read; they never route, and the model still reads every message. A wide ask (a drive of 30 minutes or more, the whole area, "more options") gets up to five picks and its own brake: 18 tool calls, 14 search queries, $0.80 (`budgets.per_ask_wide`).

**A2 (2026-10-14/15) — Feeds whose robots.txt asks readers out.** §2.7 drops a source on a robots block. The owner decided to read a publisher's own calendar subscribe link or RSS feed even where robots.txt asks automated readers out: twenty event calendars (2026-10-14) and four food feeds (2026-10-15), each marked in its registry. A terms block still drops a source; a 403 or a 429 is a failure the daily health line reports; nothing is fetched through a mirror, a cache of a blocked page or a proxy, and a page behind a bot challenge is left out.

**A3 (2026-10-15) — Research only when something is to be found.** Seven of the first nineteen serve-mode asks were a save, a verdict, a question or a reply, and each ran the full research loop. A message whose reading has nothing to find (no eating, planning or place check) now runs Execute without the network tools, in at most two rounds and four tool calls. Words asking for a search ("search", "look online", "use Gemini", "broaden", "more options") or for places the household doesn't know ("something new", "haven't tried") are read in code and always win. Decided the same day, next: a database-first path, in which the model applies the household's taste to the knowledge base in one call and the research loop runs when the list is thin, when the model says so, or when the household asks.

**A4 (2026-10-17) — One brain, one memory (§2.1).** As written, §2.1 sent every command to a deterministic handler. For the first tenant those handlers were the pre-harness pipeline, with stores of its own, so a command and a typed message were answered by two brains with two memories: at home, `/coffee` asked "where are you?" from a past trip's tables, the answer (a town) reached the harness as a new message with no ask behind it, and `/list` showed a past trip's city. Now a command is one of two kinds:

- a **lookup** (`/list`, `/who`, `/where`, `/cost`, `/kb`, `/help`, `/join`, `/household`): answered by code from the knowledge base and the harness's own session, never from a second store;
- an **ask** (`/eat`, `/coffee`, `/dessert`, `/save`, and the exact replies "more", "which one", "something else", "just me", "going"): a typed shortcut into the harness, handed over as the words it stands for (`/coffee near the park` → "coffee near the park", `/eat` → "where should we eat").

The grammar is still exact match or nothing, and nothing inspects free text before the model. Two reading rules join A1: a message that leads with a meal word is that meal; a message that is only a place moves the origin there (A5) and, within 90 minutes of an ask, continues that ask from there with everything else kept. The pre-harness pipeline now answers only when the harness is off or in shadow, or as the fallback when it crashes. An answer that says nothing no longer validates, so an empty reply cannot pass every check.

**A5 (2026-10-17) — Where the household is.** The session scope (§2.7) gains a key every ask reads: `location`, a point with a label and its source, standing for three hours after the last point. It is written by a place named alone in a message (resolved in code from the knowledge base, the landmarks, a table of towns or the gazetteer), by a location shared in Telegram (once, or live, including "until I turn it off"), or by a push from the household's phone to the local bridge. Without one, the harness ranks from home. `/who` shows the point's source and age. Coordinates are never logged.
