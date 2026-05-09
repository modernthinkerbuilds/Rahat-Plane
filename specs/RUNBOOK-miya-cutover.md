# Runbook — Miya cutover

The Phase Now refactor is complete. The Scientist still runs through the
legacy `agents/the_scientist/main.py` Telegram poll loop. This runbook
flips that to the new orchestrator (`core/miya_main.py`) — a like-for-like
swap that takes ~5 minutes and is fully reversible.

**Status: ARTIFACT READY. Do not load until you're at the keyboard and
ready to confirm.** Eval suites pass; the new entry point boots cleanly
in offline test. The actual launch is a manual step — done by you, on
your Mac Mini, with eyes on the Telegram bot.

---

## What changes

- **Before:** Telegram → `com.rahat.scientist` (launchd) → `main.py`'s poll loop → `route()` → `send()`.
- **After:** Telegram → `com.rahat.miya` (launchd) → `core/miya.run_loop()` → registered agents (Scientist for now) → Charter → `core.io.send`.

Same bot token. Same chat. Same wire output (proven by the eval suite —
125 cases through the wrapper, 36 through the extended suite). The
difference is one process, one entry point, one decision log, one policy
gate — ready to absorb Coach / Curriculum / Bajrangi / etc. without
touching the launchd config again.

---

## Pre-flight (offline, on your Mac)

```bash
cd ~/developer/agency/rahat
python3 tests/scientist/eval_suite.py     # → 125/125
python3 tests/scientist/eval_via_agent.py # → 125/125
python3 tests/scientist/eval_extended.py  # → 36/36
```

If any of those are red, **stop**. The cutover is not safe.

Optional dry-run with no Telegram traffic — verify Miya boots and
registers the Scientist:

```bash
SCIENTIST_BOT_TOKEN= TELEGRAM_CHAT_ID= python3 core/miya_main.py
# expect:
#   🪶 Miya live | agents=['the_scientist'] | db=…/vault/rahat.db
# Ctrl-C to exit.
```

---

## Cutover (5 minutes)

```bash
# 1. Stop the legacy Scientist daemon.
launchctl unload ~/Library/LaunchAgents/com.rahat.scientist.plist
launchctl list | grep com.rahat        # should show NOTHING

# 2. Install the new Miya daemon.
cp core/com.rahat.miya.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rahat.miya.plist

# 3. Watch the log come up.
tail -f vault/miya.log
# expect:
#   🪶 Miya live | agents=['the_scientist'] | db=…/vault/rahat.db
```

Then send one message to the bot from Telegram (e.g. `today`) and confirm
you get a sensible reply. Confirm `vault/miya.log` shows the trace.

---

## Verification

```bash
# Decision log is being written
sqlite3 vault/rahat.db \
  "SELECT actor, op, outcome FROM decisions ORDER BY ts DESC LIMIT 10;"

# Charter is being consulted on tick-emitted nudges
sqlite3 vault/rahat.db \
  "SELECT actor, subject, decision FROM governance_log ORDER BY ts DESC LIMIT 10;"
```

Both should have rows after the first message + first minute-tick.

---

## Rollback (60 seconds)

If anything is wrong:

```bash
launchctl unload ~/Library/LaunchAgents/com.rahat.miya.plist
launchctl load ~/Library/LaunchAgents/com.rahat.scientist.plist
tail -f vault/scientist.log
```

The Scientist plist still points at `main.py`, which still has its full
Telegram poll loop. The wrapper layer added in Phase Now is additive —
removing Miya restores the pre-cutover behavior exactly.

---

## What's NOT changing in this cutover

- Bot token, chat id, .env layout
- `vault/rahat.db` schema (Miya only ADDS to it — `decisions`,
  `episodes`, `episode_notes`)
- The Apple Watch ingest endpoint (`skills/vitals_listener.py`)
- The SugarWOD bridge (`bridges/sugarwod/server.py`)
- Caffeinate / KeepAlive / ThrottleInterval semantics

---

## When to run this

You said it's OK to take the Scientist down for a few hours. The
cutover itself is ~5 minutes; the safe window is "I'm at the keyboard
and the next ~30 minutes is mine." Don't do it on a Friday night, don't
do it right before a workout, and don't do it 30 minutes before a
weigh-in.

If anything ambiguous shows up in `vault/miya.log` after the swap, just
roll back. The state of the world is preserved either way — both
processes write to the same SQLite file.
