# Rahat — new-plane agents

This file declares the agents the OpenClaw runtime should load from this
plugin. Per the OpenClaw convention, this is the per-agent config the
runtime reads on boot.

## new_miya

- **Role:** orchestrator — single voice the user talks to.
- **Provider:** Gemini (Flash for routine; Pro for synthesis when arbitration is needed).
- **System prompt:** see `src/agents/miya.system.md`.
- **Tools:** see `TOOLS.md` — uses Kobe + Fraser tools via HTTP.
- **Autonomy budget:** ≤ 3 tool calls per user message; ≤ 1 Pro call per message.
- **Memory:** outcome-conditioned (v0: simple recency + tagged provenance).
  ContextEngine plugin pulls from the Python adapter's `/signals/recent`.
- **Subagents:** none in v0 — orchestrator is flat. Subagent spawn deferred to
  post-8-week gate.
- **Charter:** every outbound message goes through `/kobe/charter_check`
  before send. If vetoed, drop and log.

## Boundaries

- new_miya does NOT replace old miya. Old miya stays on the legacy Telegram
  bot (`SCIENTIST_BOT_TOKEN`); new_miya uses a separate token
  (`NEW_MIYA_BOT_TOKEN`) so production stays green.
- new_miya does NOT directly mutate any state. Kobe/Fraser are read-only
  through their adapter endpoints; writes happen on the old plane.
- new_miya publishes outcomes through `/signals/publish` after every turn —
  this is the load-bearing primitive per the PM thesis v1.1.
