# Rahat test report — ✅ PASS

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | ✅ | 12 | 0 | 0 | 0.17s |
| `contract` | ✅ | 30 | 0 | 0 | 0.26s |
| `eval` | ✅ | 43 | 0 | 1 | 0.26s |
| `adversarial` | ✅ | 14 | 0 | 0 | 0.32s |
| `regression` | ✅ | 17 | 0 | 0 | 0.22s |
| **total** | ✅ | **116** | **0** | **1** | **1.23s** |

## Layers
- **unit** — Pure-function unit tests (voice, helpers, no I/O).
- **contract** — Agent ABI + Charter ABI invariants.
- **eval** — Scenario-fidelity evals against the Sports Scientist.
- **adversarial** — Prompt injection / jailbreak / PII / hallucination probes.
- **regression** — Replay regression — golden fixtures vs. live router.

> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in `tests/conftest.py`. No test can write to `vault/rahat.db`.
