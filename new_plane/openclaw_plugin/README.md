# rahat-new-plane — OpenClaw plugin

New Miya, orchestrating Kobe + Fraser via the Python adapter at
`bridges/openclaw_adapters/`.

## Install

```bash
cd ~/developer/agency/rahat/new_plane/openclaw_plugin
npm install   # or pnpm install if you prefer
npm run build
```

## Wire to OpenClaw

The vendored OpenClaw is in `staging/fleet/`. Follow its plugin docs to
register this package — typically by adding to `extensions/` in the
OpenClaw repo or pointing the runtime's plugin-loader at this directory.

In `src/index.ts` there's a commented pseudo-registration block — adapt
the imports + exports to the exact plugin-SDK shape your OpenClaw
version expects.

## Environment

Add to `~/developer/agency/rahat/.env`:

```
OPENCLAW_ADAPTER_URL=http://127.0.0.1:8765
OPENCLAW_ADAPTER_TOKEN=<long random hex — same value the Python adapter reads>
OPENCLAW_COST_LOG=/Users/you/.rahat/cost_router.log
OPENCLAW_LOG_HTTP=0
NEW_MIYA_BOT_TOKEN=<separate Telegram bot token — DO NOT reuse SCIENTIST_BOT_TOKEN>
```

## What's in here

| Path | Purpose |
|---|---|
| `AGENTS.md`, `TOOLS.md` | Agent + tool manifests for OpenClaw. |
| `src/adapter_client.ts` | HTTP client for the Python adapter. |
| `src/signals.ts` | Cross-agent typed signal publish/read. |
| `src/cost_router.ts` | v0 model-routing + cost-event logging. |
| `src/tools/kobe.ts` | TS face for Kobe HTTP tools. |
| `src/tools/fraser.ts` | TS face for Fraser HTTP tools. |
| `src/agents/miya.ts` | new_miya orchestrator skeleton. |
| `src/agents/miya.system.md` | System prompt (editable without rebuild). |
| `src/index.ts` | Plugin entry; register tools/agent here. |

## What this does NOT do (yet)

- Learned cost router (week 2+).
- Learned arbitration (week 2+).
- Outcome-conditioned memory ranking (week 3+).
- Verifiable compliance receipts (week 4+).
- Subagent spawn (post-8-week gate).

## Smoke test

1. Start the Python adapter (see `bridges/openclaw_adapters/README.md`).
2. Boot OpenClaw with this plugin loaded.
3. Send a Telegram message to the `/v2` bot.
4. Confirm: response synthesized, charter allowed, signal published.
   Check the cost log at `$OPENCLAW_COST_LOG`.
