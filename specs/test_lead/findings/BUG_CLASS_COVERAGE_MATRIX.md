# Bug-class coverage matrix — 2026-06-10

Maps every historical incident (registry + TELEGRAM_BUG_HISTORY) and
known weak spot to a bug *class*, the layer that catches it, the coverage
strength after this shift, and concrete next additions. "STRONG" = a
regression here would fail a deterministic test today; "MEDIUM" = pinned
but with gaps; "WEAK" = little/no behavioral net.

| # | Bug class | Example incidents | Layer / files that catch it | Coverage strength | Recommended additions |
|---|---|---|---|---|---|
| 1 | **Wrong-agent routing** | 2026-05-16 Kobe hallucinated WOD; 2026-05-17 slash bypass; 2026-05-23 plan mutations; Bug-K/M/N | LAYER 2 contract + `new_plane/test_runner_delegate_classifier.py` (163) + **NEW** `test_delegate_classifier_properties.py` (15 props) + **NEW** `test_corpus_routing.py` (186 real phrasings) + **NEW** `test_telegram_history_replay.py` | **STRONG** — example + property + real-corpus + history replay | None critical. Consider adding `huberman_route` once it exists. |
| 2 | **Synth paraphrase / hallucination** | 2026-06-08 Bug H; 2026-06-09 Bug I | LAYER 3 eval — **NEW** `test_synthesizer_grounding.py` (7+2xfail); registry `test_2026_06_08_*` + `test_2026_06_09_*`; **NEW** prompt snapshot | **MEDIUM** (was WEAK) — structural grounding pinned; the two residual defects are xfail (PF-001, PF-004); semantic check still needs the LLM judge | Ship PF-001 (intent-scoped prompt) + PF-004 (supersede contradictory summary); add a weekly LLM-judge run (DEAD_TESTS #5). |
| 3 | **Empty / silent response** | 2026-05-17 silent NL response | LAYER 2 + `tests/silent_failure/` + **NEW** parity aggregate `test_no_new_plane_silent_failures_across_corpus` | **STRONG** | Extend the silent-failure guard to the orchestrate path under a forced adapter error (ties to COVERAGE_AUDIT gap 1). |
| 4 | **Stale-fact / sync claim** | 2026-05-17 show_plan lies "no gym synced"; 2026-05-18 plan shows gym alongside cadence | LAYER 5 registry (`test_2026_05_17_show_plan_lies_about_sync.py`) | **MEDIUM** — pinned for the specific render, not generalized | Add a live-fact freshness probe: assert a render's sync claim is derived from the *current* `parse_gym_plan()` result, not a cached flag (generalize the 05-17 pin). |
| 5 | **Multi-turn confusion ("Yes")** | Bug-J yes-after-offer | `test_runner_chat_memory_bridge.py` (6) + **NEW** replay `bug-J` case | **MEDIUM** — flag-gated (`RAHAT_XAGENT_MEMORY`, default off in prod) | Test "Yes" after a Kobe offer vs after a Fraser offer resolve to different routes; pin the load path (COVERAGE_AUDIT gap 4). |
| 6 | **Routing typo / input-variation tolerance** | 2026-06-09 "tommorow"; "/ fix" space | **NEW** property fuzz (day-typo, whitespace, unicode) + **NEW** corpus xfails PF-002 (`/ fix`), PF-003 (past-tense WOD) | **MEDIUM** — common typos pinned; two real gaps are xfail | Ship PF-002 (`^\s*/\s*[a-z]`) + PF-003 (`what was` + `last/this <day>`); re-mine the corpus monthly. |
| 7 | **Cross-agent signal leakage** | SUITE_MAP §9.7 weak spot | **NEW** `test_cross_agent_signal_isolation.py` (4+2xfail) | **MEDIUM** (was WEAK) — store-level filtering pinned; orchestrator-level leak is xfail | Ship PF-005 (scope `signals_recent` by intent/agent). |
| 8 | **Off-topic fact merging (intent-unscoped synth)** | 2026-06-09 Bug I (2nd half: pace fact in a WOD reply) | **NEW** `test_synthesizer_grounding.py::test_wod_query_prompt_excludes_unrelated_pace_facts` (xfail PF-001) | **WEAK** — defect pinned as xfail, not yet fixed | Ship PF-001; then add positive tests that a WOD prompt contains only WOD facts and a pace prompt only pace facts. |
| 9 | **Telegram transport / poll-loop robustness** | SUITE_MAP §9.6 weak spot | **NEW** `test_runner_telegram_chaos.py` (22) | **STRONG** (was WEAK) — timeout, network error, multi-msg, unicode, 4096-split, markdown fallback, offset invariant | Add a one-iteration integration test that drives `cmd_serve` with a mocked client (chat-filter + per-update exception → error reply). |
| 10 | **Old→new migration parity** | ADR-013 cutover | `production_parity/` + **NEW** `test_compare_harness.py` parity (22 fixtures) | **MEDIUM** — silent-failure + routing parity pinned; text parity not (old side is a sim proxy) | Record cassettes for the old plane's real responses and diff against new on deterministic facts (pace number, day name, kcal target). |
| 11 | **Concurrent-chat isolation** | SUITE_MAP §9.7 ("two messages same chat same second") | **NEW** `test_cross_agent_signal_isolation.py::test_signals_are_scoped_per_chat` (xfail PF-006) | **WEAK** — structural gap pinned (no `chat_id` dimension) | Ship PF-006 (add `chat_id` to signals + `recent(chat_id=)`); then test chat A / chat B don't bleed and same-second writes serialize. |
| 12 | **Secret / PII leakage, prompt injection, jailbreak** | (no incident — proactive) | LAYER 4 `tests/evals/test_adversarial.py` (14) | **MEDIUM** — small probe set, green | Expand probes; add an injection probe that targets the new-plane synth prompt specifically (e.g., a fact payload containing "ignore previous instructions"). |

## Where the suite is now STRONG vs UNDER-SERVED

**Strong (deterministic net, low regression risk):** wrong-agent routing
(class 1), silent response (3), typo tolerance for common cases (6),
Telegram poll-loop (9).

**Under-served — priority for the next shift:**
1. **Off-topic fact merging (class 8)** and **synth grounding (class 2)** —
   the exact Bug-H/Bug-I shape. The defects are *pinned* (xfail PF-001,
   PF-004) but **not fixed**; until the architect ships those, the suite
   documents the bug rather than preventing it. This is the single
   highest-leverage area.
2. **Cross-agent + concurrent-chat isolation (7, 11)** — store filters
   exist but the orchestrator doesn't use them and there's no chat
   dimension (PF-005, PF-006). Low blast radius today (single user) but a
   latent multi-chat hazard.
3. **Stale-fact generalization (4)** — pinned per-incident, not as a
   class-level freshness invariant.

## Throughline

Bug H and Bug I were both class 2/8 (the synth turning unsupported facts
into user-read assertions). The suite was STRONG on routing (class 1) —
which is why it caught nothing: the bugs lived one layer past routing, in
the synth's relationship to its facts. This shift moves classes 2, 7, 8,
9 from WEAK toward MEDIUM/STRONG and pins the residual defects as strict
xfails so the architect's fixes have a finish line.
