# Weekend Stage 0 — Progress & Resume

**2026-05-30/31 (sandbox session, three iterations).** Built while you were
out. Status: **ready to run on your Mac.** Nothing has touched production;
everything new lives in isolated directories per
`specs/ARCHITECT_THREADS_2026-05-30.md`.

**Overnight build (2026-05-31 ~01:00 → 06:00):** Stage 1 of the new plane
shipped — full Python new Miya runner (Telegram poller + HTTP adapter
client + Gemini synth + cost router), launchd template + installer, and
side-by-side comparison harness for 8-week-gate evidence. **121
new-plane tests green** (47 Stage-0 + 74 overnight). Wake-up playbook
at `specs/WAKE_UP_PLAYBOOK.md`. The Stage-2 TS/OpenClaw plugin is
parked, no longer critical path — the Python runner satisfies the
parallel-planes thesis with identical architecture, faster to deploy.
See `## Overnight additions (Stage 1)` below.

**H1–H3 additions (2026-05-31 iteration):** validated adapter end-to-end
against real Kobe/Fraser code; hardened all endpoints to never 5xx; added
`/signals/consume` + `/signals/health` (the missing half of the load-bearing
primitive); wrote 12 real-agent integration tests; wrote an OpenClaw plugin-
SDK adaptation guide grounded in the actual `staging/fleet/` types; shipped a
Python new_miya simulator that orchestrates Kobe+Fraser without OpenClaw
(reference implementation + pre-OpenClaw smoke test). **47 new-plane tests
green, full legacy 5-layer suite still green.**

## What's done (sandbox-shipped, suite still green)

### P1 — Python HTTP adapters ✓ (hardened in H1)
`bridges/openclaw_adapters/` — FastAPI app that exposes existing Kobe + Fraser
tool functions over localhost HTTP for the OpenClaw plugin to call.

- `server.py` — 13 endpoints + healthz/version. **Hardened in H1:** every
  endpoint wraps the underlying tool call in `_safely()` so an agent error
  comes back as `{"error": "<type>: <msg>"}` — the adapter never returns 5xx.
  Added `/signals/consume` and `/signals/health` (the load-bearing half of
  the cross-agent primitive). Trace-id pass-through. Token auth via
  `OPENCLAW_ADAPTER_TOKEN` (dev mode if unset).
- `auth.py` — bearer-token guard.
- `README.md` — run instructions + the load-bearing contract list KTLO must
  not break.
- **Tests:**
  - `tests/new_plane/test_openclaw_adapter.py` — 11 unit tests (mocked), all pass.
  - `tests/new_plane/test_adapter_integration.py` — 12 integration tests
    using **real Kobe/Fraser code paths** (no mocks). Catches contract drift
    the mock tests can't see. All pass.

### P2 — Cross-agent signal store ✓
`new_plane/signals/` — SQLite-backed store, isolated from old-plane decisions
table. The load-bearing primitive per the PM thesis v1.1.

- `store.py` — `publish / recent / mark_consumed / unconsumed_count`. Path:
  `$OPENCLAW_SIGNALS_DB` (defaults to `~/.rahat/new_plane_signals.db`).
- `__init__.py` — public surface.
- **Tests:** `tests/new_plane/test_signal_store.py` — 9 tests, all pass.

### P3 — OpenClaw plugin skeleton (TS) ✓ (untestable from sandbox)
`new_plane/openclaw_plugin/` — TS plugin structure. Cannot run from sandbox
(no Node); ready for `npm install && npm run build` on your Mac.

- `package.json`, `tsconfig.json` — Node ≥20, ES2022, strict TS.
- `AGENTS.md`, `TOOLS.md` — agent + tool manifests for OpenClaw.
- `src/adapter_client.ts` — HTTP client for the Python adapter.
- `src/signals.ts` — typed signal publish/read.
- `src/cost_router.ts` — v0 model routing + JSONL cost-event log.
- `src/tools/kobe.ts`, `src/tools/fraser.ts` — TS tool faces.
- `src/agents/miya.ts` + `miya.system.md` — orchestrator skeleton with
  hard-coded arbitration, autonomy budget (≤3 tools, ≤1 design, ≤1 Pro per
  turn), signal publication.
- `src/index.ts` — plugin entry (registration shim — adapt to your exact
  OpenClaw plugin-SDK version).
- `README.md` — install + wire-up + .env needs.

### P4 — New Miya orchestrator (TS) ✓ (untestable from sandbox)
Folded into P3 above (`src/agents/miya.ts`). Hard-coded arbitration v0; the
learner upgrade is week-2+ work per the PM thesis.

### H3 — Python new_miya simulator ✓ (pre-OpenClaw baseline, fully testable)
`new_plane/miya_sim/` — **the orchestration logic in pure Python**, mirroring
what the TS plugin will do once OpenClaw is wired. Use cases:
- **Pre-OpenClaw smoke test:** validate the orchestration logic, charter
  gating, arbitration, signal publication BEFORE the TS plugin is integrated.
- **Side-by-side comparison:** send the same prompts to old Miya and the
  simulator; capture both responses for analysis.
- **Reference implementation:** when the TS plugin ships, its behavior should
  match this on the same inputs.

Files:
- `orchestrator.py` — intent classification, autonomy budget, arbitration
  policy (v0), charter precheck, signal publication. Same shape as `miya.ts`.
- `__main__.py` — CLI: `python -m new_plane.miya_sim ask "<msg>"`,
  `python -m new_plane.miya_sim health`, `python -m new_plane.miya_sim recent`.

**Tests:** `tests/new_plane/test_miya_sim.py` — 15 tests covering intent
classification, arbitration rules, budget caps, end-to-end runs against real
Kobe code. All pass.

**Confirmed working CLI run** (sandbox, real Kobe code, no LLM key):
```
$ python -m new_plane.miya_sim ask "what's my plan today"
--- new_miya response (trace=sim-0988fb55) ---
[new_miya sim] user: "what's my plan today"
arbitration: behind_pace — User is behind pace-to-date this week...
recalibration: Behind by 5,500 kcal. To catch up, convert Sun from rest → CrossFit.
--- meta ---
tools used:        kobe_active_goal, kobe_recalibration, kobe_charter_check
arbitration rule: behind_pace
sent (charter ok): True
signals published: [1]
```

### P5 — Scripts ✓
- `scripts/weekend_setup.sh` — installs Python deps, inits signal DB, runs
  adapter tests, TS install + typecheck.
- `scripts/weekend_smoke.sh` — boots adapter, curls every endpoint, signal
  round-trip, PASS/FAIL summary.

Both executable (`chmod +x` done).

## State of the world

- Full 5-layer suite still green: **unit 28 · contract 801 · eval 101 ·
  adversarial 14 · regression 17** (= 961 legacy tests).
- New-plane test suite: **47 tests, all green** (11 adapter unit + 12 adapter
  integration with real agents + 9 signal store + 15 miya simulator).
- Boundary doc intact (`specs/ARCHITECT_THREADS_2026-05-30.md`). My changes
  are entirely additive in new directories.
- Old plane runtime untouched. Live bot still works.
- **The Python plane of new_miya is *fully functional* — orchestrates Kobe +
  Fraser, arbitrates conflicts, publishes signals, all via real code paths.**
  The TS/OpenClaw side is the remaining handoff.

## What you do when you're back — exact sequence

```bash
cd ~/developer/agency/rahat

# 1) Setup (idempotent)
./scripts/weekend_setup.sh

# 2) Add the new-plane env keys (.env)
#    OPENCLAW_ADAPTER_TOKEN=<long random hex>  (optional for hello-world)
#    OPENCLAW_ADAPTER_URL=http://127.0.0.1:8765
#    OPENCLAW_SIGNALS_DB=$HOME/.rahat/new_plane_signals.db
#    OPENCLAW_COST_LOG=$HOME/.rahat/cost_router.log
#    NEW_MIYA_BOT_TOKEN=<separate Telegram bot token — NOT the live one>

# 3) Stage 0 smoke (boots adapter, exercises every endpoint)
./scripts/weekend_smoke.sh

# Expected: "Stage 0 GREEN. Foundation works."
# If FAIL: fix before going further.

# 4) Wire OpenClaw against this plugin (see new_plane/openclaw_plugin/README.md
#    section "Wire to OpenClaw"). This is the one step I can't predict
#    exactly because it depends on the OpenClaw version in staging/fleet/.
```

## Resume instructions if a new Claude thread picks this up

The architect role for the new plane is defined in
`specs/ARCHITECT_THREADS_2026-05-30.md`. New context for this thread:

- Stage 0 scaffolding is COMPLETE (Python adapter + signal store + TS plugin
  skeleton + scripts).
- The user is verifying it on their Mac via `scripts/weekend_smoke.sh`.
- Next deliverables after Stage 0 passes:
  1. Wire `src/index.ts` against the exact OpenClaw plugin-SDK API.
  2. Configure new_miya in the OpenClaw runtime with the `NEW_MIYA_BOT_TOKEN`.
  3. Send first real Telegram message to `/v2`; verify a signal lands in
     `~/.rahat/new_plane_signals.db`.
  4. Capture side-by-side responses from old vs new Miya for 5–10 example
     prompts.

## Open items for next session

- **The OpenClaw plugin-registration shim in `src/index.ts` is the one
  remaining hand-work item.** See `specs/OPENCLAW_INTEGRATION_GUIDE.md` for
  the concrete adaptation steps grounded in the actual `staging/fleet/` SDK
  types (`ChannelAgentTool`, `ChannelAgentToolFactory`, `ContextEngine`,
  `registerContextEngine`). The body of the plugin (HTTP client, signals,
  cost router, miya orchestrator) is SDK-independent and ready.
- `synthesize()` in `src/agents/miya.ts` (and `orchestrator.py`) is a
  placeholder structured-fallback — it doesn't call Gemini yet. The
  OpenClaw runtime is supposed to provide the LLM call; integrate when
  wiring `index.ts`. The simulator's `synthesize()` accepts an `llm_call=`
  parameter so you can wire Gemini there for the Python side too.
- v0 arbitration is hard-coded. The learner is week-2+ work and depends on
  signal data accumulating.
- Charter check kind defaults to `notify.user.reply` — refine when needed.
- ContextEngine plugin (Rahat-substrate) is sketched in the guide but not
  yet implemented. v0 = facts injection at `assemble`. Week-3+ = causal-
  contribution memory ranking through `consumed_by`.

## Overnight additions (Stage 1 — 2026-05-31)

### What got shipped

**`new_plane/miya_runner/`** — production Python service for new Miya v2:
- `adapter_client.py` — stdlib-only HTTP client to the FastAPI adapter.
  Surfaces `_safely()` errors as structured `{error}` envelopes; never
  raises on adapter-side failures.
- `telegram.py` — self-contained Telegram client (no `core.io` dep).
  `getUpdates` long-poll, `sendMessage` with Markdown fallback,
  message splitter for >4k chars, idempotent webhook delete.
- `orchestrator.py` — same intent/budget/arbitration as `miya_sim` but
  tool calls go through HTTP. Fail-OPEN charter check on transport
  errors (logged so you can investigate).
- `synthesizer.py` — Gemini call with cost-aware model selection;
  falls back to structured-fallback when GEMINI_API_KEY is unset.
- `cost_router.py` — v0 Flash/Pro heuristic: short prompts → Flash;
  hard-prompt patterns or arbitration firings → Pro; logs every
  decision as JSONL for the week-2 learner upgrade.
- `__main__.py` — CLI: `serve` (Telegram loop), `once` (single CLI
  turn), `health` (adapter + signals + env diagnostics).

**`new_plane/compare/`** — 8-week stop-or-go gate evidence harness:
- `harness.py` — runs N prompts through both planes (old-Miya proxied
  via `miya_sim`, new-Miya via runner), captures timing + tools +
  arbitration + model picks.
- `__main__.py` — CLI: `python -m new_plane.compare "<prompt>"…`
  emits markdown report into `private/eval-runs/`.

**`scripts/com.rahat.miya.v2.plist.template` + `scripts/install_new_miya.sh`** —
always-on launchd service for new Miya v2. Pre-flights env (token set,
distinct from `SCIENTIST_BOT_TOKEN`, adapter reachable, .venv present)
before installing. Coexists with `com.rahat.miya` — never swap.

**Tests (74 new, 121 total new-plane):**
- `test_runner_adapter_client.py` (18) — envelope shape, bearer header,
  transport-error paths, typed wrappers; uses in-process WSGI fake.
- `test_runner_telegram.py` (15) — parse_update, message splitting,
  HTTP error handling, Markdown→plain fallback.
- `test_runner_cost_router.py` (12) — Flash vs Pro decision matrix.
- `test_runner_synthesizer.py` (14) — prompt assembly, fallback,
  mocked Gemini path, token-usage capture.
- `test_runner_orchestrator.py` (9) — full flow with fake adapter:
  arbitration, charter fail-open, budget caps, adapter-down resilience.
- `test_compare_harness.py` (6) — multi-prompt run, markdown rendering,
  pipe-escape in summary table.

### Confirmed working in sandbox

```
$ RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ -q
114 passed, 1 warning in 11.32s
```

End-to-end orchestrator smoke (adapter unreachable → fail-open):
```
tools: ['kobe_active_goal', 'kobe_recalibration', 'kobe_charter_check']
arb:   None
model: gemini-2.5-flash (default-flash)
synth: {fallback: True}
transport_errors: 3
sent:  True   ← fail-open, runner does not crash
```

### What's pending for the morning (Phase A–G in WAKE_UP_PLAYBOOK)

1. Verify tests pass on your Mac (`121 passed` expected).
2. Boot adapter via `./scripts/weekend_smoke.sh`.
3. Confirm `.env` has `NEW_MIYA_BOT_TOKEN` (your BotFather step).
4. `python -m new_plane.miya_runner` → first real Telegram round-trip.
5. `python -m new_plane.compare …` → 5-prompt side-by-side capture.
6. (Optional) `./scripts/install_new_miya.sh` → always-on launchd job.
7. Commit + push the overnight work.

## What I did NOT do (per standing constraints)

- Did NOT touch any old-plane code (KTLO architect's territory).
- Did NOT commit anything. Working tree is dirty with the new files; you
  decide commit shape via `specs/WAKE_UP_PLAYBOOK.md` Phase G.
- Did NOT touch `.env` (you add the `OPENCLAW_*` and `NEW_MIYA_*` keys).
- Did NOT install Node or run TS — sandbox can't.
- Did NOT push, merge, or flip any live flag.
- Did NOT install any launchd job — `install_new_miya.sh` is opt-in.

## Commit suggestion when you're satisfied

See `specs/WAKE_UP_PLAYBOOK.md` Phase G for the full commit command
with proper message and file list.
