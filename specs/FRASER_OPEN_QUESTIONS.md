# Fraser — Open Questions (Day 1, 2026-05-14)

Per build brief §5: questions that didn't block Day-1 P0 work get
logged here for batch resolution. The blocking ones (state-layer
choice, Workout Card persistence, route versioning) were resolved
live before scaffolding began.

Each question carries the file + line it touches so the v2 brief
revision can close them by reference.

---

## 1. behavioral-transcript-fetch — ✅ RESOLVED 2026-05-14

The Gemini coaching transcript was pasted into
`specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` (4,095 lines, 293 KB) by the
owner — file status flipped from PLACEHOLDER to POPULATED. Day-3
reasoner work is unblocked.

- File: `specs/FRASER_BEHAVIORAL_TRANSCRIPT.md` — populated.
- Consumer: `agents/fraser/handler.py::_build_system_prompt()` — will
  read the file at process boot per the loading order documented in
  the transcript file's header.
- Source of truth remains the Google Doc
  (`1J5Ty8Y1_UoI3byzSDmxkSoLZe0POGDJ77A1jQ8HpCyI`); re-sync via paste
  if the Doc evolves.
- Original Day-1 issue: the Drive MCP couldn't auto-fetch (auth-gated);
  manual paste was the workaround.

---

## 2. input-mode-classifier — Day-3 decision flagged

Day-1 ships a rule-based regex classifier
(`handler.classify_input_mode`). It covers:

- Benchmark / hero WOD names (`_BENCHMARK_NAMES`) — strong signal.
- Pasted rep schemes (`21-15-9`, `5 RFT`) — structural signal.
- Format tokens (`EMOM`, `AMRAP`, `Tabata`, `Smash Format`) — weakest
  if no other indicators.
- Multi-line input with numeric structure — fallback heuristic.

**Architect preference (logged here so the v2 brief can close it):**
hybrid. Keep the regex classifier as a CHEAP pre-filter; route
ambiguous messages to an LLM classifier on Day 3. Pure-LLM is too
expensive for every-turn classification; pure-regex misses the long
tail (e.g., friend's pasted workouts in non-standard formats).

The §13 acceptance bar (≥95% classification accuracy on the eval
set) is the success metric — fall back to LLM the moment regex slips
below that on the 40-case suite.

---

## 3. preference-vs-prvn-precedence — Behavior question

Brief §5: if PRVN prescribes a movement on `fraser_preference`
(dislike), does the preference win or does PRVN win?

**Day-1 thinking:** preference wins, but with a one-line callout in
the Workout Card NOTES so the user sees the override. Rationale:
PRVN is a template; the user is the system of record for their own
body. The substitution path already covers this — `lookup_substitution`
swaps the disliked movement, NOTES carries the rationale.

Exception case to validate: what if the disliked movement is the
*primary lift* of the PRVN day (e.g., user dislikes Deadlifts and
today is W4D2 = heavy DL day)? Two options:
- (a) Swap to a closest analog (e.g., RDL or Sumo) — preserves the
  posterior-chain stimulus the cycle is building.
- (b) Re-program the day entirely with a different primary.

Logged for Day-2/Day-3 reasoner design; the substrate already supports
both.

---

## 4. 1RM-staleness-thresholds — Tunable

Brief §5: >90d warn, >180d block PR-attempts. Reasonable or aggressive?

**Day-1 implementation:** `protocols.ONE_RM_WARN_AFTER_DAYS = 90`,
`protocols.ONE_RM_BLOCK_AFTER_DAYS = 180`. Both are easy to retune.

Counter-arguments to consider:
- 90 days is aggressive for steady-state users — most lifters don't
  re-test 1RMs every quarter. Suggest 120 / 240 for steady-state.
- 90 / 180 is RIGHT for a user actively in a strength cycle (which is
  the Day-1 default user). Tune per-user later via a preference.

For now, the published constant ships at 90 / 180. Override path is
straightforward (per-user pref via `pref_set(AGENT, "stale_warn_days", N)`).

---

## 5. race-tiebreaker — Day-1 architecture question

Brief §5: Kobe ↔ Fraser conflicts — timestamp wins (§10) vs.
last-writer-wins vs. CRDT.

**Day-1 implementation:** the substrate's `valid_from` / `created_at`
columns are already CURRENT_TIMESTAMP-driven, so timestamp-ordering is
the natural fallback. Charter `governance_log` records every write —
the audit trail is complete enough to detect conflicts after the fact.

**Recommendation for v2 brief:** stick with timestamp-ordering for
Day 4 wiring (cheapest, well-supported by the substrate, audit-friendly).
Revisit only if a real conflict surfaces in the eval suite.

CRDTs are overkill for two agents writing to disjoint type spaces
(Kobe writes its own entity types, Fraser writes its own — the only
shared write surface is the `governance_log` table, which is
append-only).

---

## 6. workout-card-render-in-Miya — Out of scope Day 1

Brief §4: don't touch `core/voice.py`. Confirmed — Day-1 scaffold
leaves voice rendering untouched. Miya's wrapping of the Workout
Card to Dakhini happens on Day 5+ per the build order in spec §8.

---

## 7. subject_id-for-multi-subject — Forward-compat decision

Spec §3 says "All carry `subject_id = 'user'` (Fraser is single-subject
for now)". The Day-1 entity bodies do NOT carry `subject_id` —
they inherit single-subject scoping from `agent="fraser"`.

When multi-subject opens (family fitness, training partners), we'll
add a `subject_id` field to each entity body and write a migration
script that backfills `subject_id="user"` on every existing row.

No action needed today; logged for awareness.

---

## 8. miya-registration-deferred — Production safety

`core/miya_main.py` has FraserAgent registration commented out. The
class is importable; the route() method returns a low-confidence
stub Reply. Uncomment on Day 3 when the reasoner produces real
output. Until then, leaving registration on would surface
"[Fraser stub]" responses to the live user for any fitness query
Miya's classifier landed on Fraser.

---

## 9. git-commit-from-sandbox — Tooling issue

The Day-1 scaffold lives on branch `feat/fraser-day1-scaffold` but
could not be committed from the build session — the sandbox runner
hit `.git/index.lock` permission errors. The files are on disk and
the branch is current; commits need to come from the developer's
own shell. No code impact.

---

*Maintained by: build session 2026-05-14. Append new questions as
they emerge during Day 2+.*
