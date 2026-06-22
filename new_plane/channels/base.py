"""Channel Protocol — the transport-agnostic gateway seam (ADR-016, Seam 1).

ADDITIVE-ONLY / UNUSED. This module defines the *shape* a channel adapter
must satisfy. It is imported by NOTHING in the runtime today. Telegram I/O
still flows through ``core/io.py`` (``send`` / ``telegram_get_updates``)
and ``new_plane/miya_runner/telegram.py`` (``TelegramClient``) unchanged.

WHY THIS EXISTS
---------------
PM thesis §4 rule #2: "Channel-abstract the gateway. Telegram is one
adapter; the runtime treats channels as plugins." Today Telegram is
hard-wired:

  - core/io.py:87        ``send(...)`` posts directly to api.telegram.org
  - core/io.py:111       ``telegram_get_updates(...)`` long-polls Telegram
  - new_plane/miya_runner/telegram.py:43  ``TelegramClient`` (self-contained)

A second surface (Slack, email, in-app SDK, OpenClaw ``Channel``) cannot
be added without copying that wiring. This Protocol is the seam: the
runtime would depend on ``Channel`` (this file), and ``TelegramClient``
would become the *first* concrete implementation — without changing the
Telegram wire output.

CONTRACT (the three verbs every transport shares)
-------------------------------------------------
  poll(...)   → pull inbound messages (long-poll, webhook drain, IMAP idle)
  send(...)   → deliver an outbound message to a destination
  format(...) → adapt rich text to the transport's constraints
                (Telegram Markdown + 4096-char split; Slack mrkdwn;
                 email HTML; plain text). Today this lives implicitly in
                 ``telegram.py:_split_for_telegram`` + the Markdown retry.

These are ``Protocol`` (PEP 544) types — structural, so ``TelegramClient``
can satisfy ``Channel`` without inheriting from it. That keeps the seam
zero-cost: the existing class is unchanged; an adapter only needs the
right method shapes.

DO NOT WIRE THIS. The wiring plan is in ADR-016 §"Seam 1 wiring".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ─── Transport-neutral message shapes ──────────────────────────────────────
@dataclass(frozen=True)
class InboundMessage:
    """One inbound message, normalized across transports.

    Mirrors ``new_plane/miya_runner/telegram.TelegramUpdate`` but drops
    Telegram-specific names so a Slack / email adapter produces the same
    shape. ``channel`` names the adapter ("telegram", "slack", ...).
    ``conversation_id`` is the transport's reply address (Telegram chat_id,
    Slack channel id, email thread id). ``subject_id`` is OPTIONAL and ties
    into ADR-016 Seam 2 (Subject abstraction) — None preserves today's
    single-subject behavior.
    """
    channel: str
    conversation_id: str
    text: str
    update_id: int = 0
    subject_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundResult:
    """Result of a ``send``. ``ok`` is the only field every transport can
    promise; ``raw`` carries the transport's native response for callers
    that want it (Telegram API JSON, Slack ts, SMTP id)."""
    ok: bool
    raw: dict[str, Any] = field(default_factory=dict)


# ─── The Channel Protocol ──────────────────────────────────────────────────
@runtime_checkable
class Channel(Protocol):
    """The seam every transport adapter satisfies.

    Structural (PEP 544): a class is a ``Channel`` if it has ``poll``,
    ``send``, and ``format`` with compatible signatures — no inheritance
    required. ``TelegramClient`` (new_plane/miya_runner/telegram.py) is the
    intended *first* implementation; its ``get_updates`` / ``send_message``
    / ``_split_for_telegram`` map onto these three verbs.

    Identifies itself via ``name`` so the dispatcher / orchestrator can log
    which transport a decision came through (ties into the portable-audit
    rule, thesis §4 #5).
    """

    name: str

    def poll(self, *, offset: int = 0, timeout_s: int = 10) -> list[InboundMessage]:
        """Pull pending inbound messages. Long-poll, webhook drain, or
        mailbox read depending on transport. Returns [] when nothing is
        waiting. Must not raise on transient transport errors."""
        ...

    def send(self, conversation_id: str, text: str, *,
             parse_mode: str | None = None) -> OutboundResult:
        """Deliver ``text`` to ``conversation_id``. Implementations apply
        ``format`` internally (chunking, escaping) as needed."""
        ...

    def format(self, text: str) -> list[str]:
        """Adapt ``text`` to the transport's constraints and return the
        ordered chunks to send. Telegram: paragraph/line/hard split at
        ~4000 chars. Plain transports: ``[text]``. Pure — no I/O."""
        ...


__all__ = ["Channel", "InboundMessage", "OutboundResult"]
