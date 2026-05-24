# Rahat — PMO Operating Model (15-20 min/day owner)

**Owner:** Modern Builder
**Constraint:** 15-20 minutes/day, async-first, agents do the work
**Status:** v1 — 2026-05-18

---

## 1. The Problem This Solves

You don't have hours to build, debug, or QA. You have 15-20 minutes/day, split between **a morning dispatch** and **an evening review**. The architects can build all day; *you* are the bottleneck for validation, decisions, and direction.

Without a system, you become the failure mode: agents wait on you, regressions slip, the user-facing experience drifts from the test-facing one (as happened with Fraser Days 1-8). The PMO is the loop that prevents this.

---

## 2. The Five Roles (each is a cowork thread you spin off)

| Role | Purpose | Time pattern |
|---|---|---|
| **PMO** | Meta-orchestrator. Synthesizes overnight reports, drafts your morning dispatches, runs the weekly retro. The system itself. | Long-running thread; updated daily |
| **PM (per agent)** | Owns ONE agent's roadmap: Fraser-PM, Kobe-PM, Huberman-PM. Translates user goals → tickets → architect prompts. | One thread per agent; days |
| **Architect (per agent)** | Builds the next layer. Fraser-Architect, Kobe-Architect, etc. Already exists for Fraser + Kobe. | One thread per agent; days |
| **Test Lead** | Validates from the **user's perspective** every day. Runs the daily user-facing test. Writes the regression that catches the next bug. **Does NOT build.** | Long-running thread; daily |
| **CMO** | Campaign, posts, brand consistency. Off the daily critical path. | Weekly cadence |

**Key insight:** Test Lead is the role that doesn't exist yet, and it's the one that fixes the Fraser gap. The architect builds; the test lead validates. They should not be the same thread.

---

## 3. The Daily Rhythm

### The Delegation Chain (critical — read this first)

The PMO **does not write substantive prompts**. It is a router, not an author. The flow is:

```
You wake up
  ↓
You ping the PMO thread: "morning, kick off today"
  ↓
PMO reads overnight state, then ASKS:
  ├── Chief Architect thread → "Give me today's prompt for Fraser-Architect"
  ├── Fraser-PM thread → "Give me today's tickets / user-facing test target"
  ├── Test Lead thread → "Give me today's user-facing test"
  └── (if relevant) CMO thread → "Anything queued for posting?"
  ↓
Each specialist drafts their own prompt
  ↓
PMO bundles all four, sends back to you in ONE message
  ↓
You paste each prompt into its own cowork thread, walk away
```

The PMO's value is that it *knows who to ask and synthesizes the responses*. The Chief Architect knows the codebase; the per-agent PM knows the agent's roadmap; the Test Lead knows yesterday's failure. None of that knowledge lives in the PMO — the PMO routes.

### Morning (5-7 min, async dispatch)

What you do:
1. Open the **PMO thread** (continued from yesterday). Send: "morning, kick off today" (10 sec).
2. PMO reads overnight reports, then asks each specialist thread for today's prompt (PMO does this for you — it's async too).
3. When PMO has all the drafts back, it sends you ONE message with the bundle (1-2 min wait, but you can be making coffee).
4. You skim, approve or push back on any draft, then paste each one into its respective agent thread (3-4 min).

Then walk away. The agents work async during your day job.

### Evening (10-15 min, sync review)

What you do:
1. Run the **user-facing test** the Test Lead prepared (5 min). This is the gate: pass or fail.
2. Read each agent's session report — PMO has summarized them to one-paragraph each (3 min).
3. Send 1-2 line replies to blocked agents OR approve the PMO's drafted answers (5 min).
4. (Optional) Update the PMO thread with anything you want carried forward (2 min).

That's it. 15-20 min total. The PMO holds everything else.

### What the agents do during the day

- **Architects** keep building the next layer per the morning dispatch.
- **PMs** refine the next round of tickets based on architect progress.
- **Test Lead** runs validations as new code lands, writes regression pins, flags failures.
- **PMO** observes all of the above, synthesizes for your evening review.

---

## 4. The Weekly Rhythm (Friday 15-min retro)

One Friday a week, replace the evening review with a 15-min retro in the PMO thread:

1. What user-facing tests passed this week? Which failed?
2. Which agents shipped vs. churned?
3. What's the one bet for next week?
4. (If a campaign post is scheduled) — approve or push back.

The PMO drafts; you approve or redirect. Don't write retros from scratch.

---

## 5. The Single Most Important Rule

**Every day must have ONE user-facing test as the success criterion.** Not "tests green." Not "feature shipped." A test the user (you) runs by hand at 9pm and either says "this works" or "this doesn't."

This is the rule that prevents the "two definitions of live" gap that took Fraser to Day 8 without a real card. The Test Lead's job is to write that test every day and make sure it actually runs against actual production.

If you don't run the user-facing test in the evening, that day didn't ship — no matter what the architect's report says.

---

## 6. Failure Modes To Avoid

- **You become the QA loop.** If your evening review involves you debugging, the system is broken. The Test Lead should hand you a clear pass/fail with a one-paragraph reproduction.
- **Architects sprint past validation.** If Day N+1 starts before Day N's user-facing test passed, you accrue invisible regressions. Test Lead has veto over "we shipped Day N."
- **PMO drift.** If the PMO thread gets reset or loses continuity, the operating system collapses. Keep it as a single long-running cowork thread you return to daily.
- **Roles collapse into each other.** If the architect is also testing, you have no validation. If the PM is also architecting, the architect has no spec. Keep the threads disjoint.
- **Skipped retros.** Friday retros are how you adjust course. Skipping them means drifting toward "build more agents" when the right answer might be "validate what we have."

---

## 7. Tomorrow's Validation Target (use case 1 in the new model)

**The first user-facing test under this operating model:** ask Fraser for today's workout. Specifically:

> "What's today's workout?"

Expected output: a Workout Card adapted from today's SugarWOD entry (Monday May 18, "Bench Press 1RM" strength piece + "Primer" warmup + whatever WOD is on the day), with:
- Bench Press strength block at 1RM-based working weight prescription (with ramp-up sets)
- Postural cues (Hunch reset, HBP cue)
- Predicted burn for the day
- Attribution to source: "From SugarWOD — Bench Press 1RM"

**Pass criteria (Test Lead encodes these):**
- [ ] Fraser was actually invoked (not Kobe)
- [ ] Card cites source SugarWOD entry
- [ ] Working weight is computed from your stored 1RM (not synthesized)
- [ ] Card has all 4 sections (Warm-up / Strength / WOD / Cool-down)
- [ ] HBP cue present (you have HBP — non-negotiable)
- [ ] One row landed in `memory_entities` with `agent='fraser'` and `type='fraser_workout'`

**Fail handling:** if it fails, the Test Lead writes a one-paragraph repro AND a regression test pin file. Fraser-Architect picks up the pin tomorrow morning and fixes. You don't debug.

---

## 8. Cadence Forecast

If this operating model holds:

- **Week 1 (this week):** validate Fraser end-to-end. Pass = first real card. Fail = one specific bug shipped to the architect each day.
- **Week 2:** Fraser stable. Start same loop for Huberman (which you haven't yet validated either, I'd bet).
- **Week 3-4:** Phase 1B (Ustad, Montessori) starts under the same model.
- **Week 5+:** CMO posts the first campaign piece (Post 1, substrate thesis) — only after the user-facing tests are reliable enough that you can point at a real card in a real post.

If the user-facing test fails three days in a row on the same gap, escalate to a Friday retro: maybe the spec is wrong, maybe the agent isn't worth building, maybe you're solving a problem that doesn't exist.

---

## 9. What This Document Replaces

- The "Day N report" pattern as the unit of progress. Reports stay (they're history), but the unit of progress shifts to **user-facing tests passed per week**.
- The "architect sets the pace" model. The Test Lead sets the pace via what passed yesterday; the architect catches up.
- The "I'll catch up on weekends" anti-pattern. You don't catch up. The agents do.

*— Update this doc weekly during the Friday retro. The operating model is itself iterative.*
