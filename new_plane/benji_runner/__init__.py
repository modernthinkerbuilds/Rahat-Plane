"""benji_runner — Benji's out-of-process surface: scheduled ingest,
digest email delivery (S1), IMAP inbound loop (S3).

Runs from launchd (com.rahat.benji.*), never cron. Email is the only
channel by co-owner decision (PRD §2) — no Telegram route, which is why
S1 touches zero shared orchestrator files.
"""
from __future__ import annotations
