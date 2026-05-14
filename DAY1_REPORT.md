# Fraser Build — Day 1 Report (2026-05-14)

## Landed

- [P0] `agents/fraser/protocols.py` — ✅ 11 entity types, Workout Card dataclass tree (Movement / StrengthLift / WarmUpBlock / StrengthBlock / WODBlock / CoolDownBlock / ContextSnapshot / WorkoutNotes), InputMode + 7 supporting enums, 11 CHARTER_* kinds, 4 movement-category sets, lift+movement normalizers, 11 CharterRuleSpec rows.
- [P0] `agents/fraser/state.py` — ✅ thin wrappers over `core.memory.api` (zero new tables, ADR-003 compliant). 15 read helpers + 11 write helpers, every write through `core/charter.review()`.
- [P0] migration NNNN_fraser_init — **DROPPED** by architectural decision: per ADR-003 the substrate has zero schema work for new agents. Confirmed live with user before scaffolding.
- [P0] `agents/fraser/main.py` — ✅ thin entrypoint, importlib-loadable as `fraser` short-name, star re-exports from protocols + state + handler.
- [P0] `agents/fraser/agent.py` — ✅ `FraserAgent(core.agent.Agent)`, name='fraser', empty triggers (Day-1 safety), description set. Importable.
- [P0] tests — ✅ `tests/test_fraser_protocols.py` (28 tests, all green) + `tests/test_fraser_state.py` (11 tests, all green). Both pass under `RAHAT_TEST_MODE=1`.
- [P1] read tools — ✅ all 13 spec-§4 read tools that are state-reads wired in `state.py`. Plus 4 cross-agent stubs (`get_kobe_tier`, `get_huberman_state`, `get_family_load`, `get_travel_state`) backed by pref-overridable mocks so eval cases paint state without monkeypatching. Computational tools (`compute_target_weight`, `compute_predicted_burn`, `lookup_movement_cues`, `parse_user_workout`) intentionally deferred to a Day-2 `tools.py` — they're transform logic, not state reads, and don't belong in `state.py`.
- [P1] `agents/fraser/handler.py` skeleton — ✅ input-mode classifier (rule-based regex; Day-3 LLM-classifier upgrade path flagged in open questions), reasoner stub (`_reasoner_stub`) returning structurally-complete Workout Card with placeholder content, `route()` returns low-confidence Reply.
- [P1] eval cases fraser_001–010 — ✅ drafted in `tests/evals/test_fraser_conversation.py`, all marked `xfail(strict=False)` so they're discoverable but don't gate run_all. 3 xfailed today (assertions that need the reasoner output), 7 xpassed vacuously (stub returns empty movement lists, so "not in" assertions pass trivially). Day-3 reasoner work will turn the XPASSes into real coverage.
- [P2] write tools — ✅ all 11 wired via `_charter_gate()` helper. Every write builds a `WorkOrder`, calls `charter.review()`, writes one `governance_log` row, then proceeds only on approval. Charter policies themselves are NOT registered yet (Day-3 work) — today `charter.review()` returns `approved` because no Fraser-kind policies fire. The wiring is in place; the policies plug in cleanly.
- [P2] 5 more eval cases (fraser_011–015) — not attempted; trade off for tighter P0/P1 quality and the FRASER_OPEN_QUESTIONS write-up.
- [P2] ADR for input-mode router — drafted as item 2 of `specs/FRASER_OPEN_QUESTIONS.md`; full ADR file deferred to Day 2.

## Tests

- run_all: 5/5 layers green
  - unit: 28 passed
  - contract: **123 passed** (84 baseline + 39 Fraser; the +39 split: 28 protocols + 11 state)
  - eval: 43 passed, 1 skipped (Scientist baseline preserved; Fraser eval file is NOT in the eval layer paths yet — Day-6 hookup per spec §8)
  - adversarial: 14 passed
  - regression: 17 passed
- Failures (new only): zero
- Storage convention test (`tests/test_storage_convention.py`): PASSES with Fraser added. Confirms `agents/fraser/` has zero `INSERT INTO intents` / `UPDATE user_state` / `INSERT INTO week_preferences` patterns (the ADR-003 guardrail).
- Standalone eval file run: `pytest tests/evals/test_fraser_conversation.py` → 3 xfailed, 7 xpassed in 0.25s. No crashes.

## Open questions (see `specs/FRASER_OPEN_QUESTIONS.md`)

- 9 entries logged. Notable ones to resolve before Day 2/3:
  1. Behavioral transcript fetch failed (Google Doc auth-gated) — paste required before reasoner work.
  2. Input-mode classifier strategy — Day-3 decision flagged with my recommendation (hybrid: regex pre-filter + LLM fallback on ambiguity).
  3. Preference vs PRVN precedence — proposing preference wins with NOTES callout; primary-lift edge case needs your call.
  4. 1RM staleness thresholds — shipping at 90 / 180 days per spec; flagged that 120/240 may be steadier for non-strength-cycle users.
  5. Race tiebreaker — recommending timestamp-ordering (Day-4 wiring); CRDTs feel like overkill for disjoint write surfaces.

## Surprises

- The brief contradicted current architecture in two places — both surfaced via the AskUserQuestion check before any code was written:
  - "mirror Kobe" was wrong post-ADR-003 — Kobe is grandfathered, Fraser is the first net-new post-ADR-003 agent.
  - The migration-file P0 was schema-orthogonal — the substrate's `memory_entities` already has every column Fraser needs.
- The 7-xpass result on the eval cases was unexpected. Cases like "no overhead movements when neck injury registered" pass vacuously because the stub reasoner emits zero movements. This is technically correct (an empty card violates no constraint), and it inverts cleanly when the Day-3 reasoner adds real movements. Logged as a Day-3 verification step rather than a Day-1 fix.
- The sandbox running this build hit `.git/index.lock` permission errors and could NOT commit. The branch `feat/fraser-day1-scaffold` is current and the files are on disk; commits need to come from your shell. See item 9 in `specs/FRASER_OPEN_QUESTIONS.md`.

## Files touched

```
agents/fraser/__init__.py        14 LOC
agents/fraser/protocols.py      888 LOC
agents/fraser/state.py          702 LOC
agents/fraser/handler.py        286 LOC
agents/fraser/main.py            50 LOC
agents/fraser/agent.py          117 LOC
tests/test_fraser_protocols.py  350 LOC
tests/test_fraser_state.py      333 LOC
tests/evals/test_fraser_conversation.py  278 LOC
specs/FRASER_BEHAVIORAL_TRANSCRIPT.md     44 LOC (placeholder)
specs/FRASER_OPEN_QUESTIONS.md          159 LOC
core/miya_main.py                 modified (commented-out FraserAgent registration)
tests/run_all.py                  modified (added Fraser to contract layer)
```

Total: ~3,200 LOC across 11 new files + 2 modified.

## Next-5-day plan refinement

- **Day 2** — Build `agents/fraser/tools.py` with the 4 computational reads (`compute_target_weight`, `compute_predicted_burn`, `lookup_movement_cues`, `parse_user_workout`) and the seed warm-up / cool-down templates from the Gemini transcript (Hunch reset, neck-guard cues, HBP cue library). Also: a `dislikes`-style integration test for the substitution-rule lookup path.
- **Day 3** — Wire the Gemini 2.5 Flash reasoner. Replace `handler._reasoner_stub` with the real call. Build the system prompt from the transcript (once you paste it in) + `protocols.FRASER_CHARTER_RULE_SPECS` + the input-mode router instructions. Register the actual Fraser Charter policies in `core/charter.py` (quiet-hour gate for `fraser.workout.commit`, HRV-red gate for the same, Huberman-green gate for `fraser.1rm.update` increases, last-session-completed gate for `fraser.prvn.advance` + `fraser.progression.advance`). After this, the 7 vacuous XPASSes flip into real assertions; remove the xfail marks one-by-one as the cases stabilize.
- **Day 4** — Real Kobe + Huberman state reads. Design the cross-agent read contract with you — Kobe doesn't currently expose `get_tier()` to other agents; either add a thin read API on `KobeAgent` (preferred) or use `core.memory.cross_agent_list` keyed on Kobe's tier-change entity type.
- **Day 5** — Input-mode router fully wired (regex + LLM-classifier fallback per the open-question recommendation). 1RM upload paths: conversational (Path A — Miya walks through lifts), bulk CSV (Path B), freeform one-shot (Path C). Each path uses `ingest_1rm_batch` under the hood.
- **Day 6** — Drop xfail marks on the 10 already-drafted cases; draft + land cases fraser_011 through fraser_040. Add `tests/evals/test_fraser_conversation.py` to run_all.py's eval-layer paths. Spec §13 acceptance bar is ≥90% green for one full nightly cycle before merge.

## Decision needed from you before Day 2 starts

1. **Behavioral transcript paste.** Day 3 is blocked without it. Could be 5-min copy/paste from the Doc.
2. **Tools.py vs state.py boundary.** I propose computational reads (`compute_*`, `lookup_movement_cues`, `parse_user_workout`) live in `agents/fraser/tools.py` — a sibling of state.py. Want your blessing before I add a fifth file to the four-file pattern. Alternative: fold them into handler.py.
3. **Kobe read-API contract.** Day-4 work needs to know whether Kobe gets a public `get_tier()` method on `KobeAgent` or whether Fraser goes through `core.memory.cross_agent_list`. The first is cleaner; the second is more substrate-symmetric. Your call.
