# Build Brief — Fraser Agent (Day 1 Scaffold)

**For:** Architect agent
**Owner:** Modern Builder (available for live questions during the session)
**Date issued:** 2026-05-14 · **Rev:** v2 (post-ADR-003)

> **READ FIRST:** ADR-003 (commit `6c58a0b`, 2026-05-14) makes the substrate canonical for all new agents. No new tables, no `db/migrations/`. The pattern is `core/memory/api.py` + `type="fraser_<thing>"` + `agent="fraser"`. The canonical reference implementation is `agents/the_scientist/dislikes.py` (commit `e5397ef`). Read both before writing any Fraser code. Kobe's per-agent tables are legacy and explicitly grandfathered — do NOT copy that layout.

---

## 1. Mission

Build the scaffolding and core read path of the Fraser agent per the v2 requirements spec. The full build is 6 days; **this session is Day 1**. Land the spine cleanly, then stop and hand off for review.

**What "spine" means:** four-file structure, substrate-backed state, read tools wired against own-state (Kobe/Huberman mocked), input-mode router stubbed, first 10 eval cases runnable, and a session-end report.

I'm around for live questions on architectural decisions — ask in the thread rather than guessing. For style/convention questions, follow ADR-003 and the `dislikes.py` pattern.

---

## 2. Source Material (read in this order)

1. **ADR-003 — substrate canonical:** `specs/ADR-003-multi-agent-storage-convention.md` (commit `6c58a0b`). The architectural ground truth. New agents store via `core/memory/api.py`; no per-agent tables.
2. **Canonical reference implementation:** `agents/the_scientist/dislikes.py` (commit `e5397ef`). End-to-end working example — read it before writing Fraser code.
3. **Memory API:** `core/memory/api.py` — the 8-function wrapper (`goal_create`, `pref_set`, `event`, `entity_get`, `event_query`, etc.). Every Fraser persistence call goes through this. The Fraser dataclasses in `protocols.py` should match what these functions accept and return.
4. **Storage convention guardrail:** `tests/test_storage_convention.py` — fails any PR that adds tables outside the substrate. Run it early.
5. **Canonical spec (Google Doc):** https://docs.google.com/document/d/1d_D8q_VbBFxea_RI9IR-5c306sAZ3uWrKZ-bKpdH7LI/edit
6. **Repo spec:** `specs/FRASER_REQUIREMENTS.md` — same content + Appendix A (25-use-case catalog). If repo and Doc conflict, **repo wins**. Note: the spec's "11 entity types" are 11 `type` values inside `memory_entities`, not 11 tables.
7. **Behavioral transcript:** https://docs.google.com/document/d/1J5Ty8Y1_UoI3byzSDmxkSoLZe0POGDJ77A1jQ8HpCyI/edit — system-prompt seed for the reasoner. Save a copy to `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` as part of Day 1.
8. **Pattern reference (file layout only, NOT storage):** `specs/PHASE_4D_R1_PLAN.md` — Scientist→Kobe four-file split. Mirror the *file structure* (protocols / state / handler / main). Do NOT mirror Kobe's storage layer; that's the legacy pattern.
9. **Memory contract:** `specs/MEMORY-AND-STATE-ARCHITECTURE.md`
10. **Charter:** `core/charter.py` — every write goes through here, no exceptions.

---

## 3. Hard Repo Conventions (DO NOT VIOLATE)

- **ADR-003 substrate is mandatory.** All Fraser persistence goes through `core/memory/api.py` with `agent="fraser"` and `type="fraser_<thing>"`. **No new tables. No `db/migrations/` directory.** `tests/test_storage_convention.py` will fail any PR that violates this.
- `RAHAT_TEST_MODE=1` for every test run. We had a live-DB corruption incident on 2026-05-08 and this env var redirects writes. No exceptions.
- Test runner: `python -m tests.run_all`. Five layers (unit / contract / eval / adversarial / regression). New Fraser tests go under `tests/agents/fraser/`.
- All work goes on feature branch `feat/fraser-day1-scaffold`. **Do NOT auto-commit to main**, even if nightly green pipeline triggers. I'm overriding the standing aggressive-auto-merge policy for new-agent builds — I want to eyeball the shape before it lands.
- Mirror the file-layout pattern (only): `main.py` star re-exports from `state.py` and `handler.py` so `fraser.<name>` works the same way `sci.<name>` does. ScientistAgent loads `main.py` via importlib — Fraser must too. **Do not mirror Kobe's storage layer** — that's grandfathered legacy.
- New dependencies require an ADR. No silent additions to `pyproject.toml`.

---

## 4. Deliverables (priority order)

### P0 — must land before you stop

1. `agents/fraser/protocols.py` — dataclasses for: Workout Card (§2.5), the 11 entity-type body shapes (`FraserWorkoutBody`, `FraserInjuryBody`, etc.), charter-rule schemas, input-mode enum. **No table definitions** — these are body-shape contracts for substrate JSON payloads.
2. `agents/fraser/state.py` — **thin wrappers** around `core/memory/api.py`. Each function is 2–5 lines: validate input, call the appropriate substrate function with `agent="fraser"` and the right `type`, return parsed dataclass. Pattern reference: `agents/the_scientist/dislikes.py` (commit `e5397ef`).
3. `agents/fraser/main.py` — thin entrypoint, Miya capability registration, star re-exports.
4. Tests for protocol round-tripping (dataclass → JSON body → substrate write → substrate read → dataclass) and substrate-convention compliance (`test_storage_convention.py` stays green). Must pass under `RAHAT_TEST_MODE=1`.

### P1 — should land

5. The 18 read tools from §4. Start with own-state (`get_recent_workouts`, `get_active_injuries`, `get_1rms`, `get_preferences`, `get_route`, `get_prvn_position`, `get_chest_progression`, `get_equipment_available`). Each is a substrate call filtered by `agent="fraser"` and the appropriate `type`. Cross-agent reads (`get_kobe_tier`, `get_huberman_state`) use mocked fixtures — real wiring is Day 4, not today.
6. `agents/fraser/handler.py` skeleton: input-mode router (default / user-supplied / user-requested), reasoner-loop scaffold. **No real LLM calls yet** — stub the reasoner as a fixture-returning function so the control flow is testable.
7. First 10 eval cases (`fraser_001` through `fraser_010` from §9 of the spec) drafted as runnable test files in `tests/agents/fraser/eval/`. They will fail today because the reasoner is stubbed — that's fine. Just have them structured.

### P2 — bonus if time

8. The 11 write tools stubbed with Charter.check() calls. Substrate writes via `entity_create` / `event` / `pref_set`; Charter check before each call. Log rationale to governance log per Charter contract.
9. 5 more eval cases (`fraser_011` through `fraser_015` — the input-mode and preference cases).
10. ADR for the input-mode router at `specs/ADR-NNN-fraser-input-modes.md`.

### DO NOT attempt in Day 1

- Real Kobe state reads. Kobe doesn't currently expose `get_tier()` to other agents; that's a contract change we design together.
- 1RM upload paths (§11). Three paths with design choices that need my review.
- Workout Card render in Miya. Do not touch `core/voice.py` without me.
- Real Gemini 2.5 Flash reasoner integration. Stub the LLM call. Day 3 wires the real model.
- Any rebranding of Scientist→Kobe or Bajrangi→Huberman — that's a separate workstream and the rebrand is already in progress.

---

## 5. Open Questions — Ask Live or Log

I'm in the thread today. When you hit any of these, **ask me directly** if it blocks progress. If it doesn't block, write to `specs/FRASER_OPEN_QUESTIONS.md` and keep moving — we'll batch-resolve.

- **Race tiebreaker:** Kobe↔Fraser conflicts on substrate writes — timestamp wins (§10) vs. last-writer-wins vs. CRDT? Want your read on it.
- **1RM staleness threshold:** >90d warn, >180d block PR-attempts. Reasonable, or too aggressive?
- **Preference vs. PRVN:** if PRVN prescribes a movement on `type="fraser_preference"` (dislike), does preference win or does PRVN win?
- **Route versioning:** single entity per route name with mutations via `entity_update`, or new entity per correction with `supersedes` link? E.g., "10k loop" → corrected to 7.5–8k.
- **Input-mode classification:** rule-based regex, embedding-based, or LLM-classifier? Day 3 decision but flag your preference now.

**Resolved already (do not re-ask):**
- Storage layout → ADR-003 substrate; `type="fraser_<thing>"` with `agent="fraser"`.
- Workout Card persistence → JSON in `memory_entities.body`; no separate table.
- Migrations → none; substrate has no per-agent schema.
- 1RM upload → loop of `entity_create` calls with `type="fraser_1rm"`; bulk is just iteration.

---

## 6. Verification Before You Stop

Run, in order:

1. `RAHAT_TEST_MODE=1 python -m tests.run_all 2>&1 | tee session_test.log`
2. `RAHAT_TEST_MODE=1 python -m pytest tests/test_storage_convention.py -v` — must stay green; substrate-convention guardrail.
3. `git status` — should show only feature branch + new files, no main mutations.
4. `git log --oneline feat/fraser-day1-scaffold` — clean commit history, one logical change per commit.
5. Confirm no new files under `db/migrations/` (there should be no such directory; if you created one, delete it).
6. Write `DAY1_REPORT.md` at repo root (see §7 below).

If `python -m tests.run_all` or `test_storage_convention.py` fails on anything that wasn't already failing on main: STOP and write what broke into DAY1_REPORT. Do not paper over.

---

## 7. Session-End Report Format

Bullet points only. No prose flourish. I'll read it and decide next move.

```
# Fraser Build — Day 1 Report (YYYY-MM-DD)

## Landed
- [P0] protocols.py — ✅ 11 body-shape dataclasses, Workout Card, input-mode enum
- [P0] state.py — ✅ thin wrappers around core/memory/api, dislikes.py pattern
- [P0] main.py — ✅ Miya capability registered, star re-exports working
- [P0] tests — ✅ protocol round-trip + test_storage_convention.py green
- [P1] read tools — 6/18 wired (substrate filtered by agent='fraser'), 12/18 mocked
- [P1] handler skeleton — ✅ input-mode router, reasoner stub
- [P1] eval cases fraser_001–010 — drafted, all failing (expected; reasoner stubbed)
- [P2] — not attempted / partial

## Tests
- run_all: NN passed / NN failed
- test_storage_convention.py: green (no new tables)
- Failures (new only): [list]

## Open questions (see FRASER_OPEN_QUESTIONS.md)
- [count]

## Surprises
- [anything architectural that pushed back on the spec]

## Next-5-day plan refinement
- Day 2: [...]
- Day 3: [...]
- ...

## Decision needed from you before Day 2 starts
- [the one or two things I cannot move past without your input]
```

---

## 8. Tone & Style

- Match `agents/the_scientist/dislikes.py` for substrate idioms (commit `e5397ef`). Match `agents/kobe/` only for non-storage code style (type hints, docstrings, dataclasses).
- Type hints everywhere. Docstrings on public functions only. Dataclasses over dicts.
- Commit messages: imperative mood, one logical change per commit. Reference spec sections + ADR-003 in commit bodies.
- If you scope-creep mid-task, **stop**, log it as an open question, and return to the priority list.
- I value clean handoffs over completeness. A perfect P0 + honest open questions beats a sprawling half-built P1.

---

*Ship the spine cleanly. Ask me when blocked. I'll review at the end of the session.*
