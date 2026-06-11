# Proposed Fixes — 2026-06-10 agent shift

Bugs surfaced by new tests. Test Lead writes the failing/xfail test;
the architect ships the production change. Each entry is self-contained.

> Convention: failing tests are marked
> `@pytest.mark.xfail(reason="blocked-by: PF-2026-06-10-NNN", strict=True)`
> so they flip to a hard failure (signalling "remove the xfail") the
> moment the architect's fix lands.

---

## PF-2026-06-10-002 — space after slash bypasses command routing

**Symptom (test):**
`tests/adversarial/test_corpus_routing.py::test_real_phrasing_routes_as_contracted[XFAIL:slash_command:/ fix sat 407]`
(real phrasing from the ledger, 2026-06 — user typed "/ fix sat 407")

**Reproduction:**
```
RAHAT_TEST_MODE=1 python -c "from new_plane.miya_runner.delegate_classifier import classify_delegation; print(classify_delegation('/ fix sat 407'))"
# → ('orchestrate', '/ fix sat 407')   — expected ('kobe_route', ...)
```

**Root cause hypothesis:**
`delegate_classifier.py:32` `_SLASH_RE = re.compile(r"^\s*/[a-z]", re.I)`
requires an alpha char *immediately* after `/`. A stray space ("/ fix")
fails the match, so the command falls through all 9 checks to
`orchestrate` — where the synth layer answers a malformed "/ fix" string
freelance. Low frequency but it is the Bug-I class (a tiny input
variation defeats deterministic routing).

**Proposed fix:**
Allow optional whitespace between the slash and the command letter:
`_SLASH_RE = re.compile(r"^\s*/\s*[a-z]", re.I)`. Kobe's own slash
dispatcher already tolerates the space, so this only widens the gate.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_slash_space_bypass.py`

**Status:** Test added (xfail strict). Architect to action.

---

## PF-2026-06-10-003 — past-tense WOD lookup falls to the synth path

**Symptom (test):**
`tests/adversarial/test_corpus_routing.py::test_real_phrasing_routes_as_contracted[XFAIL:wod_lookup:What was the workout for last Friday?]`
(real phrasing from the ledger)

**Reproduction:**
```
RAHAT_TEST_MODE=1 python -c "from new_plane.miya_runner.delegate_classifier import classify_delegation; print(classify_delegation('What was the workout for last Friday?'))"
# → ('orchestrate', ...)   — expected ('kobe_route', ...)
```

**Root cause hypothesis:**
`_WOD_LOOKUP_RE` branch 1 enumerates interrogatives
`(what.?s|what\s+is|whats|show|tell|...)` but not the **past tense**
"what was". And branch 3 requires the day token to follow "for/on/this"
directly, so "for **last** Friday" (with an intervening "last") misses.
A genuine WOD lookup therefore reaches the orchestrate→synth path — the
exact surface that paraphrased an absence in Bug I (2026-06-09).

**Proposed fix:**
Add `what\s+was` to the branch-1 interrogative alternation, and let the
day-token branch tolerate an optional `last\s+` / `this\s+` qualifier
before the weekday. Keep the design guard so "design a workout I did last
Friday" still orchestrates.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_past_tense_wod_lookup.py`

**Status:** Test added (xfail strict). Architect to action.

---

## PF-2026-06-10-001 — synth prompt is unscoped by intent (Bug-I merge)

**Symptom (test):**
`tests/evals/test_synthesizer_grounding.py::TestKnownSynthGroundingGaps::test_wod_query_prompt_excludes_unrelated_pace_facts`

**Reproduction:**
```
RAHAT_TEST_MODE=1 pytest tests/evals/test_synthesizer_grounding.py -k unrelated_pace -runxfail -q
```

**Root cause hypothesis:**
`synthesizer.py:_build_prompt` (lines 109-137) renders EVERY fact the
orchestrator collected — `active_goal, today_target, pace, recalibration,
gym_wod` — regardless of what the user asked. For a WOD-only question the
orchestrator still pulls `recalibration` (orchestrator.py:306-314, gated
only on `needs_kobe`), so the pace summary lands in the prompt. That is
the second half of Bug I (2026-06-09): Gemini then freelances about pace
when the user asked about the WOD.

**Proposed fix:**
Scope the prompt by intent. Either (a) the orchestrator passes an
`intent`/`focus` hint to `_build_prompt` and the FACTS loop only renders
facts relevant to that intent, or (b) for a pure `is_workout_lookup`
turn, skip the `recalibration`/`pace` fact pulls entirely
(orchestrator.py:306). (a) is safer — keep the facts available but tell
the synth which one is the answer.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_wod_query_no_pace_merge.py`

**Status:** Test added (xfail strict). Architect to action.

---

## PF-2026-06-10-004 — contradictory recalibration summary passed verbatim (Bug-H residual)

**Symptom (test):**
`tests/evals/test_synthesizer_grounding.py::TestKnownSynthGroundingGaps::test_contradictory_summary_not_passed_verbatim`

**Reproduction:**
```
RAHAT_TEST_MODE=1 pytest tests/evals/test_synthesizer_grounding.py -k contradictory_summary -runxfail -q
```

**Root cause hypothesis:**
When arbitration fires `behind_pace`, `_build_prompt` adds the verdict
block (good) but STILL renders `recalibration.summary` verbatim
(synthesizer.py:128-130). With the production data that summary literally
read "Ahead of pace — comfortable buffer." — so the prompt simultaneously
says "behind" (verdict) and "Ahead of pace" (summary). Gemini Flash
paraphrased the misleading summary: that is precisely how Bug H
(2026-06-08) shipped. The verdict block is necessary but not sufficient;
the contradictory raw string must not be handed over un-marked.

**Proposed fix:**
When an arbitration verdict contradicts a fact's `summary`, the prompt
builder should either (a) omit that summary, or (b) wrap it:
"recalibration.summary (SUPERSEDED by arbitration — do not repeat):
…". Pair with the cost-router Pro-escalation already shipped so the
stronger model also sees the explicit supersession.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_arbitration_supersedes_summary.py`

**Status:** Test added (xfail strict). Architect to action.

---

## PF-2026-06-10-005 — orchestrator pulls cross-agent signals unscoped

**Symptom (test):**
`tests/new_plane/test_cross_agent_signal_isolation.py::test_kobe_intent_prompt_excludes_fraser_signals`

**Reproduction:**
```
RAHAT_TEST_MODE=1 pytest tests/new_plane/test_cross_agent_signal_isolation.py -k fraser_signals -runxfail -q
```

**Root cause hypothesis:**
`orchestrator.py:374` `recent_signals = adapter.signals_recent(limit=5)`
passes no `agent`/intent filter, so the synth prompt's "RECENT CROSS-AGENT
SIGNALS" block (synthesizer.py:142-150) can contain a Fraser
design-session payload during a Kobe pace query. The store *supports*
`recent(agent=...)`; the orchestrator just doesn't use it. This is the
mechanism behind "Kobe answers a pace query with workout-design content".

**Proposed fix:**
Scope the pull to the turn's intent/agent: e.g.
`signals_recent(agent=primary_agent, limit=5)` or filter the returned
signals to the agents relevant to the current intent before handing them
to `_build_prompt`. Keep genuinely cross-agent signals only when the
turn is itself cross-agent (a mediation).

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_signal_scope_by_intent.py`

**Status:** Test added (xfail strict). Architect to action.

---

## PF-2026-06-10-007 — compare_harness parity flake (test isolation)

**Symptom (test):**
`tests/new_plane/test_compare_harness.py::test_old_vs_new_parity[*]`

**Reproduction:**
```
# PASSES in isolation
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/test_compare_harness.py -q
# 31 passed

# FAILS when run after sibling tests in tests/new_plane/
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ -q
# 6 failures: WOD/weight/burn/pain queries return EMPTY from new plane
```

**Root cause hypothesis:**
Cumulative module-level state pollution. No single predecessor file
triggers the flake; only the cumulative effect does. Suspect:
`core.io.DB_PATH` and/or `new_plane.signals.store._DB_PATH` are
mutated by sibling tests via direct assignment (not monkeypatch) and
the parity fixture's `monkeypatch.setattr(cio, "DB_PATH", db)` is
overridden during the test by a module re-import. The parity fixture
already does the right thing per the 2026-06-10 fix; the leak is
upstream.

**Proposed fix:**
Either (a) add a session-scoped autouse fixture in
`tests/new_plane/conftest.py` that snapshots+restores
`core.io.DB_PATH` and `new_plane.signals.store._DB_PATH` between
tests, OR (b) refactor the parity test to use the runner's
`Turn(...)` interface directly with a hard-coded test DB rather than
relying on inherited module state.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_parity_fixture_isolation.py`

**Status:** Documented; running the parity tests in isolation is the
current workaround. Lower priority than PF-001..006.

---

## PF-2026-06-10-006 — signals have no chat dimension (concurrent-chat bleed)

**Symptom (test):**
`tests/new_plane/test_cross_agent_signal_isolation.py::test_signals_are_scoped_per_chat`

**Reproduction:**
```
RAHAT_TEST_MODE=1 pytest tests/new_plane/test_cross_agent_signal_isolation.py -k scoped_per_chat -runxfail -q
```

**Root cause hypothesis:**
`signals` table (store.py:73-81) has `agent, type, payload_json, ts,
trace_id` — no `chat_id`. `recent()` cannot filter by chat, so a signal
generated while serving chat A is visible to chat B's `recent()` read.
Today Rahat is single-user/single-chat so the blast radius is small, but
the moment a second chat (or a shared group) is live, recent-signal
context bleeds across conversations.

**Proposed fix:**
Add a nullable `chat_id` column to `signals`, set it on `publish` from
the turn's `chat_id`, and add a `chat_id=` filter to `recent()`. The
orchestrator then pulls `signals_recent(chat_id=turn.chat_id, ...)`.
Backward compatible: existing rows get `chat_id IS NULL` and are treated
as global.

**Suggested registry test name:**
`tests/regression_registry/test_2026-06-10_signal_chat_scope.py`

**Status:** Test added (xfail strict). Architect to action.
