# Gap Matrix — old plane → new plane cutover (2026-06-10)

Plain-language version: this is what the new bot does, what the old
bot does, where they differ, and which differences MUST be closed
before we can safely flip the switch (P0), which we'd want closed
soon after (P1), and which are nice-to-have (P2).

---

## Big picture in one sentence

The new plane is a **smart router** that owns the intent classification
+ Miya's voice. For deterministic queries (90%+ of real traffic), it
**delegates to the old plane's Kobe / Fraser handlers** for the
actual work. So most "functionality" lives in shared code — what we're
really migrating is the routing brain and the Miya voice layer.

That means a gap is only a real cutover blocker if it's something the
new plane's routing brain MISSES, or something Miya's voice does WRONG.

---

## P0 — Cutover blockers (must fix before flipping to new bot)

### P0-1. WOD lookup still goes through Gemini paraphrase
**Old behavior:** `/plan` or "what's the workout for Wed" returns Sun's
verbatim WOD card. No paraphrase, no narrative.

**New behavior (today):** WOD lookups route via `kobe_route` (after
Bug I fix). Kobe's handler returns text. BUT for any orchestrate-path
WOD lookup (`@miya what's tomorrow's workout`), Gemini still gets a
crack at the `gym_wod` fact and may paraphrase.

**Why this is P0:** verbatim WOD is the #1 user pain point. Any
synthesis-layer freelancing here is a regression vs old plane.

**Fix plan:** in `orchestrator.handle`, after pulling `gym_wod` fact,
if the fact has content, **bypass the synthesizer entirely** and
return the WOD text in a thin Miya wrapper. Only synthesize when the
fact is empty/error.

**In simple language:** when the user asks for the workout, just SHOW
them the workout. Don't ask Gemini to "say it nicely."

---

### P0-2. Nudges default to OFF
**Old behavior:** morning brief at 6 AM, weekly reset on Sunday,
recovery + walk nudges fire automatically.

**New behavior (today):** `NEW_MIYA_NUDGES_ENABLED=1` is required to
enable. The .env file in your machine has it OFF (we set it during
Phase C dev). If we cut over today, your 6 AM brief STOPS.

**Why this is P0:** silent regression in daily life. You'd notice next
morning when nothing fires.

**Fix plan:** flip the default to ON in the runner. Keep the flag for
emergency disable. Update install_new_miya.sh to write NEW_MIYA_NUDGES_ENABLED=1
on install. Document in CUTOVER_SEQUENCE.md.

**In simple language:** the new bot's morning brief is asleep. Wake it
up by default; only turn it off if explicitly broken.

---

### P0-3. Charter check is generic (always "notify.user.reply")
**Old behavior:** Charter reviews each work order with its specific
`kind` ("coach.push_intensity", "fraser.workout.commit", "notify.user.nudge",
etc.). Policies like `hrv_red_blocks` only fire on relevant kinds.

**New behavior (today):** orchestrator calls `kobe_charter_check(kind="notify.user.reply", ctx={})`
for EVERY turn. Specific policies (hrv_red_blocks, quiet_hours,
fraser_1rm_increase_needs_green) never fire because they're scoped
to non-`notify.user.reply` kinds.

**Why this is P0:** the charter is a SAFETY layer. Today on the new
plane it's effectively a no-op for everything except quiet hours.

**Fix plan:** derive `kind` from the intent ("notify.user.nudge" for
morning brief, "fraser.workout.commit" when fraser_route, etc.). Pass
relevant ctx (current HRV, current 1RM, current time-of-day). Wire
through.

**In simple language:** the safety net is hanging slack. Tighten it
so the new bot can't blast a workout when your HRV is red.

---

### P0-4. Plan-mutation NL phrases that aren't pinned
**Old behavior:** "Wed for CrossFit", "tolerate partner", "rest on
Monday" — `_legacy_route` + `_try_plan_mutation` handle these.

**New behavior (today):** `_PLAN_MUTATION_RE` in delegate_classifier
covers all the patterns I checked, BUT the test coverage relies on
having seen the phrasing once. New variations like "skip Friday",
"swap Mon and Wed", "swap Mon with CrossFit" may not match the regex
even though they should reach Kobe.

**Why this is P0:** the user mutates plans constantly in real traffic.
Misses go to synth and get paraphrased.

**Fix plan:** expand `_PLAN_MUTATION_RE` to cover skip/swap/move/cancel
patterns. Add ledger-mined phrasings as tests.

**In simple language:** make sure "skip Friday" / "move Wed to Thu" /
"cancel today" go straight to Kobe, not Gemini.

---

## P1 — Should-have (close within 1 week post-cutover)

### P1-1. Verbatim WOD also for orchestrate path (defense-in-depth)
After P0-1 lands, add belt-and-suspenders: the prompt builder still
includes a SOURCE OF TRUTH marker for any gym_wod fact that survives
into synth. We've already done this; just verify Gemini honors it
under real load.

### P1-2. Multi-turn confirmation flow (the "Yes" bug, properly)
**Old behavior:** the old Miya occasionally asked clarifying A/B
questions ("Did you mean A or B?") with a 60-second TTL.

**New behavior:** chat_memory bridge (RAHAT_XAGENT_MEMORY=1) makes
"Yes" route via context, but there's no explicit pending-confirmation
state. The bot CAN ask a clarifying question but has no machinery
to remember it.

**Fix plan:** add `pending_clarification` table to signals store
with TTL. When orchestrator emits a clarification, persist it; on
next turn, check for a live pending and resolve. Keep it small —
the chat_memory bridge handles 90% of cases.

### P1-3. Explicit @huberman path
**Old behavior:** Kobe's `_should_delegate` routes recovery/sleep
queries to Huberman internally.

**New behavior:** `@huberman` and recovery queries route via
`kobe_route`, which then internally delegates. Works, but it's
ambiguous in the logs ("did Kobe answer or did Huberman?").

**Fix plan:** add `huberman_route` to native_client. Add Huberman
patterns to delegate_classifier. Log path=huberman_route explicitly.

### P1-4. Decisions ledger entries for delegated turns
**Old behavior:** every Kobe turn writes a row to `core.decisions`.

**New behavior:** when new Miya delegates via kobe_route, Kobe writes
its row AND the new Miya writes its own. Double-counting in eval +
analytics.

**Fix plan:** when delegating, skip new Miya's decision-log write.
Kobe's row is the authoritative one. Add an `originated_by` field
to the decisions row so we can tell "this came via new Miya."

---

## P2 — Nice-to-have (do later, or as time permits)

- P2-1. Old plane archive (move `agents/the_scientist/old_*` etc. to
  archived/). Cosmetic.
- P2-2. Architecture diagram refresh. The current diagram still
  shows old plane Miya as primary.
- P2-3. ADR-013 phase E doc: write the post-cutover ops runbook.
- P2-4. Compare harness flake (PF-007) — pre-existing test isolation
  issue, doesn't block cutover.
- P2-5. Mining script weekly scheduled task.

---

## NOT a gap (frequently miscategorized)

- **Fraser composer features** (pain blocks, mobility, athlete
  profile). All preserved — fraser_route calls the same composer.
- **Cool-down LLM yield.** `RAHAT_COOLDOWN_LLM=1` flag is preserved
  end-to-end through fraser_route.
- **Day-typo tolerance.** Bug I fix covers tommorow/tomorow.
- **Past-tense WOD lookup.** PF-003 fix covers "what was the workout."
- **/ fix slash whitespace.** PF-002 fix covers.
- **Signal isolation.** PF-005/006 fix covers.
- **Bug H arbitration ignored.** PF-004 fix suppresses contradictory
  summary; arbitration verdict block already in prompt.

---

## My priority for this 24-hour shift

1. **P0-1** (verbatim WOD bypass) — 1 hour
2. **P0-2** (nudges default ON) — 30 min
3. **P0-3** (charter kind derivation) — 1.5 hour
4. **P0-4** (plan-mutation regex expansion) — 1 hour
5. **P1-2** (pending_clarification table) — 2 hours
6. **P1-3** (explicit @huberman) — 1 hour
7. **P1-4** (decisions-ledger de-dup) — 1 hour
8. Regression tests for every fix — 2 hours
9. Full suite verification — 1 hour
10. Architecture + handoff docs — 2 hours
11. CUTOVER_SEQUENCE.md — 30 min
12. Archive stale files — 30 min
13. Reserve buffer for things that don't compile cleanly — 4 hours

P0-only would take ~5 hours. P1 + tests another 10. Docs + archive 3.
Buffer 4. Total ~22, fits in 24.

If anything starts taking 3x estimated, I'll cut P1-3 and P1-4 to
P2 and document.
