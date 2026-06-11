# Telegram Bug History

Raw transcripts of bugs the user observed in production against
RahatBadeMiya_bot (new plane) and SCIENTIST_BOT (old plane). This
file is **fixture material**, not narrative. Treat each entry as
input for a replay test.

Format per entry:
- **ID** — short slug used in tests
- **Date** — when the bug was observed in Telegram
- **Bot** — which bot/plane was running
- **Transcript** — verbatim user + bot exchange
- **What went wrong** — the user-facing defect
- **Root cause** — diagnosed by architect (where known)
- **Fix shipped** — git ref + brief
- **Replay-test contract** — what the test should assert

---

## Entry 1 — bug-H-pace-contradicts-missed (2026-06-08)

**Bot:** new plane RahatBadeMiya v2
**Severity:** high — two contradictory facts in one response

### Transcript

```
Venkat: where am I on pace

Bot: Hau ustad. You're on a tear this week.
     Ahead of pace — comfortable buffer.
     Burned 3,424 / 6,000 — comfortable buffer.

     Missed: Mon CrossFit.
```

### What went wrong

The bot called the user "ahead of pace" while ALSO listing a missed
workout. The Mon-CrossFit miss should have made the user *behind*
prorated pace, not ahead.

### Root cause

`agents.the_scientist.tools.get_recalibration()` correctly returned
`{"behind_pace": True, "summary": "Ahead of pace — comfortable
buffer."}` — the structured field said *behind* but the text summary
said *ahead*. The arbitration layer DID detect the contradiction
(verdict `behind_pace`), but the synth prompt didn't surface the
verdict strongly enough; Gemini Flash paraphrased the misleading
`summary` text.

### Fix shipped

- Arbitration verdict promoted to leading INSTRUCTION in the synth
  prompt.
- Cost router escalates to Pro on arbitration-fire.
- Regression test:
  `tests/new_plane/test_regression_equivalents.py::TestMissedWorkoutNotCalledAhead`

### Replay-test contract

```python
{
    "id": "bug-H-pace-contradicts-missed",
    "user_message": "where am I on pace",
    "must_not_contain": ["ahead of pace", "comfortable buffer"],
    "must_arbitrate": "behind_pace",
    "fact_overrides": {
        "active_goal": {"active": False},
        "recalibration": {
            "behind_pace": True,
            "summary": "Ahead of pace — comfortable buffer.",
        },
    },
}
```

---

## Entry 2 — bug-I-tommorow-wod-paraphrase (2026-06-09 23:45)

**Bot:** new plane RahatBadeMiya v2
**Severity:** high — paraphrased an absence into a sync-status claim;
mixed in unrelated pace fact that was also wrong

### Transcript

```
Venkat: What is tommorows WOD

Bot: Tomorrow's WOD hasn't been synced from the gym yet. With your
     goal date tomorrow, June 10th, Kobe's recalibration shows
     you're 1,433 kcal ahead of plan for the week. This gives you
     a solid buffer.

Venkat: /plan

Bot: This week — Jun 8 – Jun 14
     Tier hammer, target 6,000 kcal (6,000 kcal from plan + ~0 kcal NEAT).
     Mon: Active rest → ideal 0 kcal — burned 409 kcal
          ⤷ gym today: Bench Press 7-5-3-7-5-3 (skip per your plan, or pick Mon for CrossFit to swap)
     ▶ Tue: CrossFit → ideal 1,325 kcal — burned 1,024 kcal
     Wed: CrossFit → ideal 1,325 kcal
     Thu: CrossFit → ideal 1,325 kcal
     Fri: Active rest → ideal 0 kcal
          ⤷ gym today: Snatch Complex (blocked: overhead squat — tolerate overhead squat to scale in)
     Sat: Zone-2 10K → ideal 1,425 kcal
          ⤷ gym today: Warm-Up (blocked: partner — tolerate partner to scale in)
     Sun: Active rest → ideal 600 kcal
          ⤷ gym today: Warm-Up (skip per your plan, or pick Sun for CrossFit to swap)
     Week so far: 1,433 kcal / 6,000 kcal.
```

### What went wrong

Two bugs in the first response.

1. The "WOD hasn't been synced" claim was a **paraphrase of an absence**.
   Kobe's `get_gym_wod_on()` for Wed returned an empty/None value;
   Gemini paraphrased that absence as a confident claim about gym
   integration state ("hasn't been synced"). The bot never queried
   gym-integration health — it invented that explanation.

2. The "1,433 kcal ahead of plan" claim was **factually wrong**.
   1,433 / 6,000 by Tue evening is ~280 kcal BEHIND prorated pace
   (expected: 6,000 × 2/7 ≈ 1,714). Same Bug H shape resurfacing in
   a different intent.

The `/plan` response, by contrast, was correct — it routed through
Kobe's deterministic dispatcher (kobe_route via slash) and rendered
the structured weekly grid without going through synth.

### Root cause

`classify_delegation()` had no WOD-lookup pattern. Queries containing
"WOD" + a lookup interrogative fell through every check and went to
`orchestrate`. In orchestrate path, the synth layer (1) paraphrased
the empty gym_wod fact and (2) included pace facts in the prompt that
weren't relevant to the user's question — so Gemini freelanced.

Additionally, the day-token regex didn't tolerate "tommorow" (typo),
so even if the orchestrate WOD path had been correct, the day would
have defaulted to "today" rather than tomorrow.

### Fix shipped

- `new_plane/miya_runner/delegate_classifier.py`:
  - Added `_WOD_LOOKUP_RE` — catches "what is/show/tell ... WOD/
    workout/session/programming" with typo-tolerant day tokens
    (tommorow, tomorow, day names, "for/on/this <day>").
  - Added `_WOD_DESIGN_GUARD_RE` — preserves design-intent routing
    to Fraser (must NOT swallow "design me a workout").
  - New check #8 in `classify_delegation()`.
- `tests/new_plane/test_runner_delegate_classifier.py`: +29 WOD-lookup
  positives, +16 design-intent negatives, +1 @fraser override.
- `tests/new_plane/test_regression_equivalents.py`: new
  `TestWodLookupDoesNotParaphrase` + `TestWodDesignIntentStillReachesFraser`.

### Replay-test contract

```python
[
    {
        "id": "bug-I-tommorow-wod-typo",
        "user_message": "What is tommorows WOD",
        "must_route_to": "kobe_route",
        "must_not_contain": ["hasn't been synced", "ahead of plan", "solid buffer"],
    },
    {
        "id": "bug-I-canonical-tomorrows-wod",
        "user_message": "what is tomorrow's WOD",
        "must_route_to": "kobe_route",
        "must_not_contain": ["hasn't been synced", "ahead of plan"],
    },
    {
        "id": "bug-I-plan-still-works",
        "user_message": "/plan",
        "must_route_to": "kobe_route",
        "must_contain_one_of": ["This week —", "Tier "],
    },
]
```

---

## Entry 3 — bug-J-yes-followup-confusion (history reference)

**Bot:** old Miya / pre-cutover
**Date:** observed multiple times May 2026

### Transcript pattern

```
Venkat: my hip hurts

Bot: Want me to swap squats for hip-friendly alternatives in
     today's workout?

Venkat: Yes

Bot: I'm not sure how to route that.
```

### What went wrong

"Yes" alone has no agent affinity. The router rejected it and emitted
the generic fallback ("I'm not sure how to route that"), losing the
context of the preceding turn.

### Root cause

No conversation memory passed to the router. Each turn was classified
in isolation.

### Fix shipped (chat_memory bridge, flag-gated)

- `RAHAT_XAGENT_MEMORY=1` enables chat_memory append on every turn.
- Synthesizer prompt includes "CONVERSATION SO FAR" block when memory
  is on.
- Tests:
  `tests/new_plane/test_runner_chat_memory_bridge.py` (6 tests).

### Replay-test contract

```python
{
    "id": "bug-J-yes-after-recovery-offer",
    "history": [
        {"role": "user", "text": "my hip hurts"},
        {"role": "bot", "text": "Want me to swap squats for hip-friendly alternatives?"},
    ],
    "user_message": "Yes",
    "must_not_contain": ["I'm not sure how to route that", "not sure how"],
}
```

---

## Entry 4 — bug-K-wod-lookup-misdirected-to-fraser (2026-05-16)

**Bot:** old plane / Kobe pre-mesh-routing
**Date:** 2026-05-16

### Transcript pattern

```
Venkat: what is the WOD

Bot (Kobe): [hallucinated workout — Bench 5x5, AMRAP 20...]
```

### What went wrong

Kobe answered with a hallucinated WOD instead of either delegating to
Fraser (gym source-of-truth ownership) or saying "I don't have that".

### Root cause

Kobe's description claimed workout-lookup territory too broadly, so
the capability router picked Kobe for WOD-lookup queries. Kobe's
prompt had no tool call to fetch real WOD data, so it freelanced.

### Fix shipped

- Kobe description tightened (ADR-006 description-correctness).
- `delegate_to` wiring in Kobe (ADR-007 cross-agent).
- `_should_delegate` detector.
- Registry:
  `tests/regression_registry/test_2026_05_16_kobe_hallucinated_wod.py`.

### Replay-test contract

```python
{
    "id": "bug-K-wod-lookup-not-fraser",
    "user_message": "what is the WOD",
    "must_route_to": "kobe_route",  # NEW: was "fraser via delegation" in old plane
    "forbidden_used_tools": ["fraser_design_session"],
}
```

---

## Entry 5 — bug-L-show-plan-lies-about-sync (2026-05-17)

**Bot:** old plane
**Date:** 2026-05-17

### Transcript pattern

```
Venkat: /plan

Bot: Plan for the week:
     [...]
     No gym plan synced.
```

### What went wrong

Bot claimed "No gym plan synced" even though the user HAD synced the
bookmarklet, and `parse_gym_plan()` returned real data.

### Root cause

`handle_show_plan` used a stale `user_state.plan_fallback_*` flag
instead of deriving sync state from the current `parse_gym_plan()`
result.

### Fix shipped

- `handle_show_plan` now derives `is_fallback` per render.
- Registry:
  `tests/regression_registry/test_2026_05_17_show_plan_lies_about_sync.py`.

### Replay-test contract

```python
{
    "id": "bug-L-show-plan-no-stale-sync-claim",
    "user_message": "/plan",
    "preconditions": {"parse_gym_plan_returns_real_data": True},
    "must_not_contain": ["No gym plan synced"],
}
```

---

## Entry 6 — bug-M-slash-bypass-to-fraser (2026-05-17)

**Bot:** old plane
**Date:** 2026-05-17

### Transcript pattern

```
Venkat: /pace

Bot (Fraser): Here's a workout designed for your pace... [WRONG AGENT]
```

### What went wrong

Slash commands were going through the capability classifier and being
misrouted to Fraser.

### Root cause

Slash dispatcher didn't run before the classifier. Classifier picked
Fraser because "pace" appeared in Fraser's description as well as
Kobe's.

### Fix shipped

- Slash dispatcher runs before classifier (top of `_legacy_route`).
- Registry:
  `tests/regression_registry/test_2026_05_17_slash_bypass_dispatched_to_fraser.py`.

### Replay-test contract — new plane equivalent

```python
{
    "id": "bug-M-slash-always-kobe",
    "user_messages": ["/pace", "/plan", "/today", "/week", "/next", "/help", "/fix Mon 800", "/pain neck mild", "/profile"],
    "must_all_route_to": "kobe_route",
}
```

---

## Entry 7 — bug-N-tolerate-pain-profile-mutation (2026-05-23)

**Bot:** old plane
**Date:** 2026-05-23

### Transcript pattern

```
Venkat: tolerate partner

Bot: [generic acknowledgement, no plan update]
```

### What went wrong

Natural-language plan mutation ("tolerate X" = scale in a blocked
movement) wasn't being recognized by the slash dispatcher.

### Root cause

Slash dispatcher only ran on messages starting with `/`. NL mutations
fell through to the classifier and were mis-handled.

### Fix shipped

- NL pattern recognition in `_legacy_route` for
  tolerate/pick/swap/rest mutations.
- Registry: 2026-05-23 plan_mutations.

### Replay-test contract — new plane

```python
{
    "id": "bug-N-tolerate-plan-mutation",
    "user_message": "tolerate partner",
    "must_route_to": "kobe_route",
}
```

---

## Entry 8 — bug-O-pain-profile-natural-language (2026-05-23)

```
Venkat: my hip hurts

Bot: [no pain log update, no clarification]
```

NL pain capture failed; required `/pain` slash. Fix: NL pain pattern
in classifier. Registry: 2026-05-23 pain_profile_slash.

### Replay contract

```python
{
    "id": "bug-O-pain-NL-captured",
    "user_message": "my hip hurts",
    "must_route_to": "kobe_route",
}
```

---

## Entry 9 — bug-P-weight-log-natural-language (May 2026)

```
Venkat: 154

Bot: [confused]
```

Bare-number weight log wasn't recognized. Fix: bare-number pattern in
classifier (`_WEIGHT_LOG_RE` second branch). Registry pinned.

### Replay contract

```python
{
    "id": "bug-P-bare-number-weight-log",
    "user_messages": ["154", "154.5", "154 kg"],
    "must_all_route_to": "kobe_route",
}
```

---

## Entry 10 — bug-Q-hrv-log (May 2026)

```
Venkat: HRV 38

Bot: [classifier-confused]
```

Fix: `_HRV_LOG_RE`. Pinned in delegate classifier tests.

### Replay contract

```python
{
    "id": "bug-Q-hrv-log",
    "user_messages": ["HRV 38", "hrv: 42", "my HRV is 50"],
    "must_all_route_to": "kobe_route",
}
```

---

## Entry 11 — bug-R-burn-log (May 2026)

```
Venkat: crossfit 900 cal

Bot: [classifier-confused]
```

Fix: `_BURN_LOG_RE`. Pinned.

### Replay contract

```python
{
    "id": "bug-R-burn-log",
    "user_messages": ["burned 800 cal", "crossfit 900 cal", "z2 600 kcal"],
    "must_all_route_to": "kobe_route",
}
```

---

## Entry 12 — bug-S-recovery-breathing (May 2026)

```
Venkat: 7/15 breathing

Bot: [no protocol surfaced]
```

Fix: `_RECOVERY_RE`. Pinned.

### Replay contract

```python
{
    "id": "bug-S-recovery-protocols",
    "user_messages": [
        "7/15 breathing", "seven fifteen breathing",
        "box breathing", "pre-fuel", "post-recovery routine",
    ],
    "must_all_route_to": "kobe_route",
}
```

---

## How to use this file

In your test replay harness
(`tests/new_plane/test_telegram_history_replay.py`), import each
entry's contract dict. Treat each contract as one parameterized test
case.

If you add a new entry to this file, add the corresponding contract
to the replay harness in the same commit. The bug-to-test pre-push
gate will enforce this for `fix:` commits.

If you find a NEW bug in production, add it here AND open a registry
file under `tests/regression_registry/test_YYYY-MM-DD_*.py`. Both.
This file is the narrative; the registry file is the executable
test. They must stay in sync.
