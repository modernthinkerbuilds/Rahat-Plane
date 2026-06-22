# ADR-013 — Migrate old-plane functionality into new-plane Miya v2

**Status:** Accepted 2026-06-08
**Supersedes (in spirit):** The 8-week stop-or-go gate framing in
`RAHAT_THESIS_2026-05-27.md`. The thesis still describes the end-state
correctly, but the *decision* to migrate is taken now instead of after
8 weeks of evidence.
**Boundary change:** `specs/ARCHITECT_THREADS_2026-05-30.md`'s
two-architect split (KTLO vs new-plane) is collapsing into one.

---

## Context

The parallel-planes thesis (2026-05-27) committed to:
1. Old plane (Python Rahat — live Kobe, Fraser, charter, decisions ledger,
   nightly jobs) stays running untouched.
2. New plane (Python new Miya v2 with HTTP adapter to old plane) gets built
   alongside.
3. After 8 weeks of new-plane production usage, a stop-or-go gate
   decides whether to retire old Kobe/Fraser and port them into the new
   plane, OR kill the new plane.

The new plane went live 2026-06-08 as `RahatBadeMiya_bot`
(`com.rahat.miya.v2`, port 8766 adapter). First real Telegram round-trip
in the same session caught a "Kobe summary says ahead / numbers say
behind" inversion bug — arbitrating-orchestration evidence #1, captured
in `tests/regression_registry/test_2026_06_08_missed_workout_not_called_ahead.py`.

After that signal, the user committed early — skipped the 8-week wait —
because the operational pain of running **two Miyas** (two bot tokens,
two prompt styles, two response qualities, two places to fix bugs) is
greater than the residual risk of porting without 8 weeks of data.

## Decision

**`new_plane/` is now the active plane.** Old-plane Python code
(`agents/the_scientist/`, `agents/fraser/`, `core/charter.py`, etc.)
becomes a **library** that the new plane imports directly. The HTTP
adapter (`bridges/openclaw_adapters/`) stays alive for OpenClaw-future
and external use, but the runner stops calling it for internal
synchronous work — it goes through Python imports.

When the new plane reaches parity (Phase D below), `com.rahat.miya`
launchd job stops. Only `com.rahat.miya.v2` runs. Old code stays where
it physically lives until Phase F (cosmetic file moves), or longer if
nothing forces the move.

**KTLO of the old plane:** bug fixes (like Bug H 2026-06-08) still ship.
No new features. No structural refactors. The expectation is that
old-plane code stops changing within ~4 weeks.

## Phases

Each phase is its own session, fully reversible, and gated on the prior
phase's tests being green.

### Phase A — Direct imports in the runner (~2 hr session)

`new_plane/miya_runner/orchestrator.py` currently calls Kobe and Fraser
over HTTP via `adapter_client.py`. Replace those calls with direct
Python imports from `agents.the_scientist.tools` and
`agents.fraser.composer`. Each replacement preserves the `AdapterResult`
envelope shape so the rest of the orchestrator (charter check, signal
publish, arbitration, synthesis) doesn't have to change.

**Why:** eliminates HTTP round-trip latency (~5-15ms per call × 4 calls
per turn), eliminates the JSON envelope round-trip (no more lossy
paraphrasing of summary fields), and reduces the new plane's runtime
dependencies (no need to keep the adapter healthy for the runner to
work).

**What we keep:** the adapter itself stays in tree and stays running as
a launchd service (still useful for OpenClaw future and for the eval
suite). The runner just stops being a consumer of it.

**Rollback:** revert the orchestrator commit. The adapter is unchanged
so the runner falls back to HTTP-only behavior.

### Phase B — Vault DB unification (~1 hr session)

The new plane currently writes signals to `~/.rahat/new_plane_signals.db`
(its own SQLite). For cross-agent memory and decisions-ledger parity,
the runner also needs to write to `vault/rahat.db` (same DB as
old plane) so that:
- The decisions ledger sees new-Miya turns alongside old-Kobe turns
- Cross-agent memory accumulates in one place
- The KTLO architect's eval suite can replay new-Miya turns

Signal store stays as a separate SQLite (it's a different primitive —
the load-bearing typed-signal interface per the thesis). Only decisions
get unified.

### Phase C — Nudge port (~2 hr session)

Old Kobe's `handler.start()` runs a minute-tick loop that calls
`maybe_morning_briefing()`, `maybe_weekly_reset()`,
`maybe_recovery_nudge()`, `maybe_walk_nudge()`. These are user-visible
features the new plane doesn't replicate yet.

Port the tick into `miya_runner/__main__.py`'s serve loop so the new
plane also pushes proactive nudges. Reuse the underlying functions via
direct import (Phase A's pattern); just the *scheduling* moves.

### Phase D — Capability gap audit (~1-2 hr session) ✅ COMPLETED 2026-06-10

Run 15-20 production-realistic prompts (`python -m new_plane.compare …`)
through both bots in parallel, diff the outputs, identify anything new
Miya v2 handles meaningfully worse. Fix those before Phase E.

**Status (2026-06-10):** complete. Findings + fixes documented in
`specs/test_lead/findings/GAP_MATRIX.md`. P0/P1 fixes shipped:

| Gap | Fix | Test |
|---|---|---|
| P0-1 Verbatim WOD paraphrase | orchestrator bypasses synth when gym_wod has text | `tests/regression_registry/test_2026_06_10_cutover_p0_p1_fixes.py::TestP0_1_VerbatimWodBypass` |
| P0-2 Nudges defaulted OFF | `NEW_MIYA_NUDGES_ENABLED` default flipped to `1` | same file `TestP0_2_NudgesDefaultOn` |
| P0-3 Charter generic kind | `_charter_kind_and_ctx` derives kind from intent + injects hrv_state | same file `TestP0_3_CharterKindDerivation` |
| P0-4 skip/cancel/move not pinned | `_PLAN_MUTATION_RE` expanded with the missing patterns | same file `test_p04_skip_cancel_move_routes_to_kobe` |
| P1-2 No pending_clarification state | `new_plane/miya_runner/pending.py` with 60s TTL | same file `TestP1_2_PendingClarification` |
| P1-3 @huberman logged as kobe_route | `huberman_route` path in native_client + delegate_classifier | same file `TestP1_3_HubermanExplicitPath` |

Also covered by earlier sessions:
- `@kobe` / `@fraser` direct-agent addressing — delegate_classifier
- `pick Mon for crossfit` day-picks — `_PLAN_MUTATION_RE`
- Weight syncs (`165.2`) — `_WEIGHT_LOG_RE`
- HRV logs (`HRV 38`) — `_HRV_LOG_RE`
- Recovery protocols — `_RECOVERY_RE`
- Past-tense WOD lookup ("what was…") — PF-003 fix
- Slash + whitespace ("/ fix") — PF-002 fix

Remaining known gaps (deferred to post-cutover):
- P1-1 verbatim-WOD via @miya prefix (defense-in-depth, low priority)
- P1-4 decisions-ledger de-dup (analytics cleanup, not user-facing)

### Phase E — Cutover (~30 min)

```bash
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist
# Only com.rahat.miya.v2 runs. RahatBadeMiya_bot is the production bot.
```

Old bot's launchd plist stays in `~/Library/LaunchAgents/` (unloaded).
Old `SCIENTIST_BOT_TOKEN` stays valid (don't revoke at BotFather) so
rollback is possible.

**Rollback procedure (must be ≤2 commands):**
```bash
launchctl load ~/Library/LaunchAgents/com.rahat.miya.plist
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist
```

### Phase F — Cosmetic relocation (no time pressure)

Move `agents/the_scientist/` → `new_plane/agents/kobe/`,
`agents/fraser/` → `new_plane/agents/fraser/`,
`core/charter.py` → `new_plane/core/charter.py`, etc. Pure file
relocation + import-path fixes. Can happen weeks later, only when
convenient.

The reason this is last: file moves are huge git diffs that obscure
real changes. Better to move once when stable than to move while still
porting.

## Non-decisions (deliberately left open)

- **Telegram bot name in production.** Once `com.rahat.miya` is off,
  the user can ask BotFather to rename `RahatBadeMiya_bot` → `Miya_bot`
  (or whatever) if desired. Not part of this ADR.
- **OpenClaw TS plugin path.** Parked as Stage 2 of the original
  thesis; this ADR doesn't change that. The OpenClaw work is still
  available in `new_plane/openclaw_plugin/` if/when needed.
- **Code organization within `new_plane/`.** Phase F is cosmetic only.
  The internal structure (orchestrator / synthesizer / cost_router /
  signal_store split) stays as-is.

## What "KTLO" means in practice for the old plane

For the next ~4 weeks:
- **Bug fixes ship.** Like Bug H (Bug-2026-06-08-morning-brief). If the
  live bot breaks for the user, KTLO architect's job is to unblock.
- **No new features.** No new tools, no new intents, no behavior
  changes. If the user asks for a new behavior, build it in the new
  plane.
- **No refactors.** No file moves, no protocol changes, no test
  reorganization. Keep diffs minimal so rollback is easy.
- **Pre-push gate stays green.** Whatever passes today must pass
  tomorrow.

After Phase E (cutover), KTLO graduates to "frozen" — only critical
security fixes ship. After Phase F (relocation), the old paths exist
only in git history.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| New Miya v2 has a bug only seen in production → user gets bad responses | Phase D capability audit before Phase E cutover. Rollback is 2 launchctl commands. |
| Direct imports introduce circular dependency between new_plane and agents/ | Imports are one-way: new_plane → agents. Never the reverse. Pre-push gate catches. |
| Charter port breaks safety gate → spam or wrong-content sends | Charter port (part of Phase B / D) tests every send_kind against the original charter, side-by-side, before cutover. |
| Decision ledger collision between old and new plane writers during Phase B | Phase B sequences carefully: new plane writes new decisions to vault/rahat.db AFTER old bot is cutover (Phase E). Until then, new plane keeps its own DB. |
| KTLO architect ships a state.py refactor that breaks new plane's direct imports | KTLO is paused per user direction 2026-06-08. If a critical fix is needed, KTLO architect notifies new-plane architect in the commit message. |

## Success criteria (when is this ADR "done")

- Phase E completed: `com.rahat.miya` not running, `com.rahat.miya.v2`
  serving all user-facing traffic for ≥48 hours without incident.
- New plane test suite: 132+ tests green.
- Old plane 5-layer suite: still passes for the imported libraries.
- User can describe the migration without needing this doc.

After that, the parallel-planes thesis (2026-05-27) becomes a historical
document. The new plane is the only plane.

## References

- `RAHAT_THESIS_2026-05-27.md` — original parallel-planes commitment
- `specs/ARCHITECT_THREADS_2026-05-30.md` — KTLO vs new-plane boundary
  (now collapsing)
- `specs/WAKE_UP_PLAYBOOK.md` — how to run the new plane today
- `specs/RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md` — PM-side thinking
  on the cross-agent signal interface
- `tests/regression_registry/test_2026_06_08_missed_workout_not_called_ahead.py` —
  the bug that triggered the early commit
