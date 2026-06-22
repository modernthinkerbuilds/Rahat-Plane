"""new_plane.channels — transport-agnostic channel seam (ADR-016, Seam 1).

ADDITIVE-ONLY. Nothing in the runtime imports this package yet. It exists
so the *second* surface (Slack / email / in-app SDK / OpenClaw Channel)
can be added as a new adapter instead of forking the Telegram-hardwired
send/poll path in ``core/io.py`` and ``new_plane/miya_runner/telegram.py``.

Thesis rule it serves: §4 rule #2 — "Channel-abstract the gateway.
Telegram is one adapter; the runtime treats channels as plugins."

See ``specs/ADR-016_platform_seams.md`` for the wiring plan (deliberately
NOT wired in this change).
"""
from __future__ import annotations

from new_plane.channels.base import Channel, InboundMessage, OutboundResult

__all__ = ["Channel", "InboundMessage", "OutboundResult"]
