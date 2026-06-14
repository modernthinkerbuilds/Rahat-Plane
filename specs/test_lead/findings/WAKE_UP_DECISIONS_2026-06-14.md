# Wake-Up Decisions — 2026-06-14 (24-hour autonomous window)

You went to sleep at 2026-06-13 ~23:00 PDT after picking **Option C**
(pause RahatBadeMiya, close the architectural gap properly). You gave me
24 hours to work autonomously. This is the doc you read first when you
wake up.

---

## ACT 1 — Do these BEFORE reading further (5 minutes)

### 1. Unload the broken bot.

I can't run `launchctl` from the sandbox. Run this immediately:

```bash
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist
launchctl list | grep com.rahat.miya
# Should show ONLY com.rahat.miya (old Kobe — still running, fine)
```

This pauses RahatBadeMiya for the duration of the gap closure. Old Kobe
(com.rahat.miya at SCIENTIST_BOT_TOKEN) keeps serving you for daily use.

### 2. Pull my branch.

```bash
cd ~/developer/agency/rahat
git fetch
git checkout feat/arch-gap-closure-2026-06-13
git log --oneline main..HEAD
```

You'll see N commits in chronological order — each with a regression
test (bug-to-test policy enforced).

### 3. Read `WAKE_UP_REPORT.md`.

I'll write a final report at `specs/test_lead/findings/WAKE_UP_REPORT_2026-06-14.md`
with: what I shipped, what's still open, before/after eval scores.

---

## ACT 2 — Decisions I made on your behalf

I made my best call on each and documented it. **Override any of these
in your reply when you wake up; I'll rework that piece.**

### Decision 1: 1RMs source of truth

**My default:** I extracted these from your Gemini "Sports Scientist" chat
transcript:

| Lift | Value | Source line in transcript |
|---|---|---|
| Deadlift | 155 kg / 341 lb | "I'm at 155 kg deadlift" |
| Back Squat | 102 kg / 225 lb | "back squat sitting around 100-102 kg" |
| Bench Press | 60 kg / 132 lb | "bench 60 kg" |
| Overhead Press | 50 kg / 110 lb | "OHP around 50 kg" |
| Power Clean | TBD | not in transcript |
| Snatch | TBD | not in transcript |

**Override format:** reply "1RMs wrong, correct values: DL=X, BS=Y, …"

### Decision 2: Mobility / limitations profile

**My default:** from Gemini transcript and Telegram history:

- Right-side neck pain (recurring under load)
- Hip catch on cleans (right side)
- Right ankle issue (limits depth in squats)
- Tight hamstrings
- Hunch / forward shoulder under fatigue
- BP slightly elevated (under stress)
- Newborn at home → sleep-deprived, no consistent AM workout window

**Override format:** "limitations wrong, correct: …"

### Decision 3: Goal canonicalization

**My default:** target weight **196 lb**, date **2026-09-01** (rough — I
couldn't find a hard date you committed to). Current weight from latest
weight log in vault/rahat.db.

**Override format:** "goal: weight=X lb by date=YYYY-MM-DD"

### Decision 4: Single voice sink architecture

**My default:** re-voice every kobe_route / fraser_route response through
`synthesizer.synthesize()` with a 60-second LRU cache by `(chat_id, hash(message))`. Latency cost: +1-2s on first reply. Quality win: voice
consistency, fact-checked output, arbitration applied.

**Override format:** "voice sink: scrubber-only" or "voice sink: full synth"

### Decision 5: Branch strategy

**My default:** feature branch `feat/arch-gap-closure-2026-06-13`. No push
to main. Each commit has a regression test. You merge on wake-up if you
like the result.

**Override format:** "merge to main" or "rework before merge"

### Decision 6: Old Kobe (com.rahat.miya) stays on

**My default:** com.rahat.miya (SCIENTIST_BOT_TOKEN, old Kobe bot) keeps
running while I work. It has the wrong 1RMs bug and other old issues, but
it's what you have for daily coaching tonight + tomorrow morning. After
gap closure ships, we cut you over to new RahatBadeMiya with real
profile data.

**Override format:** "kill old Kobe too" (you go bot-less for the week)

---

## ACT 3 — What I plan to ship in 24 hours

| Phase | Hours | Output |
|---|---|---|
| 1 — Foundation | 0–6 | `vault/user_profile.json` schema + loader. Mine real 1RMs, weight log, mobility events, goal history from 3-month decisions.db scan. |
| 2 — Voice sink | 6–12 | Route kobe_route + fraser_route through synth re-voicing with UserProfile FACTS injection. Validation layer catches LLM contradictions. |
| 3 — Bug fixes | 12–18 | Fix Kobe active_rest day_target. Port arbitration to passthrough. Build fact-grounding eval. |
| 4 — Verification + handoff | 18–24 | Replay harness on 3-month corpus, diff old vs new, quality scorecard. Final report. |

Each phase commits to the branch as it lands. If anything blocks (data
not where I expect, test failure I can't fix), I'll back off, document
the block, and move to the next phase rather than stall.

---

## ACT 4 — What I will NOT do without you

- **No push to main.**
- **No `git push`** of the feature branch beyond local commits (you do
  the push after reviewing).
- **No live-DB schema migration** without your approval — the
  user_profile lives as a JSON file alongside vault/rahat.db, not as a
  schema change.
- **No agents/ KTLO-territory edits beyond the Kobe active-rest math fix
  (Decision 3).** That one is in scope because it ships a wrong number
  to you daily and the fix is a 2-line math correction with a test.
- **No prod-impacting launchd changes.**
- **No edits to core/charter.py beyond what's needed to read
  user_profile.json.**

---

## ACT 5 — Failure modes I'm planning for

**Failure A: vault/decisions.db doesn't have the 3-month transcript I
need to mine.** Fallback: I'll use the Gemini transcript JSON dump you
pasted earlier today as the profile seed. Tag the resulting profile
`source=gemini_transcript_2026-06-13` so you can audit.

**Failure B: Real Gemini API is unreachable from the sandbox.** Fallback:
synth tests run on the structured fallback. The voice-sink change is
still meaningful because the structured fallback also re-voices.

**Failure C: A bug-to-test gate failure I can't resolve.** Fallback: I
revert the offending commit, document it as a stuck-point in the wake-up
report, and continue with the next phase.

**Failure D: Live decisions.db corruption attempt.** Hard block from
RAHAT_TEST_MODE=1; I won't override. Documented as stuck-point if it
occurs.

---

## ACT 6 — When you wake up

1. Do ACT 1 (5 min — launchctl unload, git checkout).
2. Read this doc + `WAKE_UP_REPORT_2026-06-14.md`.
3. Reply to me with overrides (if any) or "merge to main and restart."
4. I'll execute your call.

Sleep well. I'm on it.
