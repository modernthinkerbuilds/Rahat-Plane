# Rahat test report — ✅ PASS

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | ✅ | 28 | 0 | 0 | 0.21s |
| `contract` | ✅ | 551 | 0 | 1 | 2.46s |
| `eval` | ✅ | 53 | 0 | 1 | 0.75s |
| `adversarial` | ✅ | 14 | 0 | 0 | 0.27s |
| `regression` | ✅ | 17 | 0 | 0 | 0.23s |
| **total** | ✅ | **663** | **0** | **2** | **3.92s** |

## Layers
- **unit** — Pure-function unit tests (voice, cost, helpers, no I/O).
- **contract** — Agent ABI + Charter ABI + decisions-ledger invariants.
- **eval** — Scenario-fidelity evals against the Sports Scientist.
- **adversarial** — Prompt injection / jailbreak / PII / hallucination probes.
- **regression** — Replay regression — golden fixtures vs. live router.

> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in `tests/conftest.py`. No test can write to `vault/rahat.db`.
