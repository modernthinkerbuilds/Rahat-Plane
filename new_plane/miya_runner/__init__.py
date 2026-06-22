"""new_plane.miya_runner — Python service that runs new Miya on the parallel plane.

Architecture (per parallel-planes thesis):
- Owns a separate Telegram bot token (NEW_MIYA_BOT_TOKEN).
- Calls Kobe + Fraser via the HTTP adapter at OPENCLAW_ADAPTER_URL
  (not direct Python imports — that's what enforces the plane boundary).
- Synthesizes responses via Gemini with a cost router (Flash default, Pro
  on hard prompts).
- Publishes signals to the new-plane signal store.
- Charter check before send (read-only — reuses old-plane charter).

This is Stage 1 of the new plane. Stage 2 swaps this runner for the
OpenClaw TS plugin once that integration is done. The orchestration
logic + arbitration rules are intentionally identical to
new_plane.miya_sim.orchestrator so Stage 2 is a swap, not a rewrite.
"""
