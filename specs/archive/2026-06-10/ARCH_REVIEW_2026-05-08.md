# Rahat — Architectural Review & Cleanup Plan

**Date:** 2026-05-08
**Reviewer:** L8 Agent Architect lens (working with the project owner)
**Scope:** `core/` + `agents/` + supporting infra (`scripts/`, `profile/`, root)
**Mode:** Decisive cleanup, single-commit deletes, small-commit refactors on `main`
**Test gate:** `python -m tests.run_all` with `RAHAT_TEST_MODE=1` (live-DB safety per 2026-05-08 incident)

---

## Section A — Forensics (Phase 1)

### A.1 What actually shipped

22 commits between 2026-05-03 (initial) and 2026-05-08 (latest). Three architectural pivots landed in **a single 20-minute window on 2026-05-08**, which is the proximate cause of the parsimony rot:

| Window | Commit | Pivot |
|---|---|---|
| 2026-05-05 22:57 | `cada452` | **Three-plane control plane.** Adds 9 new `core/` files (`agent`, `charter`, `decisions`, `episodes`, `eval`, `io`, `miya`, `miya_main`, `voice`). Defines the substrate. |
| 2026-05-08 19:31 | `faf6440` | **Tiered memory layer.** Adds `core/memory.py` (616 LOC), `core/archival.py` (254 LOC), `agents/the_scientist/memory.py` (332 LOC). |
| 2026-05-08 19:34 | `776f1ce` | **Model-first pivot.** Adds `core/anthropic_io.py`, `core/gemini_reasoner_io.py`, `core/cost.py`; `agents/the_scientist/{reasoner,tools,coach_system}.py`; `agents/bajrangi/`; 5 new eval files. |
| 2026-05-08 19:51 | `b2531ea` | README rewrite to reflect the new shape. |

### A.2 The smoking gun

**Zero `git mv` operations and zero file deletions across all 22 commits.** Every pivot landed strictly additively. New layers were stacked on top of the previous shape rather than replacing it. This is the mechanical explanation for the "I lost track" feeling — there is no narrative of what was *retired*, only what was *added*.

### A.3 Current state map — `core/` (3,227 LOC, 15 files)

| File | LOC | Inbound refs | Status |
|---|---|---|---|
| `agent.py` | 111 | miya, eval, tests, scientist agent | **load-bearing** — base contract |
| `anthropic_io.py` | 24 | none (raises ImportError) | **tombstone** — intentional dead-stub |
| `archival.py` | 254 | scripts/memory_consolidate, evals | active — cold-tier writer |
| `charter.py` | 226 | miya, tests, evals | **load-bearing** — policy plane |
| `cost.py` | 125 | io, gemini_reasoner_io, llm_cost_report | active — cost ledger |
| `decisions.py` | 197 | miya, reasoner, tests, evals | **load-bearing** — trace log |
| `episodes.py` | 226 | **none** (only mentioned in `__init__.py` docstring) | **DEAD** — superseded by `memory.py` |
| `eval.py` | 244 | self-referential only | suspect — needs verification (see B.2) |
| `gemini_reasoner_io.py` | 309 | reasoner, evals | **load-bearing** — primary LLM adapter |
| `io.py` | 242 | charter, miya, memory, decisions, archival, episodes(dead), eval, tests | **load-bearing** — single I/O chokepoint |
| `memory.py` | 616 | miya, scientist memory, bajrangi memory, evals | **load-bearing** — tiered store |
| `miya.py` | 391 | miya_main, tests, evals | **load-bearing** — orchestrator |
| `miya_main.py` | 34 | launchd entry only | active — process entry |
| `voice.py` | 214 | miya, reasoner, tests, evals | **load-bearing** — voice layer |
| `__init__.py` | 14 | docstring lists `episodes` (now dead) | needs minor edit |

### A.4 Current state map — `agents/the_scientist/` (11,972 LOC, 16 files)

| File | LOC | Notes |
|---|---|---|
| `main.py` | 2,923 | **Legacy god-file.** Loaded as `sys.modules['sci']` by `agent.py` via `importlib`. Still load-bearing. |
| `tools.py` | 1,879 | Model-first tool surface. Imports `agents.the_scientist.agent` to ensure main.py loads as `sci`. |
| `eval_extended.py` | 1,185 | 3rd of 8 eval files. |
| `eval_gemini_parity.py` | 892 | Pivot validator. Imports `coach_system`. |
| `eval_reasoner_robust.py` | 702 | Pivot validator. |
| `coach_system.py` | 640 | Model-first system prompt + scaffolding. |
| `eval_memory.py` | 567 | Tiered-memory validator. |
| `eval_reasoner.py` | 529 | Pivot validator. |
| `eval_gemini_pdf_usecases.py` | 529 | Multimodal smoke. |
| `eval_reasoner_live.py` | 388 | Live LLM (slow, expensive). |
| `eval_suite.py` | 376 | Original 142-case suite. |
| `reasoner.py` | 375 | Model-first reasoner core. Wraps gemini_reasoner_io. |
| `memory.py` | 332 | Scientist-specific memory adapters. |
| `protocols.py` | 323 | Pure math + constants extracted from main.py. |
| `agent.py` | 148 | `ScientistAgent` wrapper that loads main.py as 'sci'. |
| `eval_via_agent.py` | 60 | Tiny shim — runs eval via the new Agent contract. |

**Eval files total: 5,228 LOC across 8 files.** Three are pivot-validators (parity, reasoner, reasoner_robust); none of them are run by `tests/run_all.py` based on the grep — they exist as standalone scripts.

### A.5 Other directories

`scripts/` — 12 entries, but most are run-once historical:

- `commit-may-6-and-7.sh`, `finish-commit-3.sh`, `fix-commit-dates-utc.sh`, `push-architecture-work.sh` — git-history orchestration, **already executed**.
- `cutover-model-first.sh`, `install-sugarwod-bridge.sh`, `upgrade-python-312.sh` — one-shot ops scripts, **already executed**.
- `scientist.sh` — launchd ergonomics, still useful.
- `memory_consolidate.py`, `llm_cost_report.py` — recurring jobs, keep.

`profile/` — 4 push/recover shell scripts, **all run-once** for the GitHub profile repo. README artifacts (`README-PROFILE.md`, `README-RAHAT-REPO.md`, `SETUP.md`) are the durable part.

Root: `.env.bak.1778203419` (stale backup), `pytest-cache-files-srzvty7b/` (escaped pytest cache), three plists (one stale — see kill-list).

---

## Section B — Architectural review (Phase 2)

### B.1 The intent — three-plane decomposition

Per `cada452` ("three-plane control plane") the design intent is:

```
┌─ Orchestrator (miya.py) ──────── single inbox, regex+Flash router, fans out to agents
├─ Charter (charter.py) ─────────── policy chokepoint; approve/modify/veto with governance log
├─ Voice (voice.py) ──────────────── deterministic Hyderabadi/Dakhini dressing layer
│
├─ Substrate
│   ├─ agent.py ─── base contract (name, triggers, route, tick)
│   ├─ io.py ──── tool helpers (single chokepoint for Telegram, Gemini, DB)
│   ├─ decisions.py ── append-only trace log
│   └─ memory.py / archival.py ── tiered store
│
└─ Adapters
    ├─ gemini_reasoner_io.py ── primary LLM
    └─ cost.py ── per-call ledger
```

This is a clean L8-shape. The problem is not the design — it's that the substrate accumulated three distinct memory implementations and the agent layer accumulated three distinct LLM-execution paths without retiring the predecessors.

### B.2 Parsimony violations

**V1 — Memory layer fragmentation.** Three substrate-level files (`memory.py`, `archival.py`, `episodes.py`) plus two agent adapters (`scientist/memory.py`, `bajrangi/memory.py`). `episodes.py` is dead (zero callers); `archival.py` is conceptually a tier of `memory.py` and could plausibly be a submodule rather than a peer. After dead-code removal, the question is whether `archival.py` lives or gets folded into `memory.py`.

**V2 — Tombstone management.** `core/anthropic_io.py` is an intentional 24-LOC tombstone with a heartfelt comment. The rationale (don't silently un-pivot) is sound, but the tombstone has now done its job — the codebase has no Anthropic imports left, and the strategic context is preserved in `specs/MODEL-FIRST-PIVOT.md` and git history. The tombstone now adds noise to `core/` for diminishing returns.

**V3 — `main.py` god-file.** 2,923 LOC. The new `agent.py` wrapper explicitly defers the split: *"A deeper split into protocols.py + handler.py is intentionally deferred to a follow-up commit."* This is a **known-deferred refactor**, not an oversight. It is the largest single parsimony cost in the repo. Worth doing — but the safest order is *delete dead code first*, then *split main.py with the eval suite as the contract*, never the other way around.

**V4 — Eval-suite proliferation.** 8 eval files in `agents/the_scientist/`, total 5,228 LOC, none invoked by `tests/run_all.py`. Three are pivot-validators that have already served their purpose (parity baseline confirmed, robustness confirmed). The new test stack at `tests/` is the correct home for ongoing regression. The agent-level evals are either (a) candidates for migration into `tests/`, (b) candidates for archival into `tests/legacy/` for one release, or (c) candidates for deletion if redundant with `tests/`. Recommend migration triage rather than wholesale delete.

**V5 — Two launchd plists for one process.** `core/com.rahat.miya.plist` is the new entry. `agents/the_scientist/com.rahat.scientist.plist` is the predecessor. Per `miya_main.py` docstring: *"Replaces `agents/the_scientist/main.py` as the single user-facing process."* The scientist.plist is stale.

**V6 — Run-once scripts in `scripts/` and `profile/`.** Eight scripts have already executed and serve no further purpose. Keeping them in `scripts/` makes it harder to discover the live operational scripts (`scientist.sh`, `memory_consolidate.py`, `llm_cost_report.py`).

### B.3 Kill-list (proposed deletions, awaiting approval)

| # | Path | LOC | Evidence | Risk | Recommendation |
|---|---|---|---|---|---|
| K1 | `core/episodes.py` | 226 | Zero inbound imports. Superseded by `memory.py`. | None | **Delete** |
| K2 | `core/anthropic_io.py` | 24 | Intentional tombstone, has served its purpose. Strategic rationale preserved in `specs/MODEL-FIRST-PIVOT.md`. | Trivial — restore from git if ever needed | **Delete** (your call — keeping is defensible) |
| K3 | `agents/the_scientist/com.rahat.scientist.plist` | 53 | Replaced by `core/com.rahat.miya.plist`. | Need to confirm launchd is actually loading miya.plist | **Delete after launchd confirmation** |
| K4 | `scripts/commit-may-6-and-7.sh` | 162 | Run-once git history orchestration, already executed. | None | **Delete** |
| K5 | `scripts/finish-commit-3.sh` | 54 | Same. | None | **Delete** |
| K6 | `scripts/fix-commit-dates-utc.sh` | 117 | Same. | None | **Delete** |
| K7 | `scripts/push-architecture-work.sh` | 158 | Run-once push orchestration. | None | **Delete** |
| K8 | `scripts/cutover-model-first.sh` | 160 | Cutover already executed. Runbook preserved in `specs/RUNBOOK-model-first-cutover.md`. | None | **Delete** |
| K9 | `scripts/install-sugarwod-bridge.sh` | 100 | Bridge already installed and committed. | None | **Delete or move to docs** |
| K10 | `scripts/upgrade-python-312.sh` | 214 | Upgrade already done. | None | **Delete** |
| K11 | `profile/deploy-all.sh` | 26 | Run-once profile-repo push. | None | **Delete** |
| K12 | `profile/push-profile-readme.sh` | 80 | Same. | None | **Delete** |
| K13 | `profile/push-rahat-readme.sh` | 78 | Same. | None | **Delete** |
| K14 | `profile/recover-and-push-rahat.sh` | 98 | Same — and dangerous to leave around (recovery flow). | None | **Delete** |
| K15 | `.env.bak.1778203419` | — | Stale env backup; gitignored anyway. | None | **Delete** |
| K16 | `pytest-cache-files-srzvty7b/` | — | Escaped pytest cache. | None | **Delete + add to .gitignore** |
| K17 | `core/__init__.py` (edit, not delete) | 14 | References `episodes` in docstring | None | **Edit** to remove episodes reference |

**Total LOC removed if all approved: ~1,550 LOC + multiple files of historical noise.**

### B.4 Refactor-list (proposed, awaiting approval, AFTER kill-list)

| # | Change | Rationale | Blast radius | Effort |
|---|---|---|---|---|
| R1 | Split `agents/the_scientist/main.py` (2,923 LOC) into `handler.py` (intent dispatch) + extend `protocols.py` (math). Keep `agent.py` wrapper; eval_suite must pass byte-identical. | The largest parsimony cost in the repo. Already explicitly deferred in the codebase. | Touches ScientistAgent + all 8 eval files via `sci` module name. Test stack must stay green. | Large — 1 day |
| R2 | Migrate eval files from `agents/the_scientist/eval_*.py` into `tests/`: triage each into `tests/evals/` (live regression) or `tests/legacy/` (archive) or delete (redundant). | 5,228 LOC of evals not invoked by the test runner is a maintenance trap. | Self-contained — none of the evals are runtime dependencies. | Medium — half day |
| R3 | Fold `core/archival.py` into `core/memory.py` as a submodule, OR formalize `core/memory/` as a package with `__init__.py`, `tiers.py`, `archival.py`, `consolidation.py`. | Three-file memory layer feels over-fragmented; a package signals tier ownership cleanly. | `core/memory.py` is imported in 8+ places — names must stay stable. | Small — 2 hours |
| R4 | Collapse `core/io.py` + `core/cost.py` boundary review: `cost.py` is imported by `io.py` via lazy import to dodge a cycle. That cycle is a smell. | Lazy imports to break cycles are a clue the seam is wrong. | Surgical. | Small — 1 hour |
| R5 | Verify `core/eval.py` is actually used. Grep showed only self-references. If it's the harness for `agents/.../eval_*.py`, R2 may render it unnecessary. | Possibly orphaned. Defer until after R2. | None until R2 done. | TBD after R2 |

### B.5 Recommended order (hygiene over heroics)

1. **Confirm launchd loads `core/com.rahat.miya.plist` and not the scientist plist.** This is the only deletion that has a real-world side effect. If you confirm via `launchctl list | grep rahat` showing `com.rahat.miya`, K3 is safe.
2. **Phase 3 — single-commit deletion** of K1, K2, K4–K16, plus the `__init__.py` edit (K17). One commit, easy revert. Run `python -m tests.run_all` with `RAHAT_TEST_MODE=1` before and after.
3. **Phase 4a — R3** (memory package) — small, contained, sets up the seam clearly.
4. **Phase 4b — R4** (cost/io seam) — small.
5. **Phase 4c — R2** (eval triage) — medium, decisions per file.
6. **Phase 4d — R1** (`main.py` split) — biggest, last, with the eval suite as the contract.
7. **Phase 5 — Doc refresh**: `README.md` and `specs/ARCHITECTURE.md` updated to reflect what actually exists.
8. **Final verification** — full 5-layer test stack + `vault/` byte-untouched check.

### B.6 What I'm explicitly NOT recommending

- **Do not** split `main.py` before deleting dead code. You'll move corpses.
- **Do not** rewrite the orchestrator. The three-plane decomposition is correct; the issue is fragmentation around it, not the shape itself.
- **Do not** touch `vault/` — per the 2026-05-08 live-DB corruption incident, `RAHAT_TEST_MODE=1` is mandatory; cleanup work has zero reason to read or write the production database.
- **Do not** delete the `tests/` stack or any of the new test infrastructure under it. That's the safety net for everything that follows.

---

## Decisions needed from you before Phase 3

1. **Approve K1, K4–K16 as a batch?** (The unambiguous run-once and dead-code items.)
2. **K2 — delete the `anthropic_io.py` tombstone or keep it?** (Defensible either way; my recommendation: delete.)
3. **K3 — should I run `launchctl list | grep rahat` first, or do you want to verify the plist switch yourself?**
4. **R1 (main.py split) — green-light for Phase 4d, or hold for a separate review?** (It's the biggest item; understandable to want a separate gate.)
