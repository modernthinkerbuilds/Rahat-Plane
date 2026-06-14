# Live Bug Triage — 2026-06-13 (post-restart)

You stepped out for 2 hours after seeing the new bot misbehave in
Telegram. Here's what was broken, what I fixed, what's still on you,
and the exact commands to ship the fix.

---

## What broke in those 3 minutes of live use

**21:05** — you sent `On which day per my plan` and got a long
multi-paragraph reply about an aggressive timeline change you hadn't
asked for.

**21:07** — you sent `Assume I need to hit 196 by 06/22- create a
plan for me` and got TWO replies to the same message. Both contained:

> "Kobe flags a conflict here. ... Fraser has designed a high-volume
> workout for today ... say 'confirm the new goal' and I will
> officially update your plan."

> "Venkat, to move you toward that 196 lb target, Fraser has designed
> a high-volume session ... a quick clarification from Kobe: the
> system shows your target date is June 10, 2026, not June 22nd."

Five bugs in one exchange:

| # | Symptom | Root cause |
|---|---|---|
| 1 | **Voice leak** — bot said "Kobe flags...", "Fraser has designed...", "clarification from Kobe" | System prompt EXPLICITLY told Gemini to "Cite the source. 'Kobe says…', 'Fraser's design…'". And the workout draft section was labeled `FRASER'S DRAFT:` — Gemini parroted the label. |
| 2 | **Overproduction** — terse question → 4-section workout dump | Prompt said "be brief" but didn't enforce length-matching and didn't block unsolicited workout dumps. |
| 3 | **Hypothetical mishandled** — "Assume X" treated as real goal change | No rule in prompt for conditional/hypothetical asks. Bot offered to "officially update your plan." |
| 4 | **Duplicate replies** — same message → two bot turns | Operational: two runner processes polling the same Telegram token. "HTTP 409 Conflict" all over the log. |
| 5 | **Date confusion** — "06/22" + "June 10, 2026" mashed up | Not separately fixed today; covered by the voice/hypothetical fixes (the date hallucination only happened because bot was inventing a plan change). |

---

## What I fixed in code (committed to working tree, not yet pushed)

### Fix 1 — Rewrote the SYSTEM_PROMPT

**File:** `new_plane/miya_runner/synthesizer.py`

**Before:** Told Gemini to "Cite the source… 'Kobe says…', 'Fraser's
design…'" and to "name the conflict."

**After:**
- "You are ONE voice. Never attribute statements to 'Kobe', 'Fraser',
  'the sports scientist', 'the CrossFit coach', or any internal
  specialist."
- "Match length to the question. A one-line question gets a one-line
  answer. Do NOT dump a 5-section workout plan unless the user
  explicitly asked for a workout to be designed."
- "Hypothetical/conditional asks ('assume...', 'if I...', 'what
  would...') deserve a brief analytic answer, NOT a unilateral plan
  change. Do not 'officially update' anything; do not invent
  conflicts that the user did not raise."
- "Never say 'officially update the plan' or 'confirm the new goal'."

### Fix 2 — Renamed the draft label

Same file. `FRASER'S DRAFT:` → `WORKOUT DRAFT (internal, re-voice as
Miya):`. Gemini can no longer parrot the specialist name because the
specialist name isn't in the prompt anywhere.

### Fix 3 — Renamed the structured fallback label

When Gemini is unavailable, the runner ships `_structured_fallback()`
text directly to the user. That used to say `fraser: <text>`. Now it
says `workout: <text>`.

### Fix 4 — Regression tests

**File:** `tests/regression_registry/test_2026_06_13_live_voice_overprod_bugs.py`

11 tests pinning every fix:
- SYSTEM_PROMPT must contain the ONE-voice rule
- SYSTEM_PROMPT must NOT contain the leak phrases
- SYSTEM_PROMPT must mention "Kobe" at most once (in the negative example)
- `_build_prompt` must not include `FRASER'S DRAFT`
- `_structured_fallback` must not include `fraser:`
- SYSTEM_PROMPT must enforce length-matching
- SYSTEM_PROMPT must block unsolicited workout dumps
- SYSTEM_PROMPT must have a hypothetical/conditional rule
- SYSTEM_PROMPT must forbid "officially update the plan" phrasing
- One skipped test documents the 409 (operational, not code)

Plus 4 existing tests updated to expect the new neutral label:
- `tests/new_plane/test_runner_synthesizer.py::test_build_prompt_includes_workout_draft`
- `tests/new_plane/test_runner_synthesizer.py::test_build_prompt_includes_system_and_user`
- `tests/new_plane/test_synthesizer_prompt_snapshot.py` (2 cases)

**Suite status:** 1087 passed, 17 skipped, 9 xfailed.

---

## What's still on you

### 1. The 409 / duplicate replies — operational

The runner log was spammed with:
```
telegram getUpdates failed: HTTP Error 409: Conflict
```

This ONLY happens when two processes long-poll the same bot token. Most
likely cause: you have both the foreground `python -m new_plane.miya_runner`
AND a launchd-managed `com.rahat.miya.v2` running.

**To diagnose:**
```bash
ps aux | grep "new_plane.miya_runner" | grep -v grep
launchctl list | grep com.rahat.miya
```

If you see more than one Python process, OR both com.rahat.miya AND
com.rahat.miya.v2 are loaded, **that's your duplicate**.

**To fix:**
```bash
# Kill every runner instance
pkill -f "new_plane.miya_runner"
sleep 2
ps aux | grep "new_plane.miya_runner" | grep -v grep
# Should print nothing

# Then start exactly ONE
cd ~/developer/agency/rahat
source .venv/bin/activate
set -a; source .env; set +a
python -m new_plane.miya_runner
```

The 409 conflicts should stop immediately. If they don't, you also
have a launchd service running — `launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist`
and restart manually.

### 2. The "Kobe says..." / "Fraser designed..." voice leak is dead

But only if you **restart the runner**. The bot in your terminal right
now is still using the OLD prompt — that's what shipped the bad
replies. After commit + restart, the new prompt loads.

### 3. The date confusion (06/22 vs June 10)

Not separately fixed. It only happened because the bot was inventing a
plan change. With the hypothetical rule now in the prompt, the bot
should answer "assume I hit 196 by 06/22" as a math/implication
question, not a plan-change request — and the date confusion should
disappear with it.

If after the restart you see a fresh date-handling bug, send me the
exchange and I'll write a date-parsing fix specifically.

---

## Commit + restart sequence

```bash
cd ~/developer/agency/rahat

# Make sure the .git/index.lock isn't stale (we hit this twice today)
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null

git add new_plane/miya_runner/synthesizer.py \
        tests/regression_registry/test_2026_06_13_live_voice_overprod_bugs.py \
        tests/new_plane/test_runner_synthesizer.py \
        tests/new_plane/test_synthesizer_prompt_snapshot.py \
        specs/test_lead/findings/LIVE_BUG_TRIAGE_2026-06-13.md

git commit -m "fix(synth): kill voice leak + overproduction + hypothetical mishandle

Live RahatBadeMiya transcript 2026-06-13 21:05-21:08:
  - Bot said 'Kobe flags a conflict' and 'Fraser has designed...'
  - Terse goal-math question got a 4-section workout dump
  - 'Assume I need to hit 196 by 06/22' triggered 'officially update
    your plan' offer
  - Same message got 2 distinct bot replies (operational 409, not a
    code bug)

Root cause was concentrated in synthesizer.py:
  1. SYSTEM_PROMPT instructed Gemini to 'Cite the source: Kobe says,
     Fraser's design...' — the exact phrases the live bot shipped.
  2. _build_prompt labeled the workout draft 'FRASER'S DRAFT:' so
     Gemini saw the specialist name and parroted it.
  3. Prompt had no rule for length-matching or hypothetical handling.

Fixes:
  - SYSTEM_PROMPT rewritten: 'ONE voice', forbids naming specialists,
    enforces length-matching, blocks workout dumps outside design intent,
    handles hypotheticals analytically, forbids 'officially update' phrasing.
  - Draft label: FRASER'S DRAFT → WORKOUT DRAFT (internal, re-voice as Miya).
  - Structured fallback label: 'fraser:' → 'workout:'.

Tests:
  - tests/regression_registry/test_2026_06_13_live_voice_overprod_bugs.py
    (11 tests, 1 documented operational skip)
  - 4 existing snapshot/synth tests updated to expect new labels.

Suite: 1087 passed, 17 skipped, 9 xfailed."

git push
```

If the pre-push gate complains about the `fix:` commit needing a
regression registry file — the new test file already lives in
`tests/regression_registry/`, so the gate should pass.

After push:

```bash
# Kill ALL runners (including any launchd-managed v2)
pkill -f "new_plane.miya_runner"
sleep 2
# Confirm
ps aux | grep "new_plane.miya_runner" | grep -v grep
# Should print nothing

# Clear bytecode caches so the new prompt loads
find new_plane -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Boot exactly ONE runner
source .venv/bin/activate
set -a; source .env; set +a
python -m new_plane.miya_runner
```

You should see, in the boot log:
- `new Miya v2 live | adapter=…`
- `agents registered: ['scientist', 'fraser']` (or similar)
- `proactive nudges ENABLED (default)`

And the 409 errors should be **gone**. If they're not, you still have a
duplicate process — see Section 1 above.

---

## Quick smoke test to verify the fix

Send to RahatBadeMiya:

| You send | Old bot would say | New bot should say |
|---|---|---|
| `On which day per my plan` | Long multi-paragraph aggressive-timeline plan | One line answering the day, no workout dump |
| `Assume I need to hit 196 by 06/22- create a plan for me` | "Kobe flags...", "Fraser has designed...", "officially update your plan" | A brief analytic reply about the implication, ideally one clarifying question. No specialist names. |
| `Where am I on pace` | (test the underlying recalibration) | Honest pace verdict, no "Kobe says" prefix |
| `What is tommorows WOD` | Already fixed last session | Verbatim WOD, no specialist names |

**The acceptance criterion:** zero occurrences of "Kobe says" / "Fraser
designed" / "officially update" in any reply.

---

## What I did NOT do (deliberate, with reasons)

- **Did not rewrite the synthesizer's flow control.** The prompt change
  is the surgical fix. Touching the orchestrator/synth pipeline would
  invalidate the 235 tests we shipped Friday and the 43 PF tests
  Saturday — way more risk than reward for a 2-hour window.

- **Did not add a "two-bot-detection" tripwire.** The 409 is loud
  enough in the runner log that you'll catch it manually. A tripwire
  is a follow-up, not a tonight fix.

- **Did not write a new regression class for "duplicate replies."** The
  same fix that handles the 409 (kill the dup process) handles the
  symptom. There's nothing in code to test.

- **Did not run the Gemini-chat audit (Task #118).** You shared 800 KB
  of real coach transcripts and they're an absolute goldmine for
  fixture material — but parsing them into eval cases is a 6-hour
  job, not a 2-hour one. They're saved as the next test lead's first
  job.

- **Did not chase the date confusion separately.** It was a symptom of
  the bot inventing a plan change. With the hypothetical rule, the bot
  shouldn't invent anymore. If the date bug recurs after restart, I'll
  write a focused fix.

---

## Reading order when you're back

1. **This doc.** Confirm you agree with the fix shape.
2. Run the commit + push + restart in order.
3. Run the smoke test. If all 4 prompts behave correctly, you're done.
4. If anything regresses, send me the exact exchange and I'll diagnose.

Total time when you sit back down: ~10 minutes.
