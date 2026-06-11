# Scenario Coverage Matrix — new plane Miya

**2026-06-09.** Maps every distinct user-behavior scenario from the
Fraser Gemini chat, the Sports Scientist Gemini chat, the old Miya
chat history, and the 33 bugs in the regression registry to its
new-plane Miya routing decision and the test that pins it.

**Total scenarios covered:** ~150.
**Total new-plane tests:** 407 (172 baseline + 235 added today).
**Old plane 5-layer:** 962 tests still green.

---

## The four delegation paths

The new plane orchestrator decides between four paths:

| Path | When | What runs |
|---|---|---|
| **`kobe_route`** | slash, plan mutation, weight/HRV/burn log, status query, pain/profile, recovery protocol, `@kobe` | `agents.the_scientist.handler.route()` directly. Skips Miya synth. |
| **`fraser_route`** | `@fraser` explicit address | `agents.fraser.handler.route()` directly. Skips Miya synth. |
| **`orchestrate` → lookup** | "what's the workout for X" + day token | `kobe_gym_wod_on(day)` + arbitration + Gemini synth in Miya voice. |
| **`orchestrate` → design** | "design me", "scale", "create", "swap", "substitute" | `fraser_design_session(msg)` + arbitration + Gemini synth. |
| **`orchestrate` → coaching** | open-ended ("how am I doing", "should I rest") | `kobe_active_goal` + `kobe_recalibration` + arbitration + Gemini synth. |

---

## Fraser transcript scenarios (sports-coach)

| Scenario | Path | Test |
|---|---|---|
| "design me a workout for tomorrow" | orchestrate → design | `test_transcript_scenarios.py::TestFraserCoachScenarios::test_design_requests_orchestrate_to_fraser` |
| "scale today's WOD" | orchestrate → design | same |
| "create a session for Friday" | orchestrate → design | same |
| "substitute wall balls" | orchestrate → design | same |
| "@fraser design me a wod" | `fraser_route` | `test_explicit_fraser_address_routes_directly` |
| "give me a good warmup" | orchestrate → design | `test_warmup_request_via_design` |
| "give me a cool down" | orchestrate → design | `test_cooldown_request_via_design` |
| "give me a recovery routine" | `kobe_route` (deterministic) | `test_recovery_routine_request_routes_to_kobe` |
| Calorie-targeted workout ("burn 800 cal in 75 min") | orchestrate → design | `test_calorie_targeted_workout_via_design` |
| Equipment unavailable ("no treadmill") | orchestrate → design | `test_substitute_for_equipment_unavailable` |
| Pain log ("my neck hurts") | `kobe_route` | `test_mobility_issue_modification` |
| Sick day ("under the weather") | orchestrate → design | `test_sick_workout_request_orchestrates` |
| WOD lookup with day ("what's the workout on Friday") | orchestrate → kobe_gym_wod_on | `TestKobeHallucinatedWod::test_wod_lookup_does_not_call_fraser_compose` |
| Negation parsing ("no rowing") | Fraser composer parses correctly | `TestParseRequestNegation::test_no_rowing_parsed_correctly` |

---

## Sports Scientist transcript scenarios (nutrition/calorie coach)

| Scenario | Path | Test |
|---|---|---|
| "how many calories did I burn this week" | `kobe_route` | `TestSportsScientistScenarios::test_calorie_tracking_queries_route_to_kobe` |
| "how many should I burn today" | `kobe_route` | same |
| "weekly target" / "weekly remaining" | `kobe_route` | same |
| "last week" / "this week's total" | `kobe_route` | same |
| Weight log ("195", "89 kg", "I weigh 195 lbs") | `kobe_route` | `test_weight_logging_routes_to_kobe` |
| HRV log ("HRV: 45", "my HRV is 38") | `kobe_route` | `test_hrv_logging_routes_to_kobe` |
| Burn log ("burned 800 cal", "crossfit 950 kcal") | `kobe_route` | `test_burn_logging_routes_to_kobe` |
| `/pace` | `kobe_route` | `test_pace_query_routes_to_kobe` |
| "show my plan" | `kobe_route` | `test_show_plan_routes_to_kobe` |
| Recovery protocols (7/15 breathing, box, pre-fuel, post-recovery) | `kobe_route` | `test_recovery_protocol_routes_to_kobe` |
| Nutrition coaching ("salmon vs chicken") | orchestrate | `test_nutrition_query_orchestrates` |
| "when should I weigh in" | orchestrate | `test_pre_weigh_in_strategy_orchestrates` |
| "@kobe how am I doing" | `kobe_route` (prefix stripped) | `test_explicit_kobe_address` |

---

## Old Miya chat scenarios (the bug source)

| Scenario | Path | Test |
|---|---|---|
| "What is tomorrow's WOD" | orchestrate → kobe_gym_wod_on | `TestOldMiyaTranscriptScenarios::test_wod_lookup_question` |
| `/replan rest Monday , CrossFit on Tue Wed Thu. Zone 2 run on Sat` | `kobe_route` | `test_replan_command_with_plan_in_message` |
| "Rest on Monday" | `kobe_route` | `test_rest_on_monday_routes_to_kobe` |
| "Wed for CrossFit" (case variations) | `kobe_route` | `test_day_picks_route_to_kobe` |
| "Tuesday for CrossFit" | `kobe_route` | same |
| "Thu for CrossFit" | `kobe_route` | same |
| "Friday for rest" | `kobe_route` | same |
| "pick Mon for CrossFit" | `kobe_route` | same |
| `/recaliberate` | `kobe_route` | `test_recaliberate_command` |
| `/plan` | `kobe_route` | `test_plan_command` |
| **"Yes" follow-up (the bug)** | orchestrate w/ chat_memory | `test_yes_alone_routes_with_context` + `test_runner_chat_memory_bridge.py` |
| "tolerate partner" | `kobe_route` | `test_tolerate_partner_routes_to_kobe` |
| "tolerate overhead squat" | `kobe_route` | `test_tolerate_overhead_squat` |

---

## Regression registry coverage (33 bugs)

Each bug in `tests/regression_registry/` has an equivalent new-plane
test in `tests/new_plane/test_regression_equivalents.py`:

| Date | Bug | New-plane test |
|---|---|---|
| 2026-05-16 | Kobe hallucinated WOD | `TestKobeHallucinatedWod` |
| 2026-05-17 | Fraser lookup intent | `TestFraserLookupIntent` |
| 2026-05-17 | Show-plan lies about sync | `TestShowPlanLiesAboutSync` |
| 2026-05-17 | Silent response on NL | `TestSilentResponseNaturalLanguage` |
| 2026-05-17 | Slash bypass dispatched to Fraser | `TestSlashBypassDispatchedToFraser` |
| 2026-05-18 | Pick days recalibrates | `TestPickDaysRecalibrates` |
| 2026-05-18 | Weekday index case mismatch | `TestWeekdayIndexCaseMismatch` |
| 2026-05-19 | Chat memory coherence | `TestChatMemoryCoherence` |
| 2026-05-23 | Composer follow-up mode | `TestComposerFollowupMode` |
| 2026-05-23 | Plan mutations | `TestPlanMutations` |
| 2026-05-23 | Relative day WOD lookup | `TestRelativeDayWodLookup` |
| 2026-05-24 | Structured day picks | `TestStructuredDayPicks` |
| 2026-05-24 | Voice and render | `TestVoiceAndRender` |
| 2026-05-25 | Pace verdict consistent | `TestPaceVerdictConsistent` |
| 2026-05-25 | Parse request negation | `TestParseRequestNegation` |
| 2026-05-25 | Project goal ETA | `TestProjectGoalEta` |
| 2026-05-25 | Walk nudge daily cap | `TestWalkNudgeDailyCap` |
| 2026-05-25 | Weekly target rescales plan | `TestWeeklyTargetRescalesPlan` |
| 2026-06-08 | Bug H — missed workout "ahead" inversion | `TestMissedWorkoutNotCalledAhead` (also pinned in arbitration regression registry by KTLO architect) |

Remaining regression registry bugs (cooldown_yield_flag, goal_driven_weekly_target,
xagent_memory, etc.) are covered by their original tests via Kobe's `route()`
when the new plane delegates — they don't need new-plane equivalents because
the underlying Kobe code is unchanged and called directly.

---

## What the new plane orchestrator now does

```
User message arrives
  ↓
classify_delegation(msg) → "kobe_route" | "fraser_route" | "orchestrate"
  ↓
┌─────────────────────────────────────────────────────────────────────┐
│ kobe_route path                                                     │
│   agents.the_scientist.handler.route(stripped_msg)                  │
│   → returns Kobe's deterministic output                             │
│   → published as "miya_delegated" signal                            │
│   → recorded to chat_memory (if RAHAT_XAGENT_MEMORY=1)              │
│   → mirrored to vault/rahat.db (if NEW_MIYA_USE_LIVE_DB=1)          │
└─────────────────────────────────────────────────────────────────────┘
  OR
┌─────────────────────────────────────────────────────────────────────┐
│ fraser_route path                                                   │
│   agents.fraser.handler.route(stripped_msg)                         │
│   → returns Fraser's workout card text                              │
│   → same signal + chat_memory + decision-ledger logic               │
└─────────────────────────────────────────────────────────────────────┘
  OR
┌─────────────────────────────────────────────────────────────────────┐
│ orchestrate path (existing — was the only path before today)         │
│   1. classify_intent → needs_kobe / needs_fraser / lookup / design  │
│   2. Pull facts (active_goal, recalibration, gym_wod, fraser_design)│
│   3. Arbitrate (behind_pace, goal_close)                            │
│   4. Charter check (allow / veto)                                   │
│   5. Cost-route to Flash or Pro                                     │
│   6. Synthesize via Gemini in Miya voice                            │
│      ↳ Now includes chat_memory_block when RAHAT_XAGENT_MEMORY=1    │
│   7. Publish "miya_synthesized" signal                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Coverage gaps & known limitations

These scenarios appear in the transcripts but are NOT yet fully
end-to-end testable in the new plane. They are documented for the
next session:

1. **Verbatim WOD output.** The synthesizer prompt now has a stronger
   "SOURCE OF TRUTH" marker but Gemini may still paraphrase. Fix:
   when `gym_wod` is in facts, bypass synthesis entirely and return
   the WOD text as-is in a thin Miya wrapper. Not done — would have
   blocked the verbatim transcript scenario.

2. **Multi-turn "Yes/No" confirmation flows.** Chat_memory captures
   the previous turn, but the prompt instructions are advisory only.
   Stronger fix: build an explicit "pending_confirmation" state in
   the signal store that the orchestrator reads first.

3. **Fraser composer's full feature set** — pain blocks, mobility
   blocks, athlete profile injection — is handled inside `composer.py`
   when called through `fraser_route` or `fraser_design_session`. The
   new plane doesn't add anything here; it just routes correctly.

4. **Huberman agent.** No new-plane Huberman path. Kobe's `route()`
   already handles Huberman delegation internally; @huberman is
   funneled through Kobe.

5. **`pending_clarification` (A/B/C) flow** — old Miya has a
   60-second TTL clarification resolver. Not ported to the new plane
   yet. Most cases that triggered it now route deterministically.

6. **Cool-down LLM yield** — `RAHAT_COOLDOWN_LLM=1` flag on the old
   plane causes `post_recovery` and `pre_fuel` to yield to LLM. Both
   are now correctly routed to Kobe by `_RECOVERY_RE`, but the LLM
   yield behavior itself lives in Kobe and is preserved end-to-end.

---

## Files added/modified this session (2026-06-09)

```
A new_plane/miya_runner/delegate_classifier.py        (new — routing logic)
M new_plane/miya_runner/native_client.py              (+kobe_route, +fraser_route)
M new_plane/miya_runner/orchestrator.py               (+delegation branch, +chat_memory bridge)
M new_plane/miya_runner/synthesizer.py                (+chat_memory_block param)
A tests/new_plane/test_runner_delegate_classifier.py  (111 tests)
A tests/new_plane/test_runner_delegation_path.py      (14 tests)
A tests/new_plane/test_runner_chat_memory_bridge.py   (6 tests)
A tests/new_plane/test_transcript_scenarios.py        (66 tests)
A tests/new_plane/test_regression_equivalents.py      (38 tests)
A specs/SCENARIO_COVERAGE_MATRIX.md                   (this file)
A specs/OVERNIGHT_BUILD_2026-06-09.md                 (handoff doc)
```

**Test deltas:** 172 → 407 new-plane (+235). Old plane 5-layer unchanged at 962.
