# Rahat Architecture — 2026-05-30 (v1.1)

Reflects the PM thesis v1.1 corrections: **one substrate, two learners**;
**cross-agent typed signal interface elevated to load-bearing**; **sovereign
runtime is one of a candidate set (OpenClaw recommended, LangGraph+Letta
fallback)** — no ownership claim.

```mermaid
flowchart TB
  classDef loadBearing fill:#fff3b0,stroke:#c89028,stroke-width:3px,color:#000
  classDef agent fill:#d4e8f7,stroke:#3a6ea5,color:#000
  classDef engine fill:#f4d4d4,stroke:#a83232,color:#000
  classDef substrate fill:#e8e8e8,stroke:#666,color:#000
  classDef runtime fill:#d4f0d4,stroke:#3a8c3a,color:#000

  USER([👤 User / Household — multi-Subject])
  CHAN[Channel Adapters<br/>Telegram now · Slack / WhatsApp / iOS future]
  USER --> CHAN

  MIYA["🎤 <b>Miya</b> — Arbitrating Orchestrator<br/>single voice · conflict mediation · shared state across specialists"]:::loadBearing
  CHAN --> MIYA

  subgraph MESH["Specialist Agent Mesh (Subjects-aware)"]
    direction TB
    subgraph LIVE["Live"]
      KOBE[Kobe<br/>Vitality]:::agent
      FRASER[Fraser<br/>Programming]:::agent
      HUBE[Huberman<br/>Recovery / HRV]:::agent
    end
    subgraph W1["Wave 1 (next)"]
      GENIE[Genie<br/>Weekend Family]:::agent
      DISNEY[Disney<br/>Kids]:::agent
      POLO[Polo<br/>Pre-trip]:::agent
      BOURD[Bourdain<br/>In-trip]:::agent
      SANTA[Santa<br/>Gifts]:::agent
    end
    subgraph W2["Wave 2+ (substrate-gated)"]
      CASA[Casanova]:::agent
      MONT[Montessori]:::agent
      RAMSAY[Ramsay]:::agent
      SHERLOCK[Sherlock]:::agent
      MOCHA[Mocha]:::agent
      MORE[Ramu · Antoinette · Luwak · …]:::agent
    end
  end

  MIYA <==> MESH

  SIGNAL["⚡ <b>Cross-Agent Typed Signal Interface</b><br/>LOAD-BEARING — the mesh-compounding primitive<br/><i>Without this, mesh + engine collapses to mesh.</i><br/>Every agent publishes outcomes through this; every reasoner reads through this."]:::loadBearing
  MIYA -.publishes outcomes.-> SIGNAL
  MESH -.publishes outcomes.-> SIGNAL

  subgraph ENGINE["<b>Outcome-Validated Learning Engine</b> — 1 substrate, 2 learners"]
    direction TB
    CB["<b>Contextual-Bandit Learner</b><br/>5 immediate-decision surfaces:<br/>• Model routing (cost)<br/>• Prompt-variant routing<br/>• Memory ranking<br/>• Personalization<br/>• Nudge timing"]:::engine
    TRAJ["<b>Trajectory Policy Learner</b><br/>1 multi-day surface:<br/>• Conflict-policy learning<br/>(Fraser-vs-Huberman, etc.)"]:::engine
    ESUB["<b>Shared substrate</b><br/>outcome capture · replay · counterfactual · drift monitor"]:::engine
    CB --- ESUB
    TRAJ --- ESUB
  end

  SIGNAL --> ENGINE
  ENGINE -.routing / policy.-> MIYA
  ENGINE -.routing / policy.-> MESH

  subgraph SUB["Substrate Primitives — the durable bones"]
    direction LR
    CHARTER[<b>Charter</b><br/>policy chokepoint]:::substrate
    SUBJ[<b>Multi-Subject state</b><br/><code>Subject</code> interface — no hard-coded "family"]:::substrate
    TRACE[<b>by_trace</b><br/>decision ledger]:::substrate
    MEM[<b>Memory</b><br/>outcome-conditioned + provenance + revocation]:::substrate
    RECEIPT[<b>Compliance Receipts</b><br/>EU AI Act / OWASP / HIPAA-ready]:::substrate
    REPLAY[<b>Replay + Counterfactual</b><br/>policy-change A/B over real traces]:::substrate
  end

  MIYA --> SUB
  MESH --> SUB
  ENGINE --> SUB

  SUB --> RUNTIME

  RUNTIME["🏠 <b>Sovereign Runtime</b><br/>OpenClaw (TS) recommended · LangGraph + Letta (Python) fallback<br/>Mac mini · your data · your model choices · no SaaS dependency"]:::runtime
  RUNTIME --> MODELS
  MODELS["<b>Model Providers</b> — provider-abstract<br/>Gemini Flash · Gemini Pro<br/>(swappable; engine learns the routing)"]:::runtime
```

## Legend

- **Yellow (load-bearing):** Miya and the cross-agent typed signal interface. If either fails, the moat collapses.
- **Red:** The engine — one substrate, two learners (per the architect/PM precision in v1.1).
- **Blue:** Specialist agents — three live, ~6 in Wave 1, ~7 in Wave 2+, gated on substrate maturity.
- **Gray:** Substrate primitives — the durable bones; every one ladders to enterprise.
- **Green:** Sovereign runtime + model providers — provider-abstract.

## What the diagram makes load-bearing on purpose

1. **The yellow signal interface is the mesh-compounding primitive.** Without the typed contract enforcing publication *and* consumption, cross-agent learning fragments into per-agent silos and the moat dissolves to "agents on a shared substrate" (forkable).
2. **The engine is one substrate, two learners.** The contextual-bandit learner powers five immediate-decision surfaces; the trajectory policy learner powers conflict-policy learning. Sharing the substrate (outcome capture / replay / counterfactual / drift) keeps engineering coherent without claiming "one engine" where the math differs.
3. **The runtime is one of several candidates.** OpenClaw is the recommendation, not a structural position — Rahat's substrate is OpenClaw-compatible but not OpenClaw-dependent (runtime-agnostic packages with an OpenClaw adapter, per the architect vote).

## What the diagram explicitly does NOT claim

- Not "we own the runtime." Rahat sits on top of an external runtime (correction landed 2026-05-30).
- Not "one engine, six surfaces." Six surfaces, two learners, one substrate.
- Not "vertical-locked." Fitness is the laboratory; the Subject interface and signal-typing keep the architecture vertical-portable.

## The three load-bearing arguments this architecture serves

1. **Engine + substrate primitives proven on the hardest single-user case** (laboratory thesis).
2. **Cross-agent typed signal interface** — the mesh-compounding primitive that makes mesh + engine > mesh.
3. **Laboratory-as-graduation path** — every primitive is built to ladder to enterprise without re-architecture.

## What is explicitly NOT in scope

- Not the autonomy planner (OpenClaw / LangGraph fills that role; Rahat governs).
- Not a horizontal enterprise control plane (don't fight Google / AWS / Microsoft / Kore.ai).
- Not a memory-product wrapper (Letta / Mem0 own that lane).
