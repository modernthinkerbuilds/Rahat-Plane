# Specs: start here

Rahat's design documents. If you read one thing, read **[ARCHITECTURE.md](./ARCHITECTURE.md)**: the system as built (v3.0, October 17, 2026). If you'd rather watch it move, open the two interactive pages:

- **[Rahat Architecture](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html)**: the whole system on one map ([source](./RAHAT-ARCHITECTURE.html))
- **[Rahat Harness Architecture](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/ADR-018.html)**: inside one agent, the harness ([source](./ADR-018.html))

## What's in this folder

| Path | What it is |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | The system as built: services, routing, agents, the harness, data, the control plane, reliability, tests, privacy, roadmap |
| `ADR-0NN-*.md` | The decisions in force, indexed below |
| [`CONVENTIONS.md`](./CONVENTIONS.md) | Code conventions (a regression test reads it) |
| [`agents/`](./agents/) | Agent specs and runbooks (Genie) |
| [`img/`](./img/) | The previews the README shows |
| `RAHAT-ARCHITECTURE.html`, `ADR-018.html` | Sources of the two interactive pages |

## Decisions in force

An ADR records a decision as it was made. Its **Status** line says whether it is accepted, amended or proposed; a dated **As built** line, where there is one, says how it runs today. For the present, ARCHITECTURE.md is the description and the ADR is the record of why.

| Theme | ADR | The decision | Status |
|---|---|---|---|
| Principle | [011](./ADR-011-deterministic-shell-llm-core.md) | Deterministic shell, LLM core: state, arithmetic, routing and vetoes are code; intent, taste and phrasing are the model's | Accepted |
| The harness | [018](./ADR-018-outcome-contracts-and-independent-evaluation.md) | Outcome contracts and independent evaluation: eight steps per answer, checked in code, with a judge | Accepted; in production since Oct 4; amends 005, 009, 012, 016 and 017 |
| Routing | [009](./ADR-009-single-dispatcher.md) | One ordered dispatch table answers what it recognizes before any model | In production inside Kobe, on by default |
| Routing | [007](./ADR-007-cross-agent-delegation.md) | An agent hands an out-of-domain question to the agent that owns it | Accepted |
| Routing | [008](./ADR-008-clarification-policy.md) | Ask a short question when routing is unsure | Accepted; the Miya bot's version is a pending clarification with a time limit |
| Routing | [017](./ADR-017_nl_intent_layer.md) | A plain-language intent layer between the dispatcher and the reasoner | Accepted; behind a flag that is off |
| Agents and state | [002](./ADR-002-rebrand-risk.md) | New names outside, old keys inside: Kobe's code and records still say `scientist` | Accepted |
| Agents and state | [003](./ADR-003-multi-agent-storage-convention.md) | New agents keep state in shared memory, not in Kobe's tables | Accepted; harness tenants use the knowledge base (ADR-018) |
| Agents and state | [004](./ADR-004-five-file-agent-pattern.md) | The five-file agent pattern | Accepted; Kobe, Fraser and Genie follow it; a harness tenant declares a manifest |
| Agents and state | [013](./ADR-013_migrate_to_new_plane.md) | The `new_plane/` runners are the runtime; the agents are libraries they import | Accepted; done in June |
| Agents and state | [016](./ADR-016_platform_seams.md) | Seams for a second agent, surface or tenant, added before they're needed | Accepted; amended by ADR-018 |
| Runtime | [012](./ADR-012-shared-tool-calling-runtime.md) | One shared tool-calling runtime | Accepted as amended by ADR-018 (the harness's Execute step) |
| Spend | [005](./ADR-005-budget-enforcement.md) | A token-budget gate | Accepted; amended by ADR-018 (a per-ask brake) |
| Position | [014](./ADR-014_openclaw_position.md) | OpenClaw is a parked adapter, not the runtime | Accepted |
| Proposed | [015](./ADR-015_outcome_validated_learning_engine.md) | A learning engine that changes behaviour from outcomes | Proposed, design only, not built |

There is no ADR-010. The next decision is ADR-019.

## Archived

On October 18, 2026, documents that no longer describe the running system moved to [`../archive/`](../archive/), so this folder shows what's in force. Among them: ADR-001 (the first Now/Next/Later plan, never accepted), ADR-006 (an LLM router the Miya bot doesn't use), the May design notes (the model-first pivot, the agent-architecture review, the memory design, the cost analysis), the OpenClaw integration guide, the 21-agent planning spreadsheets and the v2.0 diagrams. [`archive/README.md`](../archive/README.md) maps each old path to its new one.
