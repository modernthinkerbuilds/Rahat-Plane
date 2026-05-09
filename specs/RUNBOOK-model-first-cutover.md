# Runbook — Model-First Reasoner Cutover

**Author:** Claude (L8 Agent Architect)
**Date:** 2026-05-07 (provider note 2026-05-08)
**Owner during soak:** Venkat (modernthinkerbuilds@gmail.com)
**Companion docs:** `specs/MODEL-FIRST-PIVOT.md` (the why), `scripts/cutover-model-first.sh` (the do)

This runbook is the operational complement to the pivot. It tells you what to watch, what "good" looks like, and exactly which knobs to turn when something goes sideways.

**Provider posture (as of 2026-05-08):** Gemini 2.5 Flash is the default reasoner, 2.5 Pro the high-stakes opt-in (auto-promoted on `tier`/`swap`/`tolerate`/`log_weight` intents). Anthropic was removed from the runtime path entirely — `core/anthropic_io.py` is a tombstone that raises ImportError if anyone tries to use it. The fallback ladder is now Gemini → legacy regex (no third tier).

---

## 1. Pre-cutover checklist (do once)

```
[ ] specs/MODEL-FIRST-PIVOT.md read end-to-end (including the 2026-05-08 update note).
[ ] GEMINI_API_KEY in ~/developer/agency/rahat/.env (already there from pre-pivot Gemini classifier work).
[ ] You are NOT in the middle of a workout window (bot will be down ~5s).
[ ] Latest scientist.log clean (no unhandled errors in the last hour).
[ ] (Optional) Remove the now-unused ANTHROPIC_API_KEY from .env — harmless but tidy.
```

To verify the environment quickly:

```sh
cd ~/developer/agency/rahat
python3 -c "from google import genai; print('google-genai imported OK')"
python3 -c "import os; assert os.environ.get('GEMINI_API_KEY'), 'set me'"
```

---

## 2. Cutover (the actual command)

```sh
cd ~/developer/agency/rahat
bash scripts/cutover-model-first.sh
```

The script is idempotent — re-running it is safe. It:

1. Verifies `ANTHROPIC_API_KEY` is set (env or .env).
2. Installs `anthropic` and any other missing deps from `requirements.txt`.
3. Runs all four eval suites — aborts if anything fails.
4. Stops the Scientist (and Miya if managed).
5. Restarts both with the new code path.
6. Prints the cost CLI's first read.

Expected total runtime: ~30 seconds. Most of it is the eval suites.

---

## 3. First-hour validation

After the cutover succeeds, send these test messages from your Telegram client and confirm the replies:

| # | Send | Expected behavior |
|---|------|---|
| 1 | `today` | One-line burn-so-far + day type. Hyderabadi flavor present. |
| 2 | `Replan to get 1016 calories per day` | Reasoner explicitly addresses 1016/day target — feasibility, gap to request, candidate plans. **NOT** a static template. |
| 3 | `When will I reach my target weight, how many cal per week, per active rest day, per workout` | All four parts answered in one reply. Numbers come from `get_weight_timeline` (≈198 lbs, 17 weeks, 2,600 kcal). |
| 4 | `aaj ka workout kya hai` | Today's plan + WOD details if it's a CF day. |
| 5 | `wt: 197.5` | Confirms weight logged + shows updated timeline. |

If any of these fail, **roll back** (§5) and capture the bot reply + ledger trace before debugging.

---

## 4. First-day monitoring

```sh
# Cost telemetry — was anything actually spent?
python3 scripts/llm_cost_report.py --since 24h

# Per-tool latency — any tool >2s?
sqlite3 vault/rahat.db "
  SELECT op, COUNT(*), AVG(latency_ms), MAX(latency_ms)
  FROM decisions
  WHERE actor='scientist' AND op LIKE 'scientist.tool.%'
    AND ts >= datetime('now','-24 hours')
  GROUP BY op ORDER BY MAX(latency_ms) DESC
"

# Hops per message — should average 1.5-2.5; >5 means runaway
sqlite3 vault/rahat.db "
  SELECT trace_id, COUNT(*) hops
  FROM decisions WHERE op LIKE 'scientist.reason.hop.%'
    AND ts >= datetime('now','-24 hours')
  GROUP BY trace_id ORDER BY hops DESC LIMIT 5
"

# Fallback rate — Anthropic→Gemini cascade
sqlite3 vault/rahat.db "
  SELECT op, COUNT(*) FROM decisions
  WHERE op IN ('scientist.reason.gemini_fallback','scientist.reason.legacy')
    AND ts >= datetime('now','-24 hours')
  GROUP BY op
"
```

**Targets after 24h:**

- Cost: < $0.20 (very chatty day) — typical < $0.05.
- Per-tool p95 latency: < 100 ms (they're SQLite reads).
- Hops/msg: 1–3 average, no trace > 6.
- Fallback rate: < 1%. If Gemini-fallback fires often, Anthropic is unstable for you — investigate before delete-legacy day.

---

## 5. Rollback (instant)

If something is wrong and you need the old behavior **right now**:

```sh
# Add the env var to the live process. Two options:

# Option A — for the current session, edit the launchd plist:
plutil -insert EnvironmentVariables.RAHAT_LEGACY_DISPATCH \
  -string 1 ~/Library/LaunchAgents/com.rahat.scientist.plist
bash scripts/scientist.sh restart

# Option B — temporary, just .env:
echo 'RAHAT_LEGACY_DISPATCH=1' >> .env
bash scripts/scientist.sh restart
```

Verify rollback by re-sending the test messages — reply formats will return to legacy regex-handler shapes. Both eval suites continue passing under either flag.

When you're ready to retry the cutover, remove the env var and restart.

---

## 6. Soak window (7 days)

Days 1–7 the legacy code stays in the repo as a safety net. After:

```sh
# A clean week looks like:
#   - cost CLI shows steady accrual, no anomalies
#   - no fallback-to-legacy entries in the last 7d
#   - no user-reported regressions
sqlite3 vault/rahat.db "
  SELECT COUNT(*) FROM decisions
  WHERE op='scientist.reason.legacy'
    AND ts >= datetime('now','-7 days')
"
```

If that count is 0 (or only deliberate test invocations) and you've sent 50+ messages over the week, schedule the legacy delete (§7).

---

## 7. Legacy delete (after 7 clean days)

```sh
# 1. Delete the regex dispatcher and supporting regexes from main.py.
#    Search for "_legacy_route(msg" and walk the call graph.
#    Keep handler functions (handle_*) — tools.py wrappers still need them.
#
# 2. Delete the RAHAT_LEGACY_DISPATCH branch in main.py:route().
#
# 3. Delete eval_suite.py and eval_extended.py? — NO. They still test
#    handler-level contracts the tools depend on. Just remove their
#    `os.environ.setdefault("RAHAT_LEGACY_DISPATCH", "1")` lines because
#    the env flag will no longer exist.
#
# 4. Run all four suites once more. eval_reasoner.py becomes the only
#    reasoner-level suite; eval_suite/eval_extended remain handler-level.
#
# 5. Commit with message:
#       refactor(scientist): retire regex dispatcher
#
#       7-day soak passed cleanly with zero fallbacks. The model-first
#       reasoner is now the only entry point; deleted ~600 lines of
#       regex routing logic.
```

Don't delete `tools.py` helpers' underlying handlers — they're called by both the reasoner *and* the tick-driven nudges. The handler functions stay; the dispatcher above them goes.

---

## 8. Common diagnostics

### "The reasoner is hallucinating numbers again."
The contract says it can't. Either:
- A tool is missing — what fact is the model asserting that it had no way to read? Add a tool.
- The system prompt was edited and cache invalidated to a leaky version. `git diff agents/the_scientist/coach_system.py` and revert if needed.

### "Latency feels worse than the regex did."
Check `scientist.reason.hop.0` p95 in the ledger. >1.5s is Anthropic acting up. Switch to Sonnet on a known cold endpoint? — counterintuitive but Anthropic occasionally has Haiku-specific brownouts. Or set `RAHAT_REASONER_MODEL=claude-sonnet-4-6` temporarily.

### "Cost is higher than projected."
Look at hops per trace (§4 query). If >3 average, the model is over-tooling. Likely cause: a vague tool description that the model interprets as "always call me." Tighten the description in `tools.py:SCHEMAS`.

### "Voice drifted to plain English."
`RAHAT_VOICE` is set to `neutral` somewhere — check `.env`, the launchd plist, and the shell that started the process. Default is `hyderabadi`.

### "User-reported reply makes no sense."
Pull the trace:
```sh
sqlite3 vault/rahat.db -header "
  SELECT decision_id, op, input_json, output_json, latency_ms, error
  FROM decisions
  WHERE trace_id = (
    SELECT trace_id FROM decisions
    WHERE actor='scientist' AND op='scientist.reason'
    ORDER BY decision_id DESC LIMIT 1
  )
  ORDER BY decision_id ASC
"
```
This walks every tool call the reasoner made for the most recent message. The mismatch will be visible.

---

## 9. Glossary (for future-you)

- **Reasoner** — `agents/the_scientist/reasoner.py`. The tool-using loop.
- **Tools** — `agents/the_scientist/tools.py`. Wrappers around legacy helpers.
- **Charter** — `core/charter.py`. Policy chokepoint, gates write tools.
- **Decisions ledger** — `decisions` table in `vault/rahat.db`. Every span lands here.
- **Hop** — one round-trip to the model. Most messages are 1–3 hops.
- **Fallback ladder** — Anthropic → Gemini → legacy regex. Each tier is a fail-open.
- **Cache write / read** — Anthropic prompt caching. 5-min TTL by default; the static system blocks land here.

---

## 10. Acceptance criteria (cutover is "done")

```
[x] All 4 eval suites green: 360/360 cases.
[ ] Cutover script ran without an abort.
[ ] At least one of the §3 test messages produced an objectively-better reply
    than the legacy path would have (e.g. test #2 "Replan to 1016/day").
[ ] First-hour `llm_cost_report.py --since 1h` shows non-zero cost lines.
[ ] No errors in `vault/scientist.log` since the restart.
[ ] Rollback verified: env-flag flip + restart returns to legacy behavior.
```

The first one is already true — the rest you fill in as you go.
