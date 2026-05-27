# Mesh Architecture Review — Response to the 2026-05-25 Audits

**Author:** Chief Architect pass. **Date:** 2026-05-24.
**Inputs reviewed:** `FRASER_KOBE_TURN_BY_TURN_AUDIT` (90-turn routing audit) and
`FRASER_KOBE_JOURNEY_AUDIT_2026-05-25` (goal-overlap / journey audit).
**Status:** Proposal only. Per the standing mandate I have **not** changed any
production code, flipped any flag, or started the 20-agent refactor. This document
is the "propose first" half. Nothing below ships without your explicit go-ahead.

---

## IMPLEMENTATION STATUS (2026-05-25) — landed in the working tree, awaiting your commit

Six fixes are implemented and tested in the working tree (NOT committed,
NOT merged, NO flag flipped — per the standing mandate). Full suite green:
**unit 28 · contract 798 (+17 new) · eval 101 · adversarial 14 · regression 17**,
zero regressions against the pre-change baseline.

| Bug | Fix | Files | Test |
|---|---|---|---|
| **E** nudge spam | per-day cap (`WALK_NUDGE_DAILY_CAP=2`) on SENT walk nudges | protocols.py, state.py (`nudge_count`), handler.py | `test_2026_05_25_walk_nudge_daily_cap.py` |
| **B** plan ignores weekly target | `replan_week` rescales daily ideals to sum to a committed weekly target (commitment-gated → tier-default weeks untouched) | state.py | `test_2026_05_25_weekly_target_rescales_plan.py` |
| **C+G** contradictory pace / cosmetic missed-day | one week-pace formula (`expected_week_burn_to_date`); `_prorated_week_target` delegates to it; recalibration verdict is pace-aware (`behind_pace`) so "ahead/buffer/on-track" never shows when behind pace-to-date | state.py, handler.py | `test_2026_05_25_pace_verdict_consistent.py` |
| **A** no inverse projection | `project_goal_eta(target, intake, burn)` → ETA; sign-aware; tool + schema + registry + coach hint | tools.py, coach_system.py | `test_2026_05_25_project_goal_eta.py` |
| **parse_request** negation/dupes | dropped brittle positive-focus inference; kept word-boundary, deduped avoid-flags; LLM reads raw text | composer.py | `test_2026_05_25_parse_request_negation.py` |
| **A4** cool-down hijack | `RAHAT_COOLDOWN_LLM` (default OFF) makes canned `post_recovery`/`pre_fuel` routes yield to the LLM path | core/dispatcher.py | `test_2026_05_25_cooldown_yield_flag.py` |
| **A3** goal → weekly burn (DYNAMIC) | `weekly_target()` now derives the weekly burn from the active committed goal ("X lbs by Y date") under the *hold-intake / flex-burn, capped* policy you chose; recomputed from current weight each call (via `compute_goal_plan`); flows into the daily plan via the Bug B rescale. Explicit weekly commitment still outranks it. Flag `RAHAT_GOAL_DRIVEN_TARGET` (default OFF). | state.py | `test_2026_05_25_goal_driven_weekly_target.py` |

**Deliberately deferred (need you / too risky unattended):** the capability
fallback router (A1) and agent contract (A5) — the fenced 20-agent refactor;
the multi-week progression engine; NL pain capture and wiring the dormant
Fraser calculators into the composer (both alter the live LLM prompt — safer to
land with you watching); and flipping the three feature flags on (live-flag flips
are yours): `RAHAT_XAGENT_MEMORY`, `RAHAT_COOLDOWN_LLM`, and
`RAHAT_GOAL_DRIVEN_TARGET` (the goal→burn loop — review the cap policy, then flip).

**Note on direction:** Bug B's fix also partially mitigates Bug C — once the
daily plan sums to the real weekly target, the "remaining plan over-covers the
goal" illusion that produced the false "ahead" shrinks. The two fixes compound.

---

## ADDENDUM (2026-05-25) — the calorie/goal regression cluster re-orders the plan

A second live transcript (the "199 lbs → 196 by 06/06" session) surfaced a cluster
of calorie/goal bugs and forced me to re-baseline. **Key re-baseline finding: the
goal engine I and the audits described as "missing" actually exists and is fairly
rich.** `compute_goal_plan(target_lbs, target_date)` lives at
`agents/the_scientist/tools.py:942`, is in the tool registry, computes a *required*
lb/wk rate, compares it to the locked 0.75 lb/wk, and returns an `options` array.
Goals are set via natural language and committed (the morning brief shows "Goal:
196 lbs by 2026-06-06 (committed)"). So my earlier T6 ("no set-goal path") and the
"seeded 176, goal→burn loop doesn't exist" framing are **superseded** — the loop
exists; its *outputs aren't consistently consumed downstream*. That's a different,
cheaper class of fix, and it moves to the front of the line.

### The bug cluster (all verified against current code)

| # | Symptom in the transcript | Root cause (verified) | Class |
|---|---|---|---|
| **B** | "Replan … assuming 6000/wk" → header says **6,000** but days still sum to **7,100** | Daily targets come from the `DAY_TYPE_BY_TIER` constant (`hammer`: cf 1300/z2 1400/rest 600); `replan_week` never scales them to the weekly target — the gap is dumped into a "NEAT" line (`state.py` `day_type_target`, `replan_week`, `handle_show_plan`) | **Regression / wiring** |
| **C** | Hourly check says "**515 behind**"; Tuesday morning brief says "**ahead of pace, comfortable buffer**" — same week | **Three** different pace formulas: intra-day prorate (`_prorated_day_target`), week-second prorate (`_prorated_week_target`), and absolute-vs-remaining-plan (`compute_week_recalibration`). They disagree by construction | **Regression / SSOT** |
| **G** | "Missed: Mon CrossFit. Treating as rest days." but the math still looks off | "Treating as rest days" is **prose only** — the plan row keeps its `cf` target, so remaining-plan math overstates make-up capacity and feeds C's false "ahead" verdict (`handle_recalibrate`, `detect_missed_workouts`) | **Regression** |
| **E** | Pace nudges at 5/6/7/8 PM — 4 in 4 hours | Per-hour dedup only (`walk_17`, `walk_18`, …); **no per-day cap or cooldown** between nudges (`maybe_walk_nudge`, `nudge_log`) | **Regression / tuning** |
| **A** | "Given I burn 6000/wk & eat 2250/day, when do I hit 197?" and "When will I get to 196?" → can't; parrots the committed date | `compute_goal_plan` is **forward-only** (target+date → burn). No inverse (intake+burn → date) and no trajectory projection from actuals | **New use case** |
| **D** | "WOD for Wednesday" returns a hard CrossFit session on a day labeled "Active rest, 600 kcal"; user suspects hallucination | Fraser's WOD path reads **stored** SugarWOD sync (not LLM-generated → no hallucination there). But `day_type` (tier template) and `gym_label` (synced WOD) are assigned **independently**, so a "rest day" can carry a hard WOD and a wrong 600-kcal target. Hallucination risk remains only if a **WOD query routes to Kobe** instead of Fraser | **Regression (incoherence) + routing** |

Two refuted items, so they don't get fixed by mistake: the `/week` "remaining days"
math is **correct** (`days_left = 7 - now.weekday()`, inclusive), and Fraser's WOD
content is **not** hallucinated on its own path.

### The theme: one Energy Ledger, consumed everywhere (this is the new #1)

Every bug above is the same shape — **calorie/goal numbers are computed in multiple
places that don't agree.** The weekly target comes from a tier constant or
`compute_goal_plan`; the daily targets come from a different tier table; pace is
computed three ways; missed-day handling is cosmetic; and the LLM narrates goal
math on top. The fix is not "add more math" — it's **consolidate the math that
exists into one deterministic Energy Ledger** that:

1. owns the goal (target weight, date, **direction** — cut/gain/maintain — and a
   variable, guard-railed rate; `compute_goal_plan` already computes the required
   rate, so extend it, don't replace it);
2. supports the **inverse** (intake + burn → projected date) and trajectory
   projection (bug A);
3. distributes the weekly target into per-day targets that **actually sum to it**
   (bug B);
4. exposes **one** pace function consumed identically by `/week`, the hourly
   nudge, and the morning brief (bug C);
5. applies missed-day adjustments to the **numbers**, not just the prose (bug G);
6. is the **only** source the LLM narrates from — the model phrases the ledger's
   numbers and never invents its own.

This is Bucket A from §2 done right, and it sharpens the answer to your original
question: **the deterministic math is absolutely needed here — the bug is that
today's determinism is fragmented and partly overridden by LLM prose, not that
there's too much of it.** Contrast with routing/intent determinism (the other
audit's regex router), which remains the wrong direction. Math determinism →
*consolidate and make authoritative*; intent determinism → *shrink*.

### Revised wave order (supersedes §11 for sequencing)

- **Wave 0 (unchanged):** xagent-memory flip, cool-down yield, parser shrink.
- **Wave 1 — Energy Ledger consolidation (NEW #1):** fix B (scale daily to weekly),
  C (one pace function), G (apply missed-day to the math), E (per-day nudge cap).
  These are mostly *deletion and unification* of duplicated logic — high trust,
  closes the loudest daily pain. Land each with an xfail→green regression test.
- **Wave 2 — Ledger extensions:** bug A (inverse projection + trajectory), and
  reconcile day-type vs synced WOD so a "rest day" with a hard WOD adjusts its
  calorie target or relabels (bug D incoherence). Confirm WOD queries route to
  Fraser, not Kobe (bug D routing — ties to A1).
- **Wave 3+ (unchanged):** the dormant calculator wiring (A2), capability fallback
  (A1), agent contract (A5), new agents.

The old A3 ("build the goal→burn loop") is **retired** — replaced by "consolidate
and extend the loop that already exists," above.

---

## 0. Bottom line — answering your actual question

You asked: *"I don't know if all the deterministic stuff is needed."*

**You're right to doubt it. The two audits pull in opposite directions, and the
one that wants *more* determinism is pointing the wrong way.**

- The 90-turn audit's headline fix — a **deterministic design-intent router** (a
  regex layer that recognizes "design / scale / substitute / cool-down / what
  weights" and forces the message to Fraser regardless of the LLM) — is the
  *wrong layer*. Intent recognition is intelligence, not substrate. A growing
  regex table of phrasings is exactly the `O(phrasings × agents)` bespoke
  pipeline the 20-agent north star is trying to delete. Building it would make
  the mesh *harder* to scale, not easier, and it duplicates the job the
  classifier already does.

- The journey audit's headline fix — a **deterministic goal→burn→daily-target
  loop** and **deterministic weight/calorie math** — is the *right layer*. That's
  arithmetic that must be exact, which is precisely what ADR-011 says stays
  deterministic. And here's the kicker the audits missed: **most of that math
  already exists** in `agents/fraser/tools.py` (`compute_target_weight`,
  `compute_predicted_burn`). It's just not wired into the live composer path.

So the honest answer is a split decision: **remove or shrink the deterministic
*intent* layer (it's causing today's bugs), and complete the deterministic *math*
layer (it's mostly built and dormant).** The resilience problem the first audit
correctly identified — Fraser is unreachable when Gemini is down — gets solved
with a *capability-scored fallback* (one mechanism for all 20 agents), not a
per-intent regex wall.

Everything else below is detail in service of that thesis.

---

## 1. Verification ledger — what I confirmed, what I corrected

I re-ran the load-bearing claims against the real code rather than trust the
audits (or my own sub-agents). Most claims hold. Four did not survive contact.

### Confirmed (verified against source)

| Claim | Verdict | Evidence |
|---|---|---|
| Kobe's `dispatcher.dispatch()` runs **before** `_should_delegate()` | ✅ TRUE | `agents/the_scientist/handler.py` route() order (dispatch → slash → delegate) |
| Fraser has `triggers = []` (never matches the regex tier) | ✅ TRUE | `agents/fraser/agent.py:120` |
| Tier-3 trigger fallback defaults to `_AGENTS[0]` (Kobe) when nothing matches | ✅ TRUE | `core/miya.py` `_route_via_triggers()` |
| Cool-down route returns a **static** block, no personalization | ✅ TRUE | `handle_post_recovery()` + `_POST_RECOVERY_RE` in `core/dispatcher.py` |
| `RAHAT_XAGENT_MEMORY` defaults **OFF** | ✅ TRUE | `core/miya.py` `_xagent_memory_enabled()` |
| `chat_memory` TTL = 4h | ✅ TRUE | `core/chat_memory.py:66` |
| Fraser gets **today-only** kcal target; no forward-day path | ✅ TRUE | `core/kobe_bridge.py:83` (`datetime.now().weekday()`) |
| Weight goal is **invisible** to Fraser | ✅ TRUE | `AthleteProfile` has no goal field; Fraser delegates weight Qs to Kobe |
| Seeded goal is a **cut to 176/185 lbs**, not 198 | ✅ TRUE | `protocols.py:53-58` (`INTENT_TARGET_KG=80.0`, `INTERMEDIATE=84.0`) |
| NL pain is dropped; only `/pain` (or the flag-gated planner) persists | ✅ TRUE | `core/pain_state.py` write path; composer only *reads* |

### Corrected (the audits or my sub-agents were wrong)

1. **The deterministic weight & calorie calculators are NOT missing — they exist
   and are dormant.** The journey audit lists "no deterministic weight/% calculator"
   and "no deterministic calorie estimator" as *gaps to build*. In fact
   `agents/fraser/tools.py` already has `compute_target_weight(lift, pct, one_rm)`
   (snaps to the plate grid) and `compute_predicted_burn(card)` (per-movement
   kcal model). The Day-1 handler path used them; the Day-11 `composer.design_session()`
   path does **not** — it tells Gemini to do the arithmetic in prose. **This is a
   wiring regression, not a missing capability.** Materially cheaper to fix than
   the audit implies.

2. **The `parse_request` negation bug is real** (the journey audit is right; my
   verification sub-agent wrongly marked it FALSE). The parser is plain substring
   matching (`if needle in text`, composer.py:82-99). "I've already done
   **deadlifts** and back **squats**, don't have those" sets `deadlift_focus` and
   `squat_focus` — it flags the movements you want to *avoid* as the *focus*. No
   negation handling exists for positive-movement needles.

3. **The duplicate-flag bug is real and easier to trigger than stated.** "no
   running" *contains* the substring "no run", so a single phrase matches both
   `("no run", "no_running")` and `("no running", "no_running")` and appends
   `no_running` twice. No dedup. It doesn't even need two phrasings.

4. **The "design-intent router" is not as absent as the 90-turn audit's "2/90"
   implies.** Kobe's `_should_delegate()` has **20 patterns** (13 Fraser, 7
   Huberman), not two. The "2/90" is how many of the 90 transcript phrasings the
   net happens to catch — the net exists, it's just narrow and brittle.
   Separately, `_should_delegate()` is **Kobe-internal**: it only runs *after*
   Miya has already routed to Kobe, so it is not a Miya-level deterministic route
   to Fraser. That nuance matters for where any fix belongs.

### One conceptual flag the audits both missed

The "goal → burn X calories" framing assumes a **cut**. Your stated target is
**198 lbs** and your last logged weight was ~196 lbs — that's roughly maintenance
or a slight *gain*. For a gain, "burn more" is the wrong lever (you'd adjust
intake/surplus, not burn). So before anyone builds a goal→burn loop, the loop has
to be **sign-aware**: deficit-driven for a cut, surplus/volume-driven for a gain,
maintenance otherwise. Hard-coding "more burn" would actively fight a 198 goal.
This is a design decision for you, not something to assume.

---

## 2. The determinism question, framed as a rule you can reuse

Sort every "should this be deterministic?" decision into three buckets. This is
the lens I'd apply to all 20 agents, not just this fix.

**Bucket A — Substrate (deterministic, keep/complete).** Math that must be exact,
state, persistence, safety, time. *Weight from %1RM, calorie estimates, the
goal→energy-balance math, the pain store + TTL, quiet-hours charter, plate
snapping, "what day is it."* These are facts, not judgments. ADR-011 already says
these stay deterministic. **Action: complete this bucket** — it's under-built
(dormant calculators) more than over-built.

**Bucket B — Intelligence (LLM, shrink/remove determinism here).** Recognizing
intent, parsing free text into structure, choosing tone, deciding "is this a
recovery request." *The proposed design-intent router, `parse_request`'s
focus/negation detection, the canned cool-down block's content selection.* Every
place we've encoded judgment as regex is either brittle (the 20-pattern
delegation net) or actively wrong (parse_request flipping avoid→focus, the
identical cool-down re-answer). **Action: stop adding here; remove what's causing
bugs.** This is the part of "all the deterministic stuff" you can safely cut.

**Bucket C — Resilience backstop (deterministic, but capability-data not
per-intent regex).** When the classifier is unavailable, the mesh must still route
sensibly instead of dumping everything on Kobe. The right backstop scores the
message against each agent's **declared description/capability tags** — one
scorer that works for all 20 agents and grows by *configuration* (a new agent
ships a description, not a regex patch). Plus `@agent`/`/agent` addressing (already
built) as the manual override. **Action: build one capability-scored fallback;
do not build the per-intent regex router.**

The 90-turn audit conflated B and C: it saw a real resilience hole (C) and
proposed to fill it with intent-regex (B). That's the core disagreement.

---

## 3. Architectural changes (the big rocks)

Ordered by leverage. Each is an ADR-sized decision; I'd want your sign-off on the
shape before writing code.

**A1 — Capability-scored fallback router (replaces the "design-intent router").**
Give every `Agent` a `capabilities` declaration (tags or a short capability
sentence; the `description` field is already required and non-empty). When the
classifier is down or empty, score the message against those declarations with a
cheap deterministic method (keyword/embedding overlap) and route to the best
agent instead of defaulting to `_AGENTS[0]`. *Why this and not regex:* it scales
as configuration for all 20 agents and removes the Gemini-down collapse without a
phrasing table. **Closes:** the 76/90 "classifier-only, no backstop" turns and the
88/90 Gemini-down collapse. *ADR needed.*

**A2 — Wire the existing deterministic math into the composer (Bucket A).** Make
`composer.design_session()` call `compute_target_weight` / `compute_predicted_burn`
and hand the LLM *computed* numbers to phrase, rather than asking the LLM to do
arithmetic. Keep the LLM for prose and movement choice. *Why:* ADR-011 says exact
numbers stay deterministic; the tools already exist. **Closes:** BUG-6 (unverified
weight/calorie math) for the structured cases. *Small-to-medium; mostly wiring.*

**A3 — Goal → energy-balance → daily-target loop (sign-aware).** A deterministic
function: active weight goal + current weight + target date → required weekly
energy balance → today's contribution, surfaced into the block Fraser sizes to.
Must handle cut/gain/maintain (see §1 flag). Pair with a "set my goal" command
(see T6) so the 198-vs-176 mismatch is fixable by the user, not by editing
constants. *Why:* this is the Kobe↔Fraser "overlap" you imagined; today it's three
decoupled numbers. **Closes:** J14 (the headline journey). *ADR needed.*

**A4 — Cool-down (and pre-fuel/breathing) routes yield to the composer.** Gate the
canned `post_recovery` / `pre_fuel` / `breathing` dispatcher routes behind
`RAHAT_COOLDOWN_LLM` (default OFF, flip after smoke) so recovery asks fall through
to Fraser's personalized composer instead of a static block. *Why:* this is
Bucket-B determinism that's hijacking Fraser's job. **Closes:** the 10/90
canned-cool-down turns + transcript bug #F. *Small; same flag-gated pattern as
the mesh-memory work.*

**A5 — Agent contract completion (`system_prompt` + `tools[]`).** This is the
ADR-012 work already specced. It's the substrate that makes A1 (capabilities) and
"onboarding = configuration" real. *Why:* it's the precondition for 3→20. *Do not
start without explicit go-ahead — this is the refactor you fenced off.*

**Deliberately rejected:** the standalone deterministic design-intent regex router.
It's superseded by A1 and contradicts the north star.

---

## 4. Tactical fixes (small, high-trust, land each with a regression test)

These are the cheap wins. Every one is additive and reversible.

| ID | Fix | Bucket | Notes |
|---|---|---|---|
| T1 | **Forward-day target** — `kobe_bridge.today_target()` takes a `weekday_idx`/date arg; "tomorrow/Saturday" sizes to that day | A | BUG-1; small |
| T2 | **NL pain capture** — extract pain from natural language into `pain_state.report()` (LLM-extracted, deterministic store + 48h TTL) | A+B | BUG-2; LLM does extraction, substrate does the write |
| T3 | **Wire calculators into composer** (= A2, listed here as the concrete diff) | A | reuses `tools.py`; no new math |
| T4 | **Fix `parse_request` negation** — either add negative needles *or* (preferred) shrink the parser and let the LLM read raw prefs | B | demotes Bucket-B regex; see §6 note |
| T5 | **Dedup + non-overlapping needles** in `parse_request` | B | trivial; or fold into T4 by deleting the list |
| T6 | **"Set my goal" command** — `/goal 198 by <date>` updates the `intents` table instead of editing `protocols.py` constants | A | BUG-4; unblocks A3 |
| T7 | **Flip `RAHAT_XAGENT_MEMORY` on** after a smoke — the fix is merged but inert | — | BUG-5; config flip, not code |
| T8 | **Cool-down yield flag** (= A4 concrete diff) | B | flag-gated |

On T4/T5: the *cleaner* move, consistent with §2 Bucket B, is to **delete most of
`parse_request`'s preference list** and let the LLM read the raw request text
(which it already gets). The parser earns its keep only for the things that must
be structured and exact — minutes and kcal — not for "no running / bench focus,"
where it's currently producing wrong flags. I'd propose deleting the buggy half
rather than patching it. Your call; flagging because it cuts *against* adding
determinism, which is the direction you're questioning.

---

## 5. Functionality gaps (new capabilities the doc's coach has and Rahat doesn't)

These aren't bugs; they're missing features. Sized for the roadmap, not this week.

1. **Multi-week progressive program engine.** The doc runs a 10-week chest
   progression and a postpartum ramp; Rahat composes each session fresh with no
   "what week/day am I in." Needs durable program state + progression rules.
   *Largest build. Probably its own ADR.*
2. **Goal→burn closed loop** (= A3). The headline gap.
3. **Huberman NL ingestion.** Today HRV/sleep/"feeling weak" only influence the
   session if the LLM happens to read them from the 4h window. Needs structured,
   decaying capture (parallel to T2's pain capture) so scaling is durable and
   guaranteed.
4. **Pasted-WOD structured adaptation.** Blacklist substitution + weight calc are
   wired to the SugarWOD sync path, not WODs pasted into chat. Pasted WODs ride
   entirely on the LLM today.
5. **New mesh agents for orphaned intent classes.** ~10/90 turns belong to domains
   no agent owns: **Life/Calendar** (time available, toddler, "home by X"),
   **Nutrition/Foodie** (rest-day diet, intake), and arguably **Route/Terrain**
   (elevation, zone-2 viability). See §10.

---

## 6. Evals to write (real-Gemini smokes — your Mac, not CI)

These exercise the LLM and so can't run hermetically. They're the things neither
audit could test.

- **Routing fidelity over the full corpus.** For all ~90 (turn audit) + ~50
  (journey audit) phrasings, assert design/scale/cool-down → Fraser, lookups →
  Kobe, recovery-signal → Huberman. This is the misrouting your transcript caught;
  it needs a live-classifier smoke as the ongoing guard.
- **Weight-math drift.** For a pasted "% of 1RM" scheme, compare Fraser's
  prescribed kg against `compute_target_weight`; fail if drift > 2.5 kg. (After
  A2 this should be ~0.)
- **Calorie sizing band.** Assert Fraser states an estimate and it lands within a
  band of the requested target; cross-check against `compute_predicted_burn`.
- **Pain adaptation presence.** With an active ankle/neck pain, assert *every*
  section references the adaptation (the doc's "every cool-down addresses the
  flare" rule).
- **Cool-down personalization (post-A4).** "Is this cool-down specific to my
  mobility?" must change text on re-ask and reflect HRV/pain — not the static block.

Wire these as the "real-Gemini smoke" tier that runs on your Mac, not in CI, so
hermeticity is preserved.

---

## 7. Regression tests to write (hermetic, CI today; xfail→green per your convention)

Land each as `tests/regression_registry/test_2026_05_DD_*.py`. Several start as
**xfail** (they encode the bug) and flip when the fix ships — your existing
bug-to-test policy.

| Test | Pins | Lands as |
|---|---|---|
| `kobe_bridge` block contains daily **and** weekly target | wiring lock | green now |
| `target_for(weekday_idx)` uses the right day's row | T1 forward-day | xfail → green |
| NL "string pain behind my right ankle, remember it" → `pain_state.list_active()` non-empty | T2 | xfail → green |
| `parse_request("already did squats, don't have those")` → no `squat_focus` | T4 | xfail → green |
| `parse_request("no running")` → exactly one `no_running` | T5 | xfail → green |
| Capability fallback: classifier disabled, "design my workout" → Fraser (not Kobe) | A1 backstop | xfail → green |
| Cool-down precedence: "is this cool-down enough?" does **not** return the static block | A4 | xfail → green |
| Goal loop: moving target date closer raises required weekly burn + daily target | A3 | xfail → green |
| `/goal 198 by <date>` updates the active intent | T6 | xfail → green |
| Xagent-memory ON: Kobe prints WOD → "scale this" → Fraser's prompt contains it; plus an inverse test pinning flag-OFF as intentional | T7 | green now (both states) |
| Composer uses `compute_target_weight` (assert the tool is called) | A2/T3 | xfail → green |

**Corpus expansion (adversarial layer):** add the ~90 + ~50 real phrasings from
both audit transcripts to `tests/adversarial/corpus.json`, each labeled with
expected agent + a non-stub assertion. Those transcripts are gold-standard; they
should seed the corpus permanently. The harness in `outputs/journey_harness.py`
is reusable for this.

---

## 8. Docs to update

- **ADR-011** — add the three-bucket rule from §2 explicitly (substrate vs
  intelligence vs resilience-backstop). Right now "deterministic shell, LLM core"
  is being read by one audit as "more routing regex," which is a misread the ADR
  should foreclose.
- **ADR-012** — fold in A1 (capability declaration as the fallback-router input)
  and confirm A5 sequencing; note that the design-intent regex router was
  considered and **rejected** in favor of capability scoring (record the
  decision so it isn't re-proposed).
- **New ADR — goal→energy-balance loop (A3)** — including the sign-awareness
  decision (cut vs gain vs maintain).
- **New ADR — capability-scored fallback router (A1).**
- **`FRASER_GEMINI_CHAT_REFERENCE.md`** — note which behaviors are LLM-only vs
  deterministic after A2/A3 land, so future readers know what's guaranteed.
- **CLAUDE.md / runbook** — document the new flags (`RAHAT_COOLDOWN_LLM`,
  the goal command) and the "real-Gemini smoke" tier location.
- **Memory note** — record that the seeded North Star (176/185) ≠ stated goal
  (198) until T6 lands, so it isn't mistaken for intentional.

## 9. Docs to clean up

- **The two 2026-05-25 audits overlap heavily** (both re-derive the cool-down
  hijack, NL-pain, forward-day, xagent-memory findings). Once this review is
  accepted, **collapse them into one canonical "Mesh Audit 2026-05-25"** and link
  this response from it. Keep one source of truth for the bug list.
- **Correct the stale claims** in the journey audit: mark "no deterministic
  weight/calorie calculator" as *exists-but-unwired* (§1 correction #1), and the
  90-turn audit's parse-bug section is right but its "deterministic router"
  recommendation should carry a pointer to this rejection.
- **`TELEGRAM_REVIEW_2026-05-24.md`** — its item #F (cool-down) is now subsumed by
  A4; add a back-reference so it's not tracked as a separate open item.
- **Sweep the regression registry** for any test still asserting the *old* NL
  round-trip or pre-ADR-012 behavior so the suite reflects current contracts.

---

## 10. Agents to spawn

Two readings of "which agents" — I'll answer both.

### (a) New *mesh* agents (the product)

Justified by orphaned intent classes in the transcripts, and on-strategy because
each is *configuration* (description + system_prompt + tools), not a pipeline —
which is exactly the 3→20 thesis and a great forcing function for A5.

- **Life/Calendar agent** — owns time-available, family/toddler constraints,
  "home by X," "I only have 60 minutes." ~6-10 turns map here. Highest-value new
  agent; it gates a huge fraction of real journeys.
- **Nutrition/Foodie agent** — rest-day diet, intake, fueling. Pairs naturally
  with A3's energy-balance math.
- **Huberman — promote from stub** — it already "exists" in the mesh; give it real
  NL ingestion (gap #3) before adding net-new agents. Cheapest capability gain.
- *(Defer)* Route/Terrain agent — only 1-2 turns; not worth an agent yet.

### (b) *Sub-agents to spawn for the build work* (how I'd parallelize execution)

When you green-light implementation, I'd run these as parallel worktree agents so
they don't collide:

- **`code-review` agent** — independent review of each branch before merge
  (security/correctness), per your bug-to-test gate.
- **One `general-purpose` implementer per workstream**, isolated to worktrees:
  (i) tactical fixes T1/T2/T3/T5 (small, additive); (ii) A4 cool-down yield; (iii)
  A1 capability fallback; (iv) A3 goal loop + T6. Each ships with its regression
  tests.
- **`testing-strategy` / test-author agent** — builds the corpus expansion (§7)
  and the real-Gemini smoke tier (§6) in parallel with the fixes.
- **A verification `Explore`/`Plan` agent** for the A5 refactor design before any
  line is written.

I would **not** spawn implementers until you pick the wave (§11) — analysis is
done; execution waits on your word.

---

## 11. Sequenced roadmap (waves) — gated on your go-ahead

**Wave 0 — config flips + reversible removals (hours, no refactor):** T7 (flip
xagent-memory after smoke), A4/T8 (cool-down yield behind a flag), T4/T5 (shrink
the buggy parser). Each behind a flag or trivially revertible.

**Wave 1 — Bucket-A completion (small, additive, high trust):** T1 (forward-day),
T2 (NL pain capture), A2/T3 (wire the dormant calculators). All land with
xfail→green tests. This fixes the most-visible journeys with the least risk.

**Wave 2 — the headline loop:** A3 (goal→energy-balance, sign-aware) + T6 (set-my-
goal command). ADR first, then code. This is what makes the Kobe↔Fraser overlap
real.

**Wave 3 — resilience + contract:** A1 (capability-scored fallback) and then A5
(agent contract `system_prompt`+`tools[]`). A5 is the fenced-off refactor — needs
an explicit second go-ahead even after this review.

**Wave 4 — new capabilities/agents:** multi-week progression engine (gap #1),
Huberman NL ingestion (gap #3), pasted-WOD structuring (gap #4), then the
Life/Calendar and Nutrition agents (§10a) as the first config-only agents on the
A5 contract.

**My vote for the very next step:** Wave 0 + Wave 1 together (cheap, reversible,
closes the loudest transcript bugs), then write the A3 ADR while those bake. I'd
hold A1/A5 until you've confirmed the determinism framing in §2 — because if you
disagree with the three-bucket rule, A1's shape changes.

---

## Appendix — verified code references

- Routing order: `agents/the_scientist/handler.py` `route()` (dispatch → slash →
  delegate); `core/miya.py` Tier-0 address / Tier-1 slash→Kobe / Tier-2 classifier
  / Tier-3 `_route_via_triggers` (defaults to `_AGENTS[0]`).
- Fraser triggers empty: `agents/fraser/agent.py:120`.
- Delegation patterns (20): `agents/the_scientist/handler.py` `_FRASER_DELEGATION_PATTERNS`
  (13) + `_HUBERMAN_DELEGATION_PATTERNS` (7).
- Cool-down static block: `handle_post_recovery()`; `_POST_RECOVERY_RE` in
  `core/dispatcher.py`.
- Dormant calculators: `agents/fraser/tools.py` `compute_target_weight`,
  `compute_predicted_burn`; composer uses LLM prose instead
  (`agents/fraser/composer.py` `design_session` / `_SYSTEM_DIRECTIVE`).
- `parse_request` substring loop (negation + dup bugs): `agents/fraser/composer.py:82-99`.
- Today-only kcal: `core/kobe_bridge.py:83`.
- Goal constants (176/185): `agents/the_scientist/protocols.py:53-58`.
- Flags/TTL: `core/miya.py` `_xagent_memory_enabled()`; `core/chat_memory.py:66`.
- Pain store + 48h TTL: `core/pain_state.py` `report()`.

*Confidence: data-flow/wiring claims are verified by reading source. LLM-prose
quality claims (does Fraser compute the right kg today) remain inferred and are
exactly what the §6 smokes are for.*
