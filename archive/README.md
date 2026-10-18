# Archive

Documents, scripts and tests that no longer describe or run the system, moved here on October 18, 2026 so the rest of the repository shows only what's in force. Nothing here is imported, collected by the test suite, run by CI or installed by launchd. Code comments elsewhere that mention an old path mean the file at its new path below; git history follows every move.

For the system as it runs, start at [`specs/README.md`](../specs/README.md) and [`specs/ARCHITECTURE.md`](../specs/ARCHITECTURE.md).

## Decisions and design notes

| Old path | Now | Why it moved |
|---|---|---|
| `specs/ADR-001-rahat-control-plane.md` | [`specs/ADR-001-rahat-control-plane.md`](./specs/ADR-001-rahat-control-plane.md) | The first Now/Next/Later plan (May), never accepted; the roadmap is ARCHITECTURE.md §15 |
| `specs/ADR-006-capability-based-router.md` | [`specs/ADR-006-capability-based-router.md`](./specs/ADR-006-capability-based-router.md) | An LLM router over agent descriptions. The Miya bot routes by the ladder in `new_plane/miya_runner/delegate_classifier.py` instead, and Kobe delegates by patterns; the classifier survives only in the library orchestrator `core/miya.py`, whose tests still run |
| `specs/MODEL-FIRST-PIVOT.md` | [`specs/MODEL-FIRST-PIVOT.md`](./specs/MODEL-FIRST-PIVOT.md) | The May pivot to a reasoner-first Kobe; Kobe's dispatcher now answers first (ADR-009) |
| `specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md` | [`specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md`](./specs/SOTA-AGENT-ARCHITECTURE-REVIEW.md) | May research notes behind the memory design |
| `specs/MEMORY-AND-STATE-ARCHITECTURE.md` | [`specs/MEMORY-AND-STATE-ARCHITECTURE.md`](./specs/MEMORY-AND-STATE-ARCHITECTURE.md) | The May memory design; what was built is ARCHITECTURE.md §8.4 and §8.5 |
| `specs/LLM-COST-OPTIMIZATION.md` | [`specs/LLM-COST-OPTIMIZATION.md`](./specs/LLM-COST-OPTIMIZATION.md) | A May cost analysis, partly superseded that month; spend is ARCHITECTURE.md §10.1 |
| `specs/OPENCLAW_INTEGRATION_GUIDE.md` | [`specs/OPENCLAW_INTEGRATION_GUIDE.md`](./specs/OPENCLAW_INTEGRATION_GUIDE.md) | How to wire the OpenClaw path, which is parked (ADR-014) |
| `OPENCLAW_LEVERAGE_2026-05-27.md` | [`OPENCLAW_LEVERAGE_2026-05-27.md`](./OPENCLAW_LEVERAGE_2026-05-27.md) | A May memo on OpenClaw; ADR-014 is the decision |
| `specs/Rahat_21Agents_FinalPlan.xlsx`, `specs/Rahat_21Agents_Scoring.xlsx` | [`specs/`](./specs/) | Planning spreadsheets for a 21-agent roadmap |
| `specs/diagrams/` | [`specs/diagrams/`](./specs/diagrams/) | The v2.0 drawings (May); the interactive pages replace them |

## Scripts

| Old path | Now | Why it moved |
|---|---|---|
| `scripts/scientist.sh` | [`scripts/scientist.sh`](./scripts/scientist.sh) | Start and stop for the v0 Scientist service, retired |
| `scripts/switch_to_miya.sh` | [`scripts/switch_to_miya.sh`](./scripts/switch_to_miya.sh) | The one-time switch from the Scientist service to the first Miya, done |
| `scripts/cleanup_pre_scale.sh` | [`scripts/cleanup_pre_scale.sh`](./scripts/cleanup_pre_scale.sh) | A one-time cleanup from June |

## Tests that no longer run

| Old path | Now | Why it moved |
|---|---|---|
| `tests/scientist/` | [`tests/scientist/`](./tests/scientist/) | The May eval scripts (v2.0's 475 cases). pytest collects only `test_*.py`, and nothing else runs these |
| `tests/scenarios/` | [`tests/scenarios/`](./tests/scenarios/) | May scenario scripts, likewise never collected or run |

## Not archived, and why

- **`core/miya.py`**, the first orchestrator, stays: the Miya runner uses it as the agent registry for Kobe's delegation, and the contract tests exercise it.
- **The OpenClaw adapter and plugin** (`bridges/openclaw_adapters/`, `new_plane/openclaw_plugin/`) stay: ADR-014 parks them rather than retiring them, tests import the adapter, and `scripts/weekend_setup.sh` installs the plugin.
- **`new_plane/learn/bandit.py`** stays: nothing imports it yet, but it is the first piece of the learning engine ADR-015 proposes.
- **`tests/nightly.sh`** stays: a scheduled run may still call it.

Also on October 18: `tests/last_run.json` and `tests/last_run_stdout.log`, outputs of a May test run, stopped being tracked; each run writes them again, and they are ignored like the other run reports.
