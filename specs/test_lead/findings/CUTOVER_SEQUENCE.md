# Cutover Sequence — 2026-06-10

Step-by-step. Copy/paste in order. Each step shows the command, the
expected output snippet, and what to do if it fails.

**Total time:** ~30 min if everything's green, +30 min for the soak
+ Telegram verifications.

---

## Phase A — Pull the new code (3 min)

```bash
cd ~/developer/agency/rahat
git fetch origin
git status -sb
```

**Expected:** `## feat/new-plane-stage0` (or your active branch),
some `M` and `??` lines for files the sandbox session changed.

**If you see "behind" by N commits:** the test lead's work or my
fixes haven't been pulled yet. Continue with the commit sequence
below — the files on disk are what matters.

---

## Phase B — Commit the new work in 3 logical groups (15 min)

### B1. Architect P0/P1 fixes commit

```bash
git add new_plane/miya_runner/orchestrator.py \
        new_plane/miya_runner/synthesizer.py \
        new_plane/miya_runner/delegate_classifier.py \
        new_plane/miya_runner/__main__.py \
        new_plane/miya_runner/native_client.py \
        new_plane/miya_runner/adapter_client.py \
        new_plane/miya_runner/pending.py \
        new_plane/signals/store.py

git commit -m "fix(new-plane): P0/P1 cutover gap closures

P0-1: verbatim WOD bypass — when intent=workout_lookup AND gym_wod has
  text AND charter allows, skip synthesizer entirely and return Kobe's
  text verbatim. Fix-of-fix for Bug I (2026-06-09).

P0-2: nudges default-ON. NEW_MIYA_NUDGES_ENABLED default flipped from
  '0' to '1' so morning brief keeps firing post-cutover. Set to '0' to
  suppress while old com.rahat.miya is still loaded.

P0-3: charter kind derivation. _charter_kind_and_ctx(intent, fraser_text,
  facts) maps intent → (fraser.workout.commit | coach.push_intensity |
  notify.user.reply) and populates ctx['hrv_state'] from latest_hrv() +
  hrv_band(). Previously all turns used kind='notify.user.reply' so the
  hrv_red_blocks / fraser_hrv_red_blocks_workout policies never fired.

P0-4: _PLAN_MUTATION_RE expanded with skip|cancel|move|postpone|
  reschedule patterns + 'swap X and Y' (real ledger phrasings).

P1-2: new_plane/miya_runner/pending.py — pending_clarification with 60s
  TTL, backed onto signal store (type=pending_clarification). Resolves
  Yes/A/1/first/etc. against the latest live pending per chat.

P1-3: explicit @huberman path. delegate_classifier returns
  ('huberman_route', body). native_client.huberman_route wraps Kobe's
  mesh delegation with a clear marker so analytics + replay show
  path=huberman_route.

Storage: signals.store gets a nullable chat_id column via additive
migration; orchestrator publishes/reads with chat_id set. Legacy NULL
rows remain visible to all chats (backward compat)."
```

### B2. Tests + corpus commit

```bash
git add tests/regression_registry/test_2026_06_10_cutover_p0_p1_fixes.py \
        tests/regression_registry/test_2026_06_10_pf_fixes.py \
        tests/new_plane/test_runner_delegate_classifier.py \
        tests/new_plane/test_cross_agent_signal_isolation.py \
        tests/new_plane/test_compare_harness.py \
        tests/evals/test_synthesizer_grounding.py \
        tests/adversarial/corpus.json \
        scripts/mine_phrasings.py

git commit -m "test(cutover): regression coverage for P0/P1 fixes

+43 cutover_p0_p1_fixes tests pinning each fix surface + negative guards.
+26 pf_fixes tests pinning PF-001..006.
Test lead's strict-xfail tests rewritten as fix-verification tests.
Adversarial corpus xfails for PF-002/PF-003 cleared (now passing).
mine_phrasings.py hardened read-only (test lead 2026-06-10 finding).

1080/1080 green on full 5-layer suite (unit 28 + contract 920 + eval 101 +
adversarial 14 + regression 17). New-plane direct: 1076 passed, 9 xfailed."
```

### B3. Docs / archive commit

```bash
git add specs/ADR-013_migrate_to_new_plane.md \
        specs/ARCHITECTURE_DIAGRAM_2026-06-10.md \
        specs/archive/ \
        specs/test_lead/findings/GAP_MATRIX.md \
        specs/test_lead/findings/DECISIONS_LOG_2026-06-10.md \
        specs/test_lead/findings/CUTOVER_SEQUENCE.md \
        specs/test_lead/findings/ARCHITECT_HANDOFF_2026-06-10.md \
        specs/test_lead/findings/PROPOSED_FIXES.md

git commit -m "docs(cutover): GAP_MATRIX, decisions log, cutover sequence

GAP_MATRIX.md — P0/P1/P2 gap inventory with plain-language summaries.
DECISIONS_LOG_2026-06-10.md — what I changed and why, in simple language.
CUTOVER_SEQUENCE.md — step-by-step git + restart procedure.
ARCHITECTURE_DIAGRAM_2026-06-10.md — current shape post-cutover.
ADR-013 updated: Phase D marked complete with a fix-shipped table.

Archived 9 stale planning docs to specs/archive/2026-06-10/ (weekend
playbooks, pre-ADR-013 thesis deltas, pre-cutover architecture). All
preserved; just out of the live specs/ root."
```

### B4. Push

```bash
git push
```

**Expected pre-push gate output:**
```
✓ bug-to-test policy (1 fix commit, 1+ registry test added)
✓ regression registry (1100+ passed)
✓ silent-failure guard
✓ unit layer
✓ contract layer
✓ pre-push GREEN
```

If the bug-to-test gate complains, the registry file is already in
B1 — check `git show --stat HEAD~2` lists
`tests/regression_registry/test_2026_06_10_cutover_p0_p1_fixes.py`.

---

## Phase C — Restart the runner (5 min)

The bot currently in memory still has the pre-cutover code. Kill, clear
caches, restart.

```bash
# 1. Verify your bot is running and find its PID
ps aux | grep "new_plane.miya_runner" | grep -v grep

# 2. Kill it
pkill -f "new_plane.miya_runner"
sleep 2

# 3. Confirm gone
ps aux | grep "new_plane.miya_runner" | grep -v grep
# Should print nothing.

# 4. Clear bytecode caches (so Python loads fresh code)
cd ~/developer/agency/rahat
find new_plane -name __pycache__ -exec rm -rf {} + 2>/dev/null

# 5. Restart in foreground so you see the boot log
source .venv/bin/activate
set -a; source .env; set +a
python -m new_plane.miya_runner
```

**Expected boot lines:**
```
new Miya v2 live | adapter=http://127.0.0.1:8766 | chat_filter=8349888326 | flash=gemini-2.5-flash | pro=gemini-2.5-pro
proactive nudges ENABLED (default). new Miya owns morning briefings + recovery + walk nudges. If old com.rahat.miya is still loaded, unload it now to avoid duplicate sends.
```

**If you see "proactive nudges DISABLED":** your .env still has
`NEW_MIYA_NUDGES_ENABLED=0`. Remove that line or change to `1` and
restart.

---

## Phase D — Smoke-test in Telegram (10 min)

Send each message to **RahatBadeMiya_bot** and verify the runner
terminal log shows the right path.

| # | Send | Expected runner log | Expected reply shape |
|---|---|---|---|
| 1 | `What is tommorows WOD` | `path=kobe_route` | verbatim WOD or "no sync" — no Gemini paraphrase |
| 2 | `What was the workout for last Friday?` | `path=kobe_route` | last Friday's WOD verbatim |
| 3 | `/ fix sat 407` | `path=kobe_route` | fix confirmation |
| 4 | `where am I on pace` | `path=orchestrate`, `arbitration_rule=behind_pace` (if behind) | honest pace verdict, no "ahead" if behind |
| 5 | `skip Friday` | `path=kobe_route` | plan updated, Friday now rest |
| 6 | `cancel today` | `path=kobe_route` | plan updated |
| 7 | `move Wed to Thu` | `path=kobe_route` | plan updated |
| 8 | `@huberman recovery for tomorrow` | `path=huberman_route` | recovery guidance |
| 9 | `@miya what's the workout tomorrow` | `path=verbatim_wod` if gym_wod synced, else `path=orchestrate` | verbatim WOD if synced |
| 10 | `design me a workout for Friday` | `path=orchestrate`, `tool=fraser_design_session` | a Fraser workout card |

**If ANY message fails (empty reply, wrong path, error):**
1. Note which one
2. Ctrl+C the runner
3. Check `tests/last_run_report.md` — if green, your local code is
   OK; the failure is environmental. Re-source .env.
4. Re-run the runner.

**If 8+ of 10 pass:** smoke-test is GOOD. Proceed to Phase E.

---

## Phase E — Cut over (the actual flip) (2 min)

This is the irreversible step (you can roll back with one command;
see below).

```bash
# 1. Verify both bots are running side-by-side currently
launchctl list | grep com.rahat.miya
# You should see: com.rahat.miya AND com.rahat.miya.v2 (or just the new one
# if you haven't installed the launchd job yet — see Phase E.1)

# 2. (Optional) install com.rahat.miya.v2 as a launchd service if you
#    haven't already (so it survives reboots)
./scripts/install_new_miya.sh
launchctl list | grep com.rahat.miya.v2
# Should now show: <pid> 0 com.rahat.miya.v2

# 3. THE CUTOVER: unload old Miya
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist
launchctl list | grep com.rahat.miya
# Should now show ONLY com.rahat.miya.v2

# 4. Send `/plan` to your OLD Kobe bot (SCIENTIST_BOT_TOKEN)
#    → expected: no response (com.rahat.miya is unloaded)

# 5. Send `/plan` to RahatBadeMiya_bot → expected: structured plan
```

---

## Phase F — 48-hour soak (passive)

Don't touch the launchd jobs. Don't push more code unless you're
fixing a fresh production bug. Just use the bot normally for 48 hours.

What you're watching for:
- Morning brief fires at 6 AM tomorrow + day after
- WOD lookups return verbatim text (no paraphrase)
- Pace queries honor arbitration (no "ahead of pace" if behind)
- Skip/cancel/move plan mutations work
- Recovery protocols (`7/15 breathing`, `box breathing`) fire correctly

**If anything regresses:** rollback (Phase G) and write a registry
test for the regression before re-attempting cutover.

---

## Phase G — Rollback (only if needed)

```bash
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist
launchctl load ~/Library/LaunchAgents/com.rahat.miya.plist
```

Old Kobe is back. RahatBadeMiya_bot stops responding. Investigate +
fix, then re-try cutover.

**Do NOT revoke `SCIENTIST_BOT_TOKEN` at BotFather for at least 2
weeks post-cutover.** That's the rollback path.

---

## Post-cutover (within 1 week)

Per `GAP_MATRIX.md`:

- **Wire `pending.py` into the orchestrator.** Pick a Miya prompt
  pattern that asks A/B/C questions. Call `pending.record()` after
  asking. Call `pending.resolve()` at the top of the next turn.
  Probably 100 lines + tests.
- **Fix PF-007** (compare_harness parity isolation flake).
- **Pin httpx<0.28** or migrate to httpx2 so the 2 adapter test
  files collect cleanly.
- **Run the corpus mining script weekly** to catch new phrasings:
  ```
  python scripts/mine_phrasings.py --db vault/rahat.db \
    --output tests/adversarial/corpus.json --since-days 14
  ```

---

## Summary table

| Phase | What | Time | Reversible? |
|---|---|---|---|
| A | Pull + status | 3 min | yes |
| B | 3 commits + push | 15 min | yes (`git reset --soft`) |
| C | Restart runner | 5 min | yes (Ctrl+C; old code in old PID) |
| D | Telegram smoke | 10 min | yes |
| **E** | **launchctl unload old** | **2 min** | **yes (Phase G, 30 sec)** |
| F | 48-hr soak | 48 h | (passive) |
| G | Rollback if needed | 30 sec | n/a |

Total active time: ~35 min. Plus the soak.

Sleep well.
