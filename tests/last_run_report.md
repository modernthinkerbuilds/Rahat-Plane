# Rahat test report — ❌ FAIL

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | ❌ | 0 | 0 | 0 | 0.02s |
| `contract` | ❌ | 0 | 0 | 0 | 0.02s |
| `eval` | ❌ | 0 | 0 | 0 | 0.02s |
| `adversarial` | ❌ | 0 | 0 | 0 | 0.02s |
| `regression` | ❌ | 0 | 0 | 0 | 0.02s |
| **total** | ❌ | **0** | **0** | **0** | **0.08s** |

## Failures
### `unit` — return-code=1

```

/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

### `contract` — return-code=1

```

/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

### `eval` — return-code=1

```

/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

### `adversarial` — return-code=1

```

/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

### `regression` — return-code=1

```

/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

## Layers
- **unit** — Pure-function unit tests (voice, cost, helpers, no I/O).
- **contract** — Agent ABI + Charter ABI + decisions-ledger invariants.
- **eval** — Scenario-fidelity evals against the Sports Scientist.
- **adversarial** — Prompt injection / jailbreak / PII / hallucination probes.
- **regression** — Replay regression — golden fixtures vs. live router.

> Hermetic guarantee: `RAHAT_TEST_MODE=1` is forced in `tests/conftest.py`. No test can write to `vault/rahat.db`.
