# PRD — The Rahat Plane

> **Note:** The original PRD was authored as a PDF (`PRD_ The Rahat Plane.pdf` in the repo root). This document is a thin markdown index that points to the living specs that have superseded the original PRD as the architecture has matured.

## Where the PRD lives now

The PRD has been decomposed into focused, version-controlled documents that are easier to keep in sync with the actual code:

| Topic | Spec |
|---|---|
| **Target architecture** (current truth) | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| **Three-plane / control-plane decision record** | [`ADR-001-rahat-control-plane.md`](./ADR-001-rahat-control-plane.md) |
| **Memory tier design** (the four-tier substrate) | [`MEMORY-AND-STATE-ARCHITECTURE.md`](./MEMORY-AND-STATE-ARCHITECTURE.md) |
| **Model-first reasoner pivot** (why we left regex routing) | [`MODEL-FIRST-PIVOT.md`](./MODEL-FIRST-PIVOT.md) |
| **Gap analysis** that motivated memory + reasoner | [`SOTA-AGENT-ARCHITECTURE-REVIEW.md`](./SOTA-AGENT-ARCHITECTURE-REVIEW.md) |
| **What's actually shipped** (build status, test counts) | [`SOTA-BUILD-STATUS.md`](./SOTA-BUILD-STATUS.md) |
| **LLM cost model + optimization** | [`LLM-COST-OPTIMIZATION.md`](./LLM-COST-OPTIMIZATION.md) |
| **Operational runbooks** | [`RUNBOOK-miya-cutover.md`](./RUNBOOK-miya-cutover.md), [`RUNBOOK-model-first-cutover.md`](./RUNBOOK-model-first-cutover.md) |
| **Architecture diagrams** (6 standalone SVGs) | [`diagrams/README.md`](./diagrams/README.md) |

## Original PRD (historical)

The original PRD lives at [`/PRD_ The Rahat Plane.pdf`](../PRD_%20The%20Rahat%20Plane.pdf) in the repo root, kept for posterity. It captures the initial vision before the three architectural pivots (three planes → memory substrate → model-first reasoner) reshaped the system. Use it for context, not as a current source of truth.

## Where to start

If you're **new to the project** and want the fastest path to understanding it:
1. Read the [Rahat-Plane README](../README.md) — vision + architecture in one page
2. Skim [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the current target
3. Look at [`diagrams/04-memory-architecture.svg`](./diagrams/04-memory-architecture.svg) and [`diagrams/05-model-first-reasoner-flow.svg`](./diagrams/05-model-first-reasoner-flow.svg) for the visual model
4. Read [`MEMORY-AND-STATE-ARCHITECTURE.md`](./MEMORY-AND-STATE-ARCHITECTURE.md) and [`MODEL-FIRST-PIVOT.md`](./MODEL-FIRST-PIVOT.md) for the *why*
5. Check [`SOTA-BUILD-STATUS.md`](./SOTA-BUILD-STATUS.md) for what's actually shipped vs. roadmap

If you're **looking for the contract a new agent has to satisfy** to plug into the mesh:
- See [`ARCHITECTURE.md`](./ARCHITECTURE.md) §13 (mesh extensibility) and the agent base in `core/agent.py`
- Use `agents/the_scientist/memory.py` as the reference implementation for the per-agent memory adapter pattern.
