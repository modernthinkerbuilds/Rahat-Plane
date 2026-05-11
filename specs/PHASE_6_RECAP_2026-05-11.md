# Rahat refactor — engagement recap (2026-05-08 → 2026-05-11)

**Source plan:** [specs/ARCH_REVIEW_2026-05-08.md](./ARCH_REVIEW_2026-05-08.md)
**R1 execution plan:** [specs/PHASE_4D_R1_PLAN.md](./PHASE_4D_R1_PLAN.md)
**Engagement scope:** `b2531ea` → `c6ecc07` (29 commits, 4 days)

---

## What started the engagement

Three architectural pivots had landed within a 20-minute window on 2026-05-08 — three-plane control plane → tiered memory → model-first reasoner — and the additive landings (zero deletions across 22 commits) left the codebase carrying the predecessors of every pivot. The user asked for an L8-grade review, a kill-list, and a cleanup that would also leave the repo trivially clonable by anyone.

## What shipped

| Phase | Commits | Outcome |
|---|---|---|
| **3 — Cleanup** | `1ec4900` | ~1,550 LOC of dead/run-once files removed (16 deletions), `core/__init__.py` docstring rewritten, `.gitignore` extended |
| **4a — R3 (memory pkg)** | `4fdb42c` | `core/memory.py` + `core/archival.py` → `core/memory/` package |
| **4b — R4 (io.py cleanup)** | `ffd7ecd` | Vestigial lazy import + stale `anthropic_io` refs removed |
| **4c — R2 (eval triage)** | `14f3632` | 8 evals moved from `agents/the_scientist/` → `tests/scientist/`; redundant `eval_via_agent.py` deleted (5,228 LOC relocated) |
| **4d-pre — Setup ergonomics** | `ab30feb` | `bootstrap.sh`, `.env.example`, templated launchd plists, `vitals_listener` made portable; **"Frictionless Setup" principle** added to `specs/ARCHITECTURE.md §3` |
| **4d — R1 main.py split** | `11317c9` (Step 0a), `ca0e758` (1a), `bb8231c` (1b), `ca4c7b1` (2a), `54a63d5` (2b) | `main.py` **2,930 LOC → 206 LOC (93% reduction)**; `state.py` and `handler.py` created |
| **5 — Doc refresh** | `c6ecc07` | `specs/ARCHITECTURE.md`, `README.md`, `specs/PHASE_4D_R1_PLAN.md` updated to match reality |
| **Misc test-infra hardening** | `6dd6144`, `c457b4c` | pytest pinned as dev dep; nightly.sh self-heal for missing pytest |

## The headline metric

`agents/the_scientist/main.py` shrank from **2,930 LOC → 206 LOC** — a **93% reduction** — and the work split across four files with clean responsibilities:

| File | LOC | Responsibility |
|---|---|---|
| `protocols.py` | 323 | Pure math + constants (no I/O) |
| `state.py` | 997 | DB I/O + planning + recalibration |
| `handler.py` | 2,042 | Intent dispatch + router + nudges + loop |
| `main.py` | 206 | Path bootstrap + config + thin entry point |

The legacy `sci.<name>` import contract (used by `ScientistAgent`'s importlib loader and 10+ eval files) was preserved end-to-end via two star re-exports in `main.py`. **Every test that worked at `b2531ea` still works at HEAD, byte-identical.**

## Test stack final state

```
unit:        28 passed
contract:    40 passed, 34 warnings
eval:        43 passed, 1 skipped
adversarial: 14 passed, 6 warnings
regression:  17 passed, 4 warnings
─────────────────────────────────────
total:       142 passed, 1 skipped (5 layers green)
```

## Architectural principles added or reinforced

- **Frictionless Setup** — anyone can clone and reach a green hermetic test suite without editing a single tracked file. Concretely enforced by `bootstrap.sh`, `.env.example`, templated `*.plist.template` files, and the kill-list rule that no tracked file may contain a hardcoded `/Users/<name>/...` path.
- **DB path centralization** — every connection to `vault/rahat.db` flows through `core.io.DB_PATH`. `RAHAT_TEST_MODE=1` redirects to a per-process tempfile so tests can never corrupt the live DB (the 2026-05-08 incident class).

## Vault integrity

**Zero tracked-file modifications to `vault/` across all 29 commits.** The 2026-05-08 live-DB safety contract held end-to-end.

## Follow-on candidates (deferred, not blocking)

1. **Drop main.py's `parse_gym_plan` / `eligible_cf_days` duplicates** — currently mirrored in `handler.py` to avoid a circular import at module-load time. A future commit can remove the main.py copies once nothing imports them directly. Small cleanup, low risk.
2. **Auto-job hardening** — the `evolve.sh` / `greenstreak.sh` / `hygiene.sh` / `regression.sh` jobs auto-mutated tracked files during this engagement and caused multiple `.git/index.lock` race conditions. A future commit should gate these jobs on "no manual commits in the last N minutes" or run them in a worktree.
3. **Pure-math extraction from state.py** — `replan_week`, `compute_week_recalibration`, etc. blend state reads with compute. A future pass could split each into `compute_X(state_dict)` (pure) + `X()` (state-wrapping), enabling property-based testing of the math.
4. **`tests/last_run_*.{md,json,log}` should be gitignored** — auto-generated test artifacts that churn on every run.
5. **`handler.py` is still ~2K LOC** — coherent (all dispatch), but a natural future pass could split by domain (`handler/burn.py`, `handler/plan.py`, `handler/voice.py`). Not urgent at N≤6 agents.

---

Generated 2026-05-11 as part of Phase 6 final verification.
