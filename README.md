<div align="center">

# 🪶 Rahat

### An operating system for a household of personal AI agents — built at human scale

*One shared memory, one rulebook, one orchestrator, and a record of every decision — so a fleet of personal agents coordinates instead of colliding, and adding the next one is configuration, not a rebuild.*

[Why it's different](#why-rahat-is-different) · [Running today](#whats-running-today) · [The bet](#what-im-building-toward--the-bet) · [How it works](#how-it-works) · [Shipped](#shipped-status) · [Roadmap](#roadmap)

</div>

---

## Why Rahat is different

Most "multi-agent" systems are a switchboard: a router with a nice front-end that forwards your question to one of several bots. Rahat is the **layer underneath** the agents — the part that's hard to get right and easy to fake.

Three things separate it from a chatbot or a router, each running today:

1. **It reconciles conflict.** When two specialists disagree, you get *one* answer, not two — the differentiator a switchboard structurally can't offer.
2. **It catches itself being wrong.** Every reply is fact-checked against what's actually true before it sends.
3. **It can prove what it did.** Every decision is logged with its reasons, and replayable.

Below each is shown concretely. The bigger bet — agents measured against real outcomes — is stated separately and honestly, because it isn't shipped yet.

---

## What's running today

A real system I message daily, not a slide.

**One answer when specialists disagree.** I ask Miya if I can train hard tomorrow. Kobe — the training-science specialist — has a heavy day on the plan; my recovery signal is in the red. A switchboard forwards both and lets me referee. **Miya** weighs them and returns one answer — *not tomorrow, here's why* — in a single voice. He owns the conversation; adding a specialist never changes the interface. *(Today this reconciliation is best-effort signal arbitration; making it deterministic is on the build path.)*

**It catches itself being wrong.** Miya once told me, cheerfully and wrongly, that my max was a number I'd never lifted. Nothing was checking the model. Now every reply is grounded against a canonical profile of what's true and rewritten if it drifts — proven by replaying 126 real messages: internal-voice leaks → 0%, invented facts caught and corrected.

**Governance you can't route around.** Ask for a 6am session at 11pm and the **Charter** — the single policy layer every action passes through — sees quiet hours *and* a red recovery flag, and blocks it before any agent acts. One rulebook, written once, applied to every agent, with an audit log of every verdict.

**Every decision on the record — ask why.** Each routing decision, tool call, and policy verdict lands as one row: which agent, which rule, latency, cost. When an answer surprises you, you replay the exact path instead of guessing.

**Sovereign, and model-swappable.** It runs on a Mac Mini at home — state never leaves the machine — and the model is a component behind an interface: swap today's best for tomorrow's better and the memory, history, and rules stay put. *(The model proposes; tested, deterministic code does the math and enforces the rules, so numbers are never hallucinated.)*

---

## What I'm building toward — the bet

**Not shipped.** This is the direction the architecture is pointed, listed honestly so you can tell vision from today — and it's *why* the foundation above is built the way it is.

- **Outcomes, not answers.** A loop that closes on whether life actually got *better*, and proves a cheaper model produced an outcome-equivalent result. Optimizing the reply is easy; optimizing the outcome is the whole game.
- **Memory that remembers what *mattered*.** Shared, typed memory is table stakes (Letta, Mem0). The differentiated bet is recall conditioned on what changed an outcome, not on what was recent.
- **Provenance for every fact.** Each fact tagged with source, freshness, and a right-to-delete — so memory, audit, and sovereignty become one story, not three.

---

## How it works

**Governing principle ([ADR-011](./specs/ADR-011-deterministic-shell-llm-core.md)): deterministic shell, LLM core.** The substrate — state, math, routing, persistence, vetoes — is deterministic and tested. The intelligence — intent, phrasing, structure — is the model's. Every agent is defined identically — `Agent = { name, description, system_prompt, tools[] }` — so the next agent is mostly a prompt and a tool list.

```
        you (Telegram, sensors)
                 │
                 ▼
            ┌──────────┐     ┌────────────────────────────┐
            │   Miya   │ ──► │  Charter (policy)          │ ◄── every action
            │ orchestr.│     │  approve · modify · veto   │     passes through
            └────┬─────┘     │  + audit log               │
                 │           └────────────────────────────┘
       ┌─────────┼─────────┐
       ▼         ▼         ▼
   specialists (train, recover, …)  →  reconciled to one voice
                 │
                 ▼
   voice sink + fact-validator (grounded against a canonical profile)
                 │
                 ▼
   shared typed memory · intent & decision ledger  (local SQLite)
                 ▲
       nightly consolidation (summarize · decay · archive)
```

---

## Shipped status

| Component | Status | What it is |
|---|---|---|
| **Miya** — orchestrator | ✅ Live | One voice out, specialists reconciled behind him; two bots consolidated into one surface |
| **Charter** — policy layer | ✅ Live | Every action checks it first; written once, applied to all; audited |
| **Shared typed memory** | ✅ Live | Events, facts, decaying preferences, archive + nightly consolidation |
| **Voice sink + fact-validator** | ✅ Live | Re-voiced to one voice, checked against a canonical profile; wrong facts rewritten before send |
| **Decision ledger** | ✅ Live | One row per decision — agent, rule, latency, cost; replayable |
| **Deterministic shell / LLM core** | ✅ Live | Model proposes, tested code disposes; nothing hallucinated |
| **Test discipline** | ✅ Live | Five-layer hermetic suite; every fix adds a regression test; pre-push gate blocks red |
| Outcome loop · outcome-conditioned memory · provenance | 🔭 The bet | Built toward, not shipped — see above |
| Everyday mesh (Genie, Santa, …) | 🔜 Roadmap | Next agents, ~1 day each on the shared contract |

---

## Roadmap

The OS points away from fitness toward ordinary moments. Swap *"can I train tomorrow?"* for *"what should we do this weekend?"* and the same machinery runs: **Genie** checks the baby skipped his nap, that Saturday's open, that last week's crowded museum was a miss — one plan, not three tabs. Same memory, same rulebook, a new lens.

| Agent | The everyday job | Status |
|---|---|---|
| **Genie** | Plans the family weekend around everyone's real energy | 🔜 Next |
| **Santa** | Gets the gift right because it remembers what people love | 🔜 Next |
| **Ramsay** | Figures out dinner from what's in the kitchen and who's eating | 🔜 Next |
| *(+ more across travel, calendar, pantry, music)* | | 🔜 Later |

Each agent is named for someone whose life embodies its job — a reminder it should be as good at its one thing as its namesake was.

---

## Status & who's building it

Rahat is a **personal build, not a product** — personal-scale, architecture public, personal data private. The *problems* generalize to any fleet; the product doesn't, and I won't imply otherwise.

Built by **Venkat Sadras — a Bay Area PM at a large tech company, hands-on with multi-agent systems** — with a toddler, a newborn, and a long interest in where agent platforms are going. First version shipped during parental leave, between naps. Views my own, not my employer's.

Runtime-agnostic: Rahat runs on its own local Python runtime today; an [OpenClaw](https://github.com/openclaw/openclaw) adapter is kept ready as **one integration target, not a foundation** ([ADR-014](./specs/ADR-014_openclaw_position.md)) — credit to that team. Architecture and docs: MIT.

---

<div align="center">

*"The future of personal AI isn't a smarter chatbot. It's a system that remembers what mattered, coordinates, and can prove it did right by you."*

</div>
