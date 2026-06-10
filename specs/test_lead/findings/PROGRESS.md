# Agent shift — 2026-06-10

Test Lead agent. Branch `test-lead-agent-2026-06-10`, base `origin/main`
@ `cfb43a9`. Budget: 15 hours. Goal: measurably stronger suite + clean
handoff. Tests only — no production edits.

## Baseline (before any agent work)

Layer runner (`RAHAT_TEST_MODE=1 python -m tests.run_all`) — exit 0:

| Layer | Status | Passed | Failed | Skipped | Time |
|---|---|---:|---:|---:|---:|
| `unit` | PASS | 28 | 0 | 0 | 0.46s |
| `contract` | PASS | 849 | 0 | 17 | 12.69s |
| `eval` | PASS | 101 | 0 | 1 | 3.75s |
| `adversarial` | PASS | 14 | 0 | 0 | 0.55s |
| `regression` | PASS | 17 | 0 | 0 | 0.33s |
| **total** | PASS | **1009** | **0** | **18** | **17.77s** |

New plane (run via direct pytest — NOT in the layer runner):
`RAHAT_TEST_MODE=1 pytest tests/new_plane/` → **446 passed in 16.96s**
(excluding 2 files that fail *collection* in this sandbox — see Env notes).

**Baseline gate: GREEN.** Shift starts.

## Environment notes (sandbox divergence — reality wins)

- The committed `.venv` / `venv` point to macOS Homebrew pythons
  (`python@3.11`, `python@3.12`) that don't exist in the Linux sandbox.
  Built a fresh venv at `/tmp/rvenv` on system Python 3.10.12 with
  `pytest hypothesis pytest-cov pytest-xdist requests python-dotenv
  fastapi uvicorn pydantic`. `google-genai` intentionally NOT installed —
  `tests/conftest.py` stubs `google.genai`, so the suite is hermetic
  without it.
- `tests/new_plane/test_adapter_integration.py` and
  `test_openclaw_adapter.py` fail at COLLECTION with
  `RuntimeError: The starlette ...` (FastAPI `TestClient`/starlette
  version mismatch under pytest 9 in this sandbox). This is an env/dep
  issue, not a suite defect, and both files are outside the test-lead
  focus areas (HTTP adapter / OpenClaw). I run new_plane with
  `--ignore` for those two. Logged in BLOCKERS.md.

## Agent mental model: 2026-06-10 hour 0

How one test call reaches a verdict, and the hermetic guard:

1. `tests/conftest.py` runs at import time **before any rahat module**:
   sets `RAHAT_TEST_MODE=1` (this is the hermetic block — it forces
   `core/io.py:_resolve_db_path` to redirect every write to a per-process
   tempfile, so a buggy test can't corrupt `vault/rahat.db`; this guard
   exists because of the 2026-05-08 corruption incident). It also stubs
   `google.genai` with `_StubClient` (real LLM calls return the literal
   `[LLM-FALLBACK]`), and adds repo root to `sys.path`.
2. `python -m tests.run_all` runs 5 layers, **each in its own
   subprocess** (`run_layer` → `subprocess.run([python, -m, pytest, -q,
   <paths>])`), so a crash in one layer doesn't sink the others. Exit
   code is 0 only if every layer passed. Results render to
   `tests/last_run_report.md`.
3. **The layer runner does NOT include `tests/new_plane/`.** Only
   `tests/regression_registry/` (inside the `contract` layer) overlaps
   with my work. So my new_plane / adversarial / synth-grounding files
   must be validated with direct `pytest`, and my Bug-H/Bug-I **registry**
   files must stay green under `run_all` (contract layer).

### Routing & synth wiring (the surfaces I'll test most)

- `new_plane/miya_runner/delegate_classifier.py:classify_delegation(msg)`
  → returns `(path, stripped)` where path ∈ {`kobe_route`,
  `fraser_route`, `orchestrate`}. (`huberman` funnels to `kobe_route`;
  `@miya`/empty/whitespace → `orchestrate`.) 9 ordered checks: @-address,
  slash, plan-mutation, state-logs, status-query, pain/profile, recovery,
  WOD-lookup (guarded against design intent), else orchestrate.
- `new_plane/miya_runner/orchestrator.py:handle(Turn)` → `Response`
  (fields incl. `text`, `arbitration_rule`, `used_tools`, `routing`).
  On a delegation path it calls `adapter.kobe_route`/`fraser_route`
  (native_client, direct import) and returns deterministic text. On
  `orchestrate` it pulls facts via `adapter.kobe_*`, calls
  `arbitrate(facts)`, charter-checks, cost-routes, and `synthesize()`s.
- `arbitrate(facts)` (in `new_plane/miya_sim/orchestrator.py`) fires
  `behind_pace` when `recalibration.behind_pace is True`; `goal_close`
  when an active goal has `0 < weeks_to_target < 1`. Pure function.
- `synthesizer._build_prompt(*, user_message, facts, arbitration,
  fraser_text, recent_signals, chat_memory_block=None)` is keyword-only.
  **`arbitration` dict must carry BOTH `rule` and `guidance`** (it reads
  `arbitration['guidance']` — passing only `rule` KeyErrors). The
  `SYSTEM_PROMPT` already carries the load-bearing grounding lines:
  honesty ("do not say 'ahead of pace' … when recalibration says
  behind"), "Synced WOD is the source of truth … do not paraphrase".
- **Offline grounding gap:** `synthesizer._structured_fallback` (used
  when no GEMINI_API_KEY) dumps `recalibration.summary` verbatim. So with
  a misleading summary ("Ahead of pace"), offline `Response.text`
  contains "ahead of pace" even when arbitration fired `behind_pace`.
  Text-level Bug-H prevention is therefore a *live-model* property; the
  deterministic pin is `arbitration_rule`. The text gap is exactly what
  the synth-grounding harness flags (xfail + PROPOSED_FIX).

Hermetic block answer: enforced at the top of `tests/conftest.py`
(`os.environ["RAHAT_TEST_MODE"] = "1"` + `google.genai` stub), which
prevents (a) writes to the live `vault/rahat.db` and (b) live network /
LLM calls. Off-limits to me.
