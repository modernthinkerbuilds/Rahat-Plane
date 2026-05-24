# Rahat Readiness Report — 2026-05-23

Autonomous hardening pass on Miya / Kobe / Fraser. Every change is behind a
green 5-layer test stack and committed incrementally.

## Verdict: READY to deploy (1 manual step)

All code is committed to `feat/fraser-day-11-15-conversational` (**7 commits
ahead of `origin/main`**). The live bot still needs **one restart** to load it
(I can't restart your launchd job from my sandbox):

```
cd ~/developer/agency/rahat
launchctl kickstart -k gui/$(id -u)/com.rahat.miya
tail -f vault/miya.log        # expect "Miya live", no import errors
```

## Test status (python -m tests.run_all)

| Layer       | Result |
|-------------|--------|
| unit        | 28 passed |
| contract    | 723 passed, 17 skipped, 9 xfailed |
| eval        | 101 passed, 1 skipped |
| adversarial | 14 passed |
| regression  | 17 passed |

The contract layer grew from ~551 → 723 and eval from 53 → 101 this arc, almost
all from new tests for the work below.

## Commits this arc (newest first)

1. `de5e031` chore: gitignore test-runner artifact + private-docs hygiene (#36)
2. `1dcdbb6` test(evals): Fraser/Kobe grounding + routing-fidelity evals (48)
3. `b202f3b` test(mesh): deep e2e suite + Fraser lookup backstop + today/named-day semantics
4. `7232a78` fix(kobe): plan edits persist via dispatcher + additive CF picks (#47/#48)
5. `4ebeae4` feat(fraser): conversational follow-ups + /pain + /profile + relative-day WOD
6. `438a818` feat(miya): chat_id through route ABI + _safe_route capability negotiation
7. `561b7e1` feat(fraser): 4-section composer grounded in profile/pain/bridges

## Gaps found & fixed

- **Plan edits silently no-op'd in prod (#47).** Pick/rest/replan/unavailable
  handlers only ran in the dead legacy router. Added `_try_plan_mutation` + a
  `plan_mutation` dispatcher route (placed last). "Mon for crossfit", "Wed rest",
  "replan" now persist.
- **"pick Sun for crossfit" wiped the week (#48).** It replaced the CF list with
  the single named day. Now single-day picks are additive; multi-day or
  "just/only" replaces; Z2-only picks don't wipe CF and vice-versa.
- **Lookup misrouted to Fraser got re-designed.** Added a runtime backstop:
  Fraser delegates day-specified workout LOOKUPS ("what is the WOD for Tuesday")
  to Kobe, while design requests stay with Fraser.
- **Composer regenerated on follow-ups** (the "hallucinating when I personalize"
  bug). Conversational mode answers "what weights?" against the existing session.
- **kobe_bridge ImportError** (calorie target lost), **relative-day WOD lookup**,
  **/pain + /profile input paths**, **time-bomb test** — all fixed earlier this arc.

## Decisions & deliberately-deferred items (NOT bugs)

- **"today" → Fraser, named-day/tomorrow/yesterday → Kobe.** "What's the WOD
  today" is your daily-driver design intent (the composer already folds in
  today's synced gym WOD); other days are schedule peeks. This resolves a
  long-standing ambiguity and respects the existing `test_fraser_delegation`
  contract.
- **Left the keyword-proxy xfails as xfail** (`test_fraser_description_does_not_
  claim_lookup`). Fraser's description carries "Defer to Kobe for… lookups"
  disclaimers that HELP the semantic classifier but trip a crude keyword-count
  test. Stripping them to satisfy the proxy would hurt real routing. The runtime
  backstop covers the actual risk instead.
- **#51 (feed behavioral transcript into composer voice) — deferred.** Output
  quality is already strong; this is a polish item, high token cost, low risk if
  skipped.
- **Untracked, intentionally not committed:** `FRASER_DAY_15_REPORT.md`,
  `evangelist-digests/`, `specs/RAHAT_PMO_OPERATING_MODEL.md` (your docs), and
  `outputs_perm_test.renamed` (a junk file from an earlier sandbox rename test —
  safe to `rm`; my sandbox can't delete it).

## Per-agent readiness

- **Miya (orchestrator):** Tier-1 slash → Kobe, Tier-2 classifier, capability
  negotiation (`_safe_route`) all verified end-to-end. chat_id threads from
  `route()` to the composer's memory. Legacy agents still dispatch.
- **Kobe (state/plan):** slash table (+ /pain, /profile), single dispatcher with
  the new plan_mutation + relative-day routes, additive picks, rest days — all
  persist and are covered by e2e tests.
- **Fraser (design):** 4-section composer grounded in real 1RMs + constraints +
  Kobe target + pain; conversational follow-up mode; delegates lookups to Kobe.
  48 grounding/routing evals pin behavior.

## What to test in Telegram after restart

- "design me a session for today" → then "what weights should I follow?"
  (should ANSWER, not regenerate).
- "/pain left shoulder sharp" → next session adapts around it; "/pain" lists it.
- "/profile set deadlift 160" → next session sizes deadlift off 160.
- "pick Sun for crossfit" → /plan (Sunday ADDED, others kept).
- "what is the WOD for Tuesday" (gym programming) vs "what's the WOD today"
  (Fraser designs today, folding in the gym WOD).

## Remaining backlog (not in this arc's scope)

#38 join gym_label into plan rows · #39 replan_week plan_path · #40 HRV-red
pick warning · #34 handle_show_goal · #49 proactive coaching · #51 transcript
voice · #44 expand Fraser eval cases toward the full 40.
