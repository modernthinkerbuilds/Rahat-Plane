# Telegram transcript review — 2026-05-24 (L8 architecture pass)

**Branch:** `feat/agent-control-plane-fixes` (off `main @ eabae6a`).
**Test status:** full 5-layer stack green — 28 unit / 780 contract / 101 eval /
14 adversarial / 17 regression. Nothing merged, pushed, or flag-flipped — that's
yours on the Mac after review.

You flagged two things specifically — "it did not give me target weight per my
initial ask" and "it did not give me the WOD unless I typed WOD" — plus the
cool-down that repeated itself verbatim. Reading the whole transcript, the
failures aren't four unrelated bugs; they're the **ADR-011 thesis showing up in
production**: deterministic code making judgment calls that belong to the model,
and a conversation memory that's siloed per-agent instead of shared across the
mesh.

## Scorecard

| # | Symptom (from the transcript) | Root cause | Status |
|---|---|---|---|
| A | "workout for Tuesday" → *"check the SugarWOD app"*; "WOD for Tuesday" → the real WOD | `handle_workout_on` matches the WOD by the cadence row's `gym_label`; when the loaded pull is a different week the match misses and it punts | **Fixed** (`9f79f62`), tested |
| B/C/D | "scale this" / "scale Tuesday's session" / "Tuesday's WOD is with you" → Fraser says it has no workout | the dispatcher serves Kobe's WOD **without** writing it to `chat_memory`; Fraser reads `chat_memory` for context → it never saw the WOD | **Fixed** (`0c10134`), tested, **flag-gated default OFF** |
| E | weights: *"assuming a standard 75%…"* when the WOD said *"start ~60%, build to ~80%"* | composer had no instruction to honor the programming's own loading scheme; also depends on B (needs the WOD in context) | **Fixed** (`63d2a67`), tested |
| F | "is this cool-down specific to my mobility?" → identical canned text | a deterministic `post_recovery` route intercepts "cool down" and returns a static block; never personalized, never re-composed | **Diagnosed + recommended; not implemented** (needs your routing call) |

## A — "workout for X" must surface the WOD (fixed)

**Symptom.** "What is the workout for Tuesday?" → *"Tuesday ka workout hai
CrossFit, target ~1,300 kcal. WOD details ke liye SugarWOD app check kar lo."*
But "What is the WOD for Tuesday?" → the full Clean-Complex + "Don't Speak" EMOM.
Same data, two answers.

**Root cause.** Two handlers read the same gym plan by different keys:
`handle_gym_wod_on` matches by **weekday token** (TUE) and succeeds;
`handle_workout_on` matches by the cadence row's **`gym_label`**. On 2026-05-24
(Sunday) the loaded SugarWOD pull is *next* week's ("TUE 26"), so the
this-week cadence row's label doesn't match any loaded day → `summary` empty →
the "check the app" fallback fires. The data was right there; the lookup key was
wrong.

**Fix.** `handle_workout_on` now falls back to a weekday-token read (the exact
source `handle_gym_wod_on` uses) when the label match yields nothing, before
defaulting to the app message. The honest "check app" message is preserved when
the gym genuinely has no entry for that weekday (pinned by an inverse test). No
flag — it's a clean bugfix with a regression test.

## B/C/D — mesh-level conversation memory (fixed, flag-gated OFF)

**Symptom.** After Kobe printed the Tuesday WOD, "How should I scale this?"
returned a *Zone-2 session* (ignored the WOD); "How should I scale the Tuesday's
session?" → *"you haven't shared the specific workout for Tuesday yet — paste it
here."* The WOD was on screen two turns earlier.

**Root cause — the deep one.** `chat_memory` is **Fraser-private**: only Fraser's
composer writes to it (`composer._record_turn`). The deterministic dispatcher
serves Kobe's WOD lookups directly and never records them. So when Fraser is
asked "scale this", its conversation block genuinely doesn't contain the WOD —
it has no way to resolve "this." Memory is siloed per-agent when it needs to be
shared across the mesh.

**Fix.** `miya._dispatch_to` (the single choke point every single-agent dispatch
funnels through — Tier-0 addressing, slash bypass, classifier, triggers) now
records each **non-Fraser** agent turn into the shared `chat_memory` window.
Fraser is skipped because it self-records (avoids double-recording). Result:
Kobe shows the WOD → it's in the shared window → Fraser reads it next turn and
can scale it.

**Why flag-gated OFF (`RAHAT_XAGENT_MEMORY`).** This changes what every agent
sees as conversational context — exactly the "big, review-worthy" class you said
to do *with* you. It's implemented and tested but inert until you enable it, so
prod is unchanged. Enable: add `RAHAT_XAGENT_MEMORY=1` to `.env`, kickstart.
Rollback: drop the line, kickstart.

**Note / open question for you:** the cleaner long-run design is to centralize
*all* turn recording in Miya (including Fraser's) and delete `composer._record_turn`,
so there's exactly one writer. I kept the bounded version (skip Fraser) to avoid
touching the composer's memory path unsupervised. Flag this if you want me to do
the centralization — it's a small, satisfying cleanup.

## E — weights grounded in the programming's scheme (fixed)

**Symptom.** WOD: *"Start ~60% of 1RM Power Clean … ~80% by the final sets."*
Fraser: *"assuming a standard working weight of 70-75% … 45 kg."* It hedged with
a generic default instead of using the scheme that was stated.

**Root cause.** Two layers: (1) Fraser often didn't have the WOD in context (bug
B), and (2) even with it, the composer directive only said "compute from the 1RM"
— nothing told it to honor a *stated* %/RPE/progression scheme over a default.

**Fix.** Composer directive #3 now: honor the programming's own loading scheme
(% of 1RM / RPE / "build to X%") applied to the recorded 1RM; do **not**
substitute a generic default or hedge with "assuming a standard working weight";
if the lift's 1RM isn't on file, say which 1RM is needed rather than guess. The
behavioral payoff fully lands once B is enabled (so Fraser sees the WOD). Pinned
by a prompt-contract test.

## F — cool-down is canned, never personalized (diagnosed, your call)

**Symptom.** "I did a Zone-2 run today, how should I cool down?" → a generic
recovery list. "Is this cool-down specific to my mobility?" → **the exact same
text**. It never adapts to your profile (the hunch, neck/trap tension, BP,
lower-body stiffness), and re-asking can't change it.

**Root cause.** The dispatcher's `post_recovery` route (`_POST_RECOVERY_RE` →
`handle_post_recovery`) intercepts anything containing "cool down" / "recovery
routine" and returns a **static string**. Both turns matched it, so both got the
identical block. This is the textbook ADR-011 smell: a deterministic route making
a coaching judgment (what a cool-down should be) that belongs to the model.

**Why I didn't just rip it out.** It's a routing-ownership decision, and there
are three defensible answers — and I don't want to pick one for you unsupervised:

1. **Fraser composes it.** Cool-down is coaching → route recovery questions to
   Fraser's composer, which already addresses pain/mobility/BP per profile. Most
   ADR-011-correct; costs an LLM round-trip; needs Miya to route "cool down" to
   Fraser (today the dispatcher claims it for Kobe).
2. **Kobe-LLM.** Keep it with Kobe but compose against the profile instead of a
   static string.
3. **Keep canned, but personalize the static block** and make the follow-up
   ("is this specific to me?") actually re-render with profile cues. Cheapest;
   least flexible.

**My recommendation:** (1), behind `RAHAT_COOLDOWN_LLM` default OFF — gate the
`post_recovery` (and likely `pre_fuel`, `breathing`) canned routes so they fall
through to Fraser when the flag is on. That's the same deterministic-shell /
LLM-core move as the rest of ADR-011. Say the word and I'll implement it with
tests the same way as A/B/E.

## Bonus: direct-agent addressing (built, since you asked mid-thread)

`@fraser what weights today` / `/fraser …` (and `@kobe`, `@huberman`, legacy
`@the_scientist`) now route straight to that agent, skipping Miya's classifier
(`2144ea1`). A slash that *isn't* an agent (`/pace`, `/week`) still reaches
Kobe's slash dispatcher — no existing command regressed. Flag:
`RAHAT_AGENT_ADDRESS` (default ON). This also gives you a manual override when
routing guesses wrong.

## How to review

```
git checkout feat/agent-control-plane-fixes
git log --oneline eabae6a..HEAD        # 5 commits, one concern each
python -m tests.run_all                 # green (after: pip install -r requirements-dev.txt)
```

Commits, each independently revertable:

- `e73ec5b` refactor(adr-012 M0): kill the plan-tool NL round-trip
- `2144ea1` feat(adr-012): explicit agent addressing (@agent / /agent)
- `9f79f62` fix(#A): 'workout for <day>' surfaces the WOD
- `0c10134` feat(#B/C/D): mesh-level conversation memory (flag-gated OFF)
- `63d2a67` fix(#E): Fraser grounds weights in the programming's scheme

**Flags to flip after review** (in `.env`, then `launchctl kickstart -k
gui/$(id -u)/com.rahat.miya`): `RAHAT_XAGENT_MEMORY=1` (turns on B/C/D — the big
one), `RAHAT_AGENT_ADDRESS` already defaults ON. A and E are live the moment you
merge (no flag).

**Live smoke once enabled:** message the bot "what's the workout for Tuesday"
(should show the WOD now), then "@fraser how should I scale it?" (Fraser should
scale *that* WOD at the WOD's own % scheme, not a generic 75%).
