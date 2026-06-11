# Wake-up playbook — new Miya v2 on the parallel plane

**Built overnight 2026-05-31 while you slept.** Status:
**all 114 new-plane tests green, runner ready, launchd template ready.**

This is the 15-minute sequence to get new Miya v2 actually talking to
you on a separate Telegram bot, calling your real Kobe + Fraser via the
adapter, synthesizing with Gemini.

> **TL;DR**: Phases A–E below. Phase D is where you actually message
> the bot. Phase F is optional — installs new Miya as a permanent
> launchd service so it survives reboots.

---

## What got built overnight

Everything is in `new_plane/miya_runner/` (the runtime) and
`new_plane/compare/` (the side-by-side harness). Strict parallel
planes — zero edits to `agents/`, `core/`, or any old-plane code.

```
new_plane/
├── miya_runner/
│   ├── __init__.py
│   ├── __main__.py          ← entry point (serve | once | health)
│   ├── adapter_client.py    ← Python HTTP client → Kobe/Fraser via adapter
│   ├── telegram.py          ← long-poll loop, sendMessage, splitter
│   ├── orchestrator.py      ← intent → tools → arbitrate → synth → signal
│   ├── synthesizer.py       ← Gemini call, falls back if no key
│   └── cost_router.py       ← Flash by default, Pro on hard prompts
└── compare/
    ├── __init__.py
    ├── __main__.py          ← CLI: python -m new_plane.compare
    └── harness.py           ← side-by-side runner + markdown report

tests/new_plane/
├── test_runner_adapter_client.py   (18 tests)
├── test_runner_telegram.py         (15 tests)
├── test_runner_cost_router.py      (12 tests)
├── test_runner_synthesizer.py      (14 tests)
├── test_runner_orchestrator.py     (9 tests)
└── test_compare_harness.py         (6 tests)

scripts/
├── com.rahat.miya.v2.plist.template   ← launchd manifest
└── install_new_miya.sh                ← one-shot installer
```

**Test count:** 47 (Stage 0) + 74 (overnight) = **121 new-plane tests**;
old-plane suite still green (untouched).

---

## Phase A — Bring the tree up to date (1 min)

The overnight work is on `feat/new-plane-stage0` in your working tree
**but not committed** (the sandbox can't write `.git/index.lock`).

```bash
cd ~/developer/agency/rahat
source .venv/bin/activate   # if you've added the auto-source alias, skip this
git status -sb              # should show many untracked under new_plane/, tests/new_plane/, scripts/

# Verify tests pass on YOUR Mac (sandbox already proved green)
RAHAT_TEST_MODE=1 python -m pytest tests/new_plane/ -q
# Expect: 121 passed
```

If `121 passed`, you're aligned with what I shipped. If anything fails,
paste it and we triage before committing.

---

## Phase B — Boot the adapter (1 min)

The runner needs the adapter on port 8766 (8765 is SugarWOD bridge).
Same script you used Friday night.

```bash
cd ~/developer/agency/rahat
./scripts/weekend_smoke.sh
```

**Expect:** trailing `Stage 0 GREEN. Foundation works.` This also leaves
the adapter running in the background. Hit Ctrl+C if you want to stop
it — but **leave it running** through Phase D.

If you want it permanently always-on as its own launchd job, do that
later (separate task; not required for today).

---

## Phase C — Confirm the new bot token is configured (30 sec)

You should have done BotFather before sleeping. Verify:

```bash
grep -E '^(NEW_MIYA_BOT_TOKEN|GEMINI_API_KEY|OPENCLAW_ADAPTER)' .env
```

You should see at minimum:
```
NEW_MIYA_BOT_TOKEN=<your-new-bot-token>
GEMINI_API_KEY=<your-existing-key>
OPENCLAW_ADAPTER_URL=http://127.0.0.1:8766
OPENCLAW_ADAPTER_TOKEN=dev-token-replace-me
```

If `NEW_MIYA_BOT_TOKEN` is missing: do BotFather now (60 seconds).
1. Telegram → @BotFather → `/newbot`
2. Name: `Rahat new Miya v2`
3. Username: `rahat_new_miya_v2_bot` (must end in `bot`)
4. Copy the token
5. `echo "NEW_MIYA_BOT_TOKEN=<paste>" >> .env`

Also optional but recommended — restrict to your chat:
```bash
grep ^TELEGRAM_CHAT_ID .env
# Copy that value:
echo "NEW_MIYA_CHAT_ID=<paste-same-chat-id>" >> .env
```

(Same chat_id as the live bot — it's *you* either way.)

---

## Phase D — First real Telegram round-trip (2 min)

Two terminals:

**Terminal 1** — keep the adapter running:
```bash
cd ~/developer/agency/rahat
source .venv/bin/activate
set -a; source .env; set +a
./.venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app \
    --host 127.0.0.1 --port 8766 --log-level info
```

**Terminal 2** — boot the runner:
```bash
cd ~/developer/agency/rahat
source .venv/bin/activate
set -a; source .env; set +a
python -m new_plane.miya_runner
```

**Expected log line on boot:**
```
new Miya v2 live | adapter=http://127.0.0.1:8766 | chat_filter=<your-id> |
                  flash=gemini-2.5-flash | pro=gemini-2.5-pro
```

**Now open Telegram → your new bot → send:**
```
what's my plan today
```

**Expect:** a synthesis from new Miya, drawing on your real Kobe data
(`active_goal`, `recalibration`) and routed through Gemini Flash.

**Then try arbitration:**
```
where am I on pace
```

If you're behind pace, you should see new Miya being **honest** about
it — that's the arbitration loop firing (`behind_pace` rule) and
escalating to Pro for synthesis.

**Then try design:**
```
design me a workout for tomorrow
```

That hits Fraser via the adapter. You should see "Fraser's draft:" in
the synthesis (or wholly synthesized if Gemini paraphrases — both fine).

### How to know it worked

In **Terminal 2** logs you should see:
```
[in] chat=<id> text='what is my plan today'
[out] trace=<id> model=gemini-2.5-flash tools=['kobe_active_goal', 'kobe_recalibration', 'kobe_charter_check'] arbitration=None
```

And in the signal DB:
```bash
sqlite3 ~/.rahat/new_plane_signals.db \
  "select id, agent, type, ts, trace_id from signals order by id desc limit 5;"
```

You should see fresh `miya_synthesized` rows.

### If it doesn't work

Most common failures:

| symptom | likely cause | fix |
|---|---|---|
| `NEW_MIYA_BOT_TOKEN not set` | env not loaded | `set -a; source .env; set +a` |
| `adapter unreachable` | adapter not running | re-run Terminal 1 |
| `NEW_MIYA_BOT_TOKEN equals SCIENTIST_BOT_TOKEN` | you copied wrong | get a SEPARATE bot from BotFather |
| Bot doesn't reply | chat_id filter blocking | `unset NEW_MIYA_CHAT_ID` or fix the value |
| Reply is `[new_miya]` literal-fallback | `GEMINI_API_KEY` unset | add it to `.env`, re-source |

For one-off smoke without Telegram:
```bash
python -m new_plane.miya_runner once "what's my plan today"
```

That runs one turn from the CLI — bypasses Telegram, useful for debug.

---

## Phase E — Side-by-side capture (3 min)

The 8-week gate evidence starts now. Run the comparison harness:

```bash
mkdir -p private/eval-runs
python -m new_plane.compare \
  "what's my plan today" \
  "where am I on pace" \
  "when will I hit 196" \
  "design me a workout for tomorrow" \
  "should I take Saturday off"
```

This writes a markdown report to `private/eval-runs/compare_<ts>.md`
comparing **old-Miya path** (structured fallback, no synthesizer) vs
**new-Miya path** (Gemini synthesis, cost-routed, arbitration-mediated).
`private/` is gitignored so the evidence stays local.

Open the report in your editor — that's the qualitative data feeding the
8-week stop-or-go decision per `RAHAT_THESIS_2026-05-27.md`.

---

## Phase F — Install as permanent launchd service (5 min, optional)

Once you trust the runner across a session or two, install it as an
always-on service alongside `com.rahat.miya`, `com.rahat.vitals`, and
`com.rahat.sugar.bridge`:

```bash
cd ~/developer/agency/rahat
./scripts/install_new_miya.sh
```

This:
1. Validates env (token set, adapter reachable, tokens distinct, .venv present).
2. Renders `scripts/com.rahat.miya.v2.plist` from the template.
3. Copies to `~/Library/LaunchAgents/`.
4. Loads via `launchctl`.
5. Verifies both `com.rahat.miya` and `com.rahat.miya.v2` are running.

Logs: `vault/miya_v2.log` (separate from live bot's `vault/miya.log`).
Restart after code change: `launchctl kickstart -k gui/$(id -u)/com.rahat.miya.v2`.
Uninstall: `launchctl unload ~/Library/LaunchAgents/com.rahat.miya.v2.plist && rm ~/Library/LaunchAgents/com.rahat.miya.v2.plist`.

**The adapter** also needs to be always-on for new Miya v2 to work
permanently. That's a separate launchd job we can add later — for
today, the manual uvicorn from Phase D is fine.

---

## Phase G — Commit + push (3 min)

After you've verified Phase D worked, commit the overnight work:

```bash
cd ~/developer/agency/rahat
git status -sb   # confirm the new files are visible

git add new_plane/miya_runner/ \
        new_plane/compare/ \
        scripts/com.rahat.miya.v2.plist.template \
        scripts/install_new_miya.sh \
        tests/new_plane/test_runner_*.py \
        tests/new_plane/test_compare_harness.py \
        specs/WAKE_UP_PLAYBOOK.md \
        .gitignore

# Sanity check before committing
git status -sb | head -20

git commit -m "feat(new-plane): Python new Miya runner + comparison harness

new_plane/miya_runner/ — the Stage-1 parallel-plane new Miya:
  - adapter_client.py: Python HTTP client to bridges/openclaw_adapters/
    (mirrors TS adapter_client.ts envelope)
  - telegram.py: long-poll + sendMessage, stdlib-only, no core/io dep
  - orchestrator.py: intent→tools(HTTP)→arbitrate→charter→synth→signal
  - synthesizer.py: Gemini call with structured-fallback for offline
  - cost_router.py: v0 Flash/Pro heuristic, logs JSONL for learner upgrade
  - __main__.py: 'serve' (Telegram loop) | 'once' (CLI) | 'health'

new_plane/compare/ — 8-week gate evidence harness:
  - harness.py: run N prompts through both planes, capture timing,
    tools, arbitration, model picks
  - __main__.py: CLI that emits markdown reports to private/eval-runs/

scripts/com.rahat.miya.v2.plist.template + install_new_miya.sh:
  always-on launchd service (separate label from live bot — both coexist).

74 new tests (12 cost-router + 14 synth + 18 adapter-client + 15 telegram
+ 9 orchestrator + 6 compare) — 121 total new-plane tests green.

Old plane untouched. Requires NEW_MIYA_BOT_TOKEN (distinct from
SCIENTIST_BOT_TOKEN) and the adapter running on port 8766.

Stage 2: swap this runner for the OpenClaw TS plugin once you have
time for the SDK integration work."

git push
```

The pre-push gate runs the full 5-layer suite (~5s) and the new-plane
suite. Should be green.

---

## What we did NOT do

- **No push, no merge, no live-flag flip.** All staged for you.
- **No edits to old-plane code.** Boundary doc respected.
- **No OpenClaw work.** Stage 2 — the Python runner makes the TS
  integration optional, not urgent. When you DO get to it, the
  contract is already validated end-to-end.
- **No `NEW_MIYA_BOT_TOKEN` configured.** That's BotFather + you.
- **No launchd install.** `install_new_miya.sh` is opt-in.

---

## Open items for the next session

1. **Adapter as always-on service.** Currently the uvicorn process is
   manual. Add `com.rahat.openclaw.adapter.plist` so it survives
   reboots. (Or roll into install_new_miya.sh.)
2. **Set an active goal** in your live Kobe so the new-Miya synthesis
   has anchor data (right now `get_active_goal` returns "no-active-goal-
   in-memory", which makes responses lean on recalibration math alone).
3. **Architecture diagram update.** SugarWOD bridge + vitals collector
   + new-Miya v2 are still missing from
   `specs/RAHAT_ARCHITECTURE_2026-05-30.md`. One pass when you have time.
4. **8-week gate scorecard.** I sketched the comparison harness; the
   actual evaluation rubric (cost saved %, arbitration handoff count,
   memory-recall delta) is week-2+ work.
5. **OpenClaw integration** (Stage 2). When you want to swap the
   Python runner for the TS plugin, the integration guide at
   `specs/OPENCLAW_INTEGRATION_GUIDE.md` is the starting point.

---

## Reference cheatsheet

| What | How |
|---|---|
| Boot adapter | `./.venv/bin/python -m uvicorn bridges.openclaw_adapters.server:app --port 8766` |
| Boot new Miya | `python -m new_plane.miya_runner` |
| One-off CLI test | `python -m new_plane.miya_runner once "<msg>"` |
| Health check | `python -m new_plane.miya_runner health` |
| Side-by-side | `python -m new_plane.compare "<prompt>" "<prompt>"` |
| Watch signals | `sqlite3 ~/.rahat/new_plane_signals.db "select * from signals order by id desc limit 10;"` |
| Watch cost log | `tail -f ~/.rahat/cost_router.log \| jq .` |
| Watch runner log | `tail -f ~/.rahat/new_miya_runner.log` |
| Install as service | `./scripts/install_new_miya.sh` |
| Restart service | `launchctl kickstart -k gui/$(id -u)/com.rahat.miya.v2` |

Sleep well. Coffee, BotFather, then Phase A.
