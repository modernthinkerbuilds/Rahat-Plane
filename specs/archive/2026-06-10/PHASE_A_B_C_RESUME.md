# Phase A + B + C resume — 2026-06-08 evening

**Built unattended while you stepped out.** Per ADR-013.
Status: **1,134 tests green, ready for you to verify + commit.**

## Where we are

- **Phase A** (Direct imports) — **COMPLETE.** Runner uses
  `native_client.py` (direct Python imports of Kobe/Fraser) instead of
  HTTP adapter. `adapter_client.py` stays for OpenClaw / external use.
- **Phase A.2.b** (charter `allow`/`allowed` mismatch) — **COMPLETE.**
  Standardized on `allow`. Pinned in test_openclaw_adapter.py +
  test_runner_native_client.py.
- **Phase B** (Vault DB unification) — **COMPLETE, flag-gated OFF.**
  Set `NEW_MIYA_USE_LIVE_DB=1` to mirror each turn into
  `core.decisions` (vault/rahat.db).
- **Phase C** (Nudge tick port) — **COMPLETE, flag-gated OFF.**
  Set `NEW_MIYA_NUDGES_ENABLED=1` to fire morning brief / weekly
  reset / recovery / walk nudges from RahatBadeMiya. **You MUST stop
  com.rahat.miya first** to avoid double-sends.

## Test counts

| Suite | Count |
|---|---|
| new-plane | 172 passed (up from 132) |
| unit (old) | 28 passed |
| contract (old) | 802 passed |
| eval (old) | 101 passed |
| adversarial (old) | 14 passed |
| regression (old) | 17 passed |
| **Total** | **1,134 passed** |

New tests added this session:
- `test_runner_native_client.py` — 23 tests (envelope shape, parity,
  charter envelope, error wrapping, day token resolution, end-to-end
  via native path)
- `test_runner_live_db.py` — 7 tests (Phase B flag semantics, write
  contents, token capture, failure handling, charter-veto outcome)
- `test_runner_nudges.py` — 7 tests (Phase C tick semantics, swallow
  exceptions, import failure handling, flag-gate)

Plus the WOD lookup adapter tests + the standardized charter envelope
pin in `test_openclaw_adapter.py`.

## Files changed this session

```
M  bridges/openclaw_adapters/server.py
       (envelope key: "allowed" → "allow")
M  new_plane/miya_runner/orchestrator.py
       (default client = native_client; added _log_decision_to_live_db)
M  new_plane/miya_runner/__main__.py
       (added _fire_nudges + minute tick in serve loop)
A  new_plane/miya_runner/native_client.py
       (NEW — same API as adapter_client but direct imports)
M  tests/new_plane/test_runner_orchestrator.py
       (force HTTP mode in existing tests)
M  tests/new_plane/test_runner_wod_lookup.py
       (force HTTP mode in existing tests)
M  tests/new_plane/test_compare_harness.py
       (force HTTP mode in existing tests)
M  tests/new_plane/test_openclaw_adapter.py
       (envelope key pin + new gym_wod_on/workout_on endpoint tests)
A  tests/new_plane/test_runner_native_client.py    (NEW)
A  tests/new_plane/test_runner_live_db.py          (NEW)
A  tests/new_plane/test_runner_nudges.py           (NEW)
A  specs/PHASE_A_B_C_RESUME.md                     (this file)
```

## What you do when you're back

### 1. Quick sanity (1 min)
```bash
cd ~/developer/agency/rahat
git status -sb        # should match the file list above
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ -q   # expect 172 passed
```

### 2. Try the runner against direct imports (3 min)

The runner now defaults to `native_client`. Adapter doesn't need to be
running for the runner to work — but for backward compat, you can
still run it:

```bash
# Terminal A — adapter (optional, only needed if NEW_MIYA_USE_HTTP_CLIENT=1)
source .venv/bin/activate
set -a; source .env; set +a
python -m uvicorn bridges.openclaw_adapters.server:app --port 8766

# Terminal B — runner (uses native client by default)
source .venv/bin/activate
set -a; source .env; set +a
python -m new_plane.miya_runner
```

Look for boot log line:
```
proactive nudges DISABLED (default). Old Kobe still owns morning
briefings + recovery + walk nudges.
```

Send "what's the workout for tomorrow" in Telegram → verify:
- No transport_errors logged (direct imports, no HTTP)
- Response is faster (no round-trip)
- WOD comes through cleaner (no envelope paraphrase loss)

### 3. (Optional) Try Phase B — live DB mirroring (2 min)

If you want new Miya turns to land in `vault/rahat.db` alongside
old Kobe's:

```bash
echo "NEW_MIYA_USE_LIVE_DB=1" >> .env
# Restart runner (Ctrl+C, re-source .env, re-run)
```

Verify after one turn:
```bash
sqlite3 vault/rahat.db "select decision_id, ts, actor, op, outcome from decisions where actor='miya.v2' order by decision_id desc limit 5;"
```

### 4. (Optional) Try Phase C — nudges from new Miya (only after stopping old Kobe)

**Pre-condition:** old Kobe must be off, or you get double nudges.

```bash
# Stop old Kobe
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist

echo "NEW_MIYA_NUDGES_ENABLED=1" >> .env

# Restart runner — boot log will say:
#   proactive nudges ENABLED — porting from old Kobe
```

Wait for the next minute boundary; new Miya checks each nudge function
and sends any non-None output.

### 5. Rollback procedure (if anything misbehaves)

```bash
# Bring old Kobe back
launchctl load ~/Library/LaunchAgents/com.rahat.miya.plist

# Stop new Miya nudges (won't affect message responses)
sed -i '' 's/^NEW_MIYA_NUDGES_ENABLED=1/NEW_MIYA_NUDGES_ENABLED=0/' .env
# Restart runner
```

### 6. Commit + push

```bash
git add bridges/openclaw_adapters/server.py \
        new_plane/miya_runner/native_client.py \
        new_plane/miya_runner/orchestrator.py \
        new_plane/miya_runner/__main__.py \
        tests/new_plane/test_runner_orchestrator.py \
        tests/new_plane/test_runner_wod_lookup.py \
        tests/new_plane/test_compare_harness.py \
        tests/new_plane/test_openclaw_adapter.py \
        tests/new_plane/test_runner_native_client.py \
        tests/new_plane/test_runner_live_db.py \
        tests/new_plane/test_runner_nudges.py \
        specs/PHASE_A_B_C_RESUME.md

git commit -m "feat(new-plane): ADR-013 Phases A+B+C — direct imports, live DB, nudge port

Phase A — direct imports:
  new_plane/miya_runner/native_client.py: same API as adapter_client.py
  but calls agents.the_scientist.tools / agents.fraser.composer
  directly. Orchestrator switches to native by default.
  NEW_MIYA_USE_HTTP_CLIENT=1 falls back to HTTP (OpenClaw / debug).

Phase A.2.b — charter envelope fix (Bug 2026-06-08):
  Adapter was emitting {'allowed': bool}, orchestrator was reading
  {'allow': bool} — check always fell open to True default.
  Standardized on 'allow' everywhere. Pinned with explicit anti-key tests.

Phase B — live-DB unification (flag-gated):
  NEW_MIYA_USE_LIVE_DB=1 → orchestrator mirrors each turn into
  core.decisions (vault/rahat.db). Default OFF. Captures input,
  output, tokens, model, outcome, veto reason.

Phase C — nudge tick port (flag-gated):
  NEW_MIYA_NUDGES_ENABLED=1 → runner serve loop fires
  maybe_morning_briefing / maybe_weekly_reset / maybe_recovery_nudge
  / maybe_walk_nudge each minute and sends to RahatBadeMiya.
  Default OFF — old Kobe still owns these until cutover.

Tests:
  test_runner_native_client.py — 23 tests (envelope, parity, errors)
  test_runner_live_db.py — 7 tests (flag semantics, write contents)
  test_runner_nudges.py — 7 tests (tick, error handling, flag-gate)
  test_openclaw_adapter.py: charter envelope pin + new endpoint tests
  Existing orchestrator/wod/compare tests updated to force HTTP mode

1,134 total tests green (172 new-plane + 962 old-plane).

Cutover path: stop com.rahat.miya, set NEW_MIYA_USE_LIVE_DB=1 and
NEW_MIYA_NUDGES_ENABLED=1, restart runner. Rollback is reverse."

git push
```

## What still needs YOU (Phase D + E)

**Phase D — capability gap audit.** Send 15-20 production prompts to
RahatBadeMiya, compare with what old Kobe would say. Look for anything
new Miya does meaningfully worse. Common things to check:
- @kobe / @fraser direct addressing — not ported yet
- `pick Mon for crossfit` day-pick commands — not handled by new Miya
- `goal 196 by 2026-06-10` goal-set commands — not handled
- Weight syncs (`165.2`) — not handled
- Dislikes (`no deadlifts today`) — not handled
- `show plan` / `show goal` / etc. — partial coverage

These are old Kobe's command surface. New Miya orchestrator currently
only does Q&A + design. To reach full cutover, we'd port the command
surface in **Phase D.5** (call it that when we get there).

**Phase E — Cutover.** Single command:
```bash
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist
```
With Phase B + C flags ON, new Miya v2 is now the only bot. Rollback
is `launchctl load`.

## Open architectural items

- **Adapter as always-on launchd service.** It's currently manual via
  the smoke script. Phase A made the runner not need it, but the
  OpenClaw plugin path will. Worth installing as a service when you
  pick up OpenClaw work.
- **Command surface port.** Per Phase D.5 above — old Kobe handles ~30
  command-style messages (pick, goal, sync, blacklist, etc.) that
  new Miya doesn't yet. The orchestrator structure assumes Q&A; we'd
  need a "command intent" branch alongside the Kobe/Fraser/lookup intents.
- **Nudge mark-already-sent coordination.** Both planes share the same
  `nudge_already_sent` marker (via vault/rahat.db). If you run both
  with nudges ON, the first one to fire claims the marker. That's
  actually a safety net — but it's an artifact, not a designed behavior.
  Worth being explicit before turning Phase C on for real.
