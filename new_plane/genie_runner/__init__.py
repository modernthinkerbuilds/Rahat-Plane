"""new_plane.genie_runner — Genie as its OWN Telegram bot (2026-08-10).

Why a second bot: the owner asked for Genie "as its own person in
Telegram", with his wife having direct access. Bade Miya's runner is
chat-filtered to one owner chat and voiced as the fitness plane; the
household plane gets its own bot identity, its own allowlist (two
adults + optionally one shared group chat — the PRD's §6.5 bounded
multi-user reading), and its own launchd service (com.rahat.genie).

Governance parity: every turn runs inside a decisions span (actor
"genie"), every state write stays charter-gated in agents/genie/state,
allowlist changes are charter-gated access-control events, and the
never-empty guard applies to every reply. Reuses miya_runner's
TelegramClient (token-parameterized) — a bot token is a single-poller
resource, so the two bots never conflict.
"""
