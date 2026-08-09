# Genie Bot — standalone Telegram runner (2026-08-10)

Genie as its own Telegram persona, separate from Bade Miya, with
household-scoped access for both adults. Engineering runbook — no
personal data in this file (tokens, chat ids, location live in `.env`
/ `vault/`, both gitignored).

## What it is

A second long-poll bot process (`new_plane/genie_runner/`,
launchd label `com.rahat.genie`) that routes every message through
`agents.genie.handler`'s deterministic pipeline. It reuses
`miya_runner.telegram.TelegramClient` (token-parameterized) and shares
the same vault state as Genie-via-Miya, so a plan made in either
surface is the same plan.

Governance parity with the Miya plane:

* every turn runs in a `decisions.span` (actor `genie`, op
  `genie_bot.turn`) — replayable;
* every state write stays charter-gated in `agents/genie/state.py`;
* allowlist changes are themselves charter-gated
  (`genie.household.chat_add` / `chat_remove`) — access to family data
  is the most consequential grant Genie makes;
* never-empty guard on every reply.

## Household access model (PRD §6.5 — bounded, NOT multi-tenant)

One household: **two adult chats** (`primary`, `spouse`) and optionally
**one shared group chat**. The cap is enforced in
`state.add_household_chat` — the bot cannot quietly become multi-tenant.

* The owner's chat (env `GENIE_PRIMARY_CHAT`) is auto-enrolled as
  `primary` on first contact.
* Anyone else must present the pair code: `/join <GENIE_PAIR_CODE>`
  (→ next free adult slot) or `/join <code> group` (→ the group slot).
* Non-household chats get one polite refusal line and never touch
  family data. Failed joins are logged.
* `/household` lists members; primary can `/household remove <chat_id>`.

## Commands (both surfaces)

| Command | What |
|---|---|
| `/weekend_plan [high\|medium\|low]` | sequenced live plan (J1), energy override |
| `/whatson` or "what's on this weekend" | J5 raw de-duplicated list, scope stated |
| `swap in <name>` / `/swap <name>` | swap a listed alternate into the saved plan (J1 step 5); displaced item returns to the pool |
| `/family_log <role>: <note>` | charter-gated household log (J6 surface) |
| `/household` | membership list / remove (bot only) |
| `/start`, `/help`, `/genie` | greeting + command help |

## Install (owner, ~3 min)

1. Telegram → `@BotFather` → `/newbot` → copy token.
2. `.env`: set `GENIE_TELEGRAM_TOKEN`, `GENIE_PRIMARY_CHAT`,
   `GENIE_PAIR_CODE` (placeholders are already there).
3. `bash scripts/install_genie_bot.sh` — preflights the env, renders
   `com.rahat.genie.plist`, loads it, verifies.
4. Message the bot `/start`; wife sends `/join <code>`.

Operate: `tail -f vault/genie_bot.log`; reload after new commits with
`launchctl kickstart -k gui/$UID/com.rahat.genie`; uninstall with
`launchctl unload ~/Library/LaunchAgents/com.rahat.genie.plist`.

## Explicitly not built yet (PRD honesty legend)

* per-member advocates / conflict ledger — **[NEW]**
* OPTW solver day-sequencing — **[NEW]** (deterministic caps + nap
  protection stand in)
* outcome-conditioned memory, satisfaction tracking — **[BET]**
* proactive Wednesday nudge — deferred (PRD §6.6: earn proactivity;
  propose-never-auto-act)
* day-of re-plan (J4), childcare guard (J2), calendar write-back
* speaker attribution on family-log entries (who logged it) — backlog
* private-vs-shared thread reconciliation (§8 open decision)
