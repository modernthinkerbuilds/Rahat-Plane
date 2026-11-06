"""new_plane.miya_runner — Python service that runs new Miya on the parallel plane.

Architecture (per parallel-planes thesis):
- Owns a separate Telegram bot token (NEW_MIYA_BOT_TOKEN).
- Calls Kobe + Fraser through `native_client` (direct Python imports, the
  default since ADR-013) or, with NEW_MIYA_USE_HTTP_CLIENT=1, through the
  HTTP adapter at RAHAT_ADAPTER_URL.
- Synthesizes responses via Gemini with a cost router (Flash default, Pro
  on hard prompts).
- Publishes signals to the new-plane signal store.
- Charter check before send (read-only — reuses old-plane charter).

The orchestration logic + arbitration rules are intentionally identical
to new_plane.miya_sim.orchestrator, so the simulator and the runner
cannot drift.
"""
