# Rahat test report — ✅ PASS

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | ✅ | 28 | 0 | 0 | 0.16s |
| `contract` | ✅ | 40 | 0 | 0 | 0.22s |
| `eval` | ✅ | 43 | 0 | 1 | 0.24s |
| `adversarial` | ✅ | 14 | 0 | 0 | 0.22s |
| `regression` | ✅ | 17 | 0 | 0 | 0.20s |
| **total** | ✅ | **142** | **0** | **1** | **1.04s** |

## Layers
- **unit** — Pure-function unit tests (voice, cost, helpers, no I/O).
- **contract** — Agent ABI + Charter ABI + decisions-ledger invariants.
- **eval** — Scenario-fidelity evals against the Sports Scientist.
- **adversarial** — Prompt injection / jailbreak / PII / hallucination probes.
- **regression** — Replay regression — golden fixtures vs. live router.

> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in `tests/conftest.py`. No test can write to `vault/rahat.db`.
