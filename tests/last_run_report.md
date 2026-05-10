# Rahat nightly 2026-05-10 — PASS (after auto-fix)

| Layer | Status | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| `unit` | PASS | 28 | 0 | 0 |
| `contract` | PASS | 40 | 0 | 0 |
| `eval` | PASS | 43 | 0 | 1 |
| `adversarial` | PASS | 14 | 0 | 0 |
| `regression` | PASS | 17 | 0 | 0 |
| **total** | **PASS** | **142** | **0** | **1** |

> Hermetic guarantee: `RAHAT_TEST_MODE=1` enforced in `tests/conftest.py`.
> No Gemini, no Telegram, no live DB writes.

## Status

- **Before auto-fix:** FAIL — 1 eval test failing (`TestPaceStatus::test_weigh_in_window`); other 4 layers green.
- **After auto-fix:** PASS — all 5 layers green, 142 passed / 0 failed / 1 skipped.

## What broke

`tests/evals/test_scientist_conversation.py::TestPaceStatus::test_weigh_in_window`

```
assert "weigh" in out
AssertionError: assert 'weigh' in 'last hammer: sat may 9 (33h ago).
inflammation peaks at 24–36h. wait until *mon may 11* morning.
tonight: low sodium, 3l water, 7/15 breathing, dinner by 7pm.'
```

The test queries `"when should I weigh in"` and asserts the response
contains the substring `weigh`. The router correctly dispatched to
`handle_weighin_when()`, but the `<36h since hammer` branch of that
handler tells the user "Wait until *Mon May 11* morning." without
naming **what** they're being scheduled for.

The other two branches of the same handler (36–60h and >60h) both
say "Weigh tomorrow morning..." / "Weigh in tomorrow morning...". The
short-window branch was an inconsistency: same intent, same handler,
but the user-facing string dropped the verb that names the action.

## What changed

- `agents/the_scientist/main.py` (`handle_weighin_when`, line ~1147):
  rewrote the `<36h` branch from `"Wait until *<day>* morning."` to
  `"Wait until *<day>* morning to weigh in."`. Restores parity with
  the other two branches and makes the recommendation legible without
  context. No numbers, markdown markers, or structural fields changed;
  only added `to weigh in ` after the existing date marker.

This change is in `agents/`, not `core/charter.py` or `core/voice.py`,
so user-facing safety semantics are unaffected.

## Auto-fix iteration count

1 / 3 — single coherent edit, target test went green on first re-run,
full suite re-ran clean.

## Artifacts on this branch

- `tests/last_run_report.md` (this file)
- `tests/last_run_status.json` — `pass: true`, `post_auto_fix: true`
- `tests/last_run.json` — per-layer detail
- `tests/last_run_stdout.log` — raw pytest output
