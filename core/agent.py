"""core.agent — the Agent contract.

Every agent in Rahat subclasses `Agent`. The contract is deliberately
small (four pieces) so adding the 20th agent costs the same as the 2nd:

    name      — string identifier; used for routing, logs, governance
    triggers  — list of regex patterns this agent claims as cheap-routing
                shortcuts. The Miya orchestrator walks these first.
    route()   — given a user message, return a Reply or None ("not mine").
    tick()    — called once per minute by the scheduler. Used for nudges,
                recalibration, weekly resets. Default: no-op.

Routing semantics (see core/miya.py):
    1. Miya tries regex triggers across all agents. If exactly one fires,
       message is dispatched to that agent.
    2. If zero or multiple fire, Miya asks Gemini Flash to classify which
       agent's `description` best matches the message.
    3. The winning agent's `route()` is called. If it returns None, Miya
       falls back to LLM coaching with the agent as context.

A Reply has three pieces:
    text         — what the user sees
    confidence   — 0.0..1.0; used by Miya to break ties when multiple
                   agents return non-None
    work_orders  — optional list of dicts to enqueue for downstream
                   action (e.g. "schedule a nudge at 9pm tomorrow")

The Reply tuple is intentionally small. Agents that need richer return
shape can subclass Reply — but for the Now phase, this is enough.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Reply:
    text: str
    confidence: float = 1.0           # default: high — agent claimed this msg
    work_orders: list[dict] = field(default_factory=list)


class Agent:
    """Base class for all Rahat agents.

    Subclasses must set `name` and `description`, may set `triggers`,
    must implement `route()`, may implement `tick()`.
    """

    # ─── declarative metadata ───
    name: str = "unnamed"
    description: str = ""              # used by Miya's LLM classifier
    version: str = "0.1.0"
    # Legacy / brand-equivalent names. Miya's classifier accepts any of
    # these as a match for this agent — used during rebrands to keep
    # back-compat without forcing every caller to learn the new name.
    # Example: KobeAgent declares aliases=["the_scientist"] for one week
    # after the 2026-05-12 rebrand.
    aliases: list[str] = []
    # Regex strings — Miya compiles them once. Use word-boundary anchors
    # to avoid false fires on substrings.
    triggers: list[str] = []

    def __init__(self) -> None:
        self._compiled_triggers: list[re.Pattern] = [
            re.compile(p, re.I) for p in self.triggers
        ]

    # ─── routing surface ───
    def matches(self, msg: str) -> bool:
        """True if any trigger fires on the message. Cheap pre-filter
        used by Miya before calling route()."""
        return any(p.search(msg) for p in self._compiled_triggers)

    def route(
        self,
        msg: str,
        *,
        chat_id: str | int | None = None,
        db_path: str | None = None,
    ) -> Reply | None:
        """Handle a user message. Return a Reply or None (not mine).

        Subclasses must override. The default raises so that an agent
        that forgets to implement routing fails loudly in tests.

        `chat_id` and `db_path` are keyword-only and optional so that
        the ABI is backward compatible: legacy callers that pass only
        `msg` continue to work. Agents that maintain per-conversation
        state (e.g. Fraser's chat memory) read `chat_id`; agents that
        don't simply ignore it. Threading it here — rather than reaching
        into a global — keeps each route() call self-describing and lets
        Miya hand the same conversation context to every specialist.
        """
        raise NotImplementedError(
            f"{self.name}: route() not implemented")

    # ─── scheduler surface ───
    def tick(self, now: datetime | None = None) -> list[Reply]:
        """Called once per minute. Return zero or more Replies for Miya
        to send to the user. Default: no-op.

        Replies returned here usually have low confidence and are unsolicited
        (morning briefing, recovery nudge). Miya consults the Charter before
        forwarding each one — quiet hours, notification budget, etc.
        """
        return []

    # ─── lifecycle hooks (optional) ───
    def on_start(self) -> None:
        """Called once when the agent host boots. Use for one-time DB
        migrations, table seeds, etc."""
        return None

    def on_stop(self) -> None:
        """Called on graceful shutdown."""
        return None

    # ─── introspection ───
    def manifest(self) -> dict[str, Any]:
        """Return a dict description suitable for the Miya classifier
        and `rahat agents` CLI output."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "triggers": list(self.triggers),
            "aliases": list(self.aliases),
        }
